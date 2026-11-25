from rest_framework import viewsets, status, permissions
from rest_framework.decorators import action
from rest_framework.response import Response
from django.contrib.auth import authenticate
from django.db import transaction as db_transaction, IntegrityError
from django.utils import timezone
from decimal import Decimal, InvalidOperation
import uuid

from .models import Sale, SaleLine, Payment
from .serializers import SaleSerializer, SaleLineSerializer, PaymentSerializer
from .services import TransactionService, CartService, CartError, CartItemNotFoundError, InvalidQuantityError, SaleNotModifiableError, TransactionError, InsufficientPaymentError, InvalidPaymentMethodError
from .payments import PaymentMethod, get_processor
from products.models import Product
from customers.models import Customer
from accounts.utils import is_manager


class PaymentProcessingRejected(Exception):
    """Raised when a payment processor refuses a tender (HTTP 400-equivalent)."""


class SaleViewSet(viewsets.ModelViewSet):
    queryset = Sale.objects.all()
    serializer_class = SaleSerializer
    permission_classes = [permissions.IsAuthenticated]
    
    # Transactions are instantiated per-request (see checkout/void handlers).
    # Do NOT instantiate a TransactionService at import time: its constructor
    # reads BusinessInfo from the DB, which breaks system checks and `migrate`
    # whenever the schema is out of sync or the database is still empty.

    def perform_create(self, serializer):
        # Attribute the sale to the open session for the cashier's register/
        # business day (see sessions.models.resolve_open_session). Could be
        # None when no session is open — server-side session enforcement is
        # absent on this line, and a NULL session falls back to the legacy
        # close/summary time-window rule. Never attach to a closed or
        # sibling-register session.
        from django.utils import timezone
        from sessions.models import resolve_open_session
        session = resolve_open_session(self.request.user, timezone.now())
        serializer.save(cashier=self.request.user, session=session)

    def _reassign_guard(self, request, sale):
        """Reassign (PATCH/PUT `cashier`) is manager/admin-only and open-sale-only.

        Returns an error Response to short-circuit a request, or None to
        allow it through. Kept in the view rather than the serializer so the
        authorization failure returns an HTTP 403 instead of a field-level
        400; target role/active validation stays in the serializer.
        """
        if 'cashier' not in request.data:
            return None
        if not is_manager(request.user):
            return Response(
                {'detail': 'Only managers and admins can reassign a sale.'},
                status=status.HTTP_403_FORBIDDEN,
            )
        if not sale.is_open():
            return Response(
                {'detail': 'Only open sales can be reassigned.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return None

    def update(self, request, *args, **kwargs):
        """Update a sale; gate the `cashier` write behind manager authority."""
        instance = self.get_object()
        guard = self._reassign_guard(request, instance)
        if guard is not None:
            return guard
        partial = kwargs.pop('partial', False)
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)
        if getattr(instance, '_prefetched_objects_cache', None):
            instance._prefetched_objects_cache = {}
        return Response(serializer.data)

    def partial_update(self, request, *args, **kwargs):
        kwargs['partial'] = True
        return self.update(request, *args, **kwargs)

    @action(detail=True, methods=['post'])
    def add_line(self, request, pk=None):
        sale = self.get_object()
        if not sale.is_open():
            return Response({'detail': 'sale not open'}, status=400)
        try:
            qty = int(request.data.get('quantity', 1))
        except (TypeError, ValueError):
            return Response({'detail': 'invalid quantity'}, status=400)
        if qty < 1:
            return Response({'detail': 'quantity must be positive'}, status=400)
        try:
            product = Product.objects.get(pk=request.data.get('product'))
        except (Product.DoesNotExist, TypeError, ValueError):
            return Response({'detail': 'product not found'}, status=400)
        
        # Use CartService for adding item
        try:
            cart = CartService(sale)
            line = cart.add_item(product, qty)
        except (InvalidQuantityError, SaleNotModifiableError) as e:
            return Response({'detail': str(e)}, status=400)
        
        return Response(SaleLineSerializer(line).data, status=201)

    @action(detail=True, methods=['patch', 'delete'], url_path='lines/(?P<line_id>[^/.]+)')
    def line_detail(self, request, pk=None, line_id=None):
        sale = self.get_object()
        if not sale.is_open():
            return Response({'detail': 'sale not open'}, status=400)
        try:
            line = SaleLine.objects.get(pk=line_id, sale=sale)
        except (SaleLine.DoesNotExist, TypeError, ValueError):
            return Response({'detail': 'line not found'}, status=404)
        
        cart = CartService(sale)
        
        if request.method == 'DELETE':
            try:
                cart.remove_item(line_id)
            except CartItemNotFoundError:
                return Response({'detail': 'line not found'}, status=404)
            except SaleNotModifiableError as e:
                return Response({'detail': str(e)}, status=400)
            return Response(status=204)
        
        if 'quantity' in request.data:
            try:
                qty = int(request.data['quantity'])
            except (TypeError, ValueError):
                return Response({'detail': 'invalid quantity'}, status=400)
            if qty < 1:
                return Response({'detail': 'quantity must be positive'}, status=400)
            try:
                cart.update_item_quantity(line_id, qty)
            except (CartItemNotFoundError, InvalidQuantityError, SaleNotModifiableError) as e:
                return Response({'detail': str(e)}, status=400)
        
        line.refresh_from_db()
        return Response(SaleLineSerializer(line).data)

    @action(detail=True, methods=['post'], url_path='lines/(?P<line_id>[^/.]+)/discount')
    def apply_discount(self, request, pk=None, line_id=None):
        sale = self.get_object()
        if not sale.is_open():
            return Response({'detail': 'sale not open'}, status=400)
        try:
            line = SaleLine.objects.get(pk=line_id, sale=sale)
        except (SaleLine.DoesNotExist, TypeError, ValueError):
            return Response({'detail': 'line not found'}, status=404)
        
        # manager gating
        manager_username = request.data.get('manager_username')
        manager_password = request.data.get('manager_password')
        user = None
        if manager_username and manager_password:
            cand = authenticate(username=manager_username, password=manager_password)
            if cand and is_manager(cand):
                user = cand
        if user is None and is_manager(request.user):
            user = request.user
        if user is None:
            return Response({'detail': 'manager required'}, status=403)
        
        dtype = request.data.get('discount_type', 'none')
        if dtype not in dict(SaleLine.DISCOUNT_CHOICES):
            return Response({'detail': 'invalid discount type'}, status=400)
        try:
            value = Decimal(str(request.data.get('discount_value', 0)))
        except (InvalidOperation, TypeError, ValueError):
            return Response({'detail': 'invalid discount value'}, status=400)
        if value < 0:
            return Response({'detail': 'discount cannot be negative'}, status=400)
        
        # Use CartService for applying discount
        try:
            cart = CartService(sale)
            cart.apply_discount(line_id, dtype, value, user)
        except (CartItemNotFoundError, ValueError, SaleNotModifiableError) as e:
            return Response({'detail': str(e)}, status=400)
        
        line.refresh_from_db()
        return Response(SaleLineSerializer(line).data)

    def _idempotency_key(self, request):
        """Resolve a stable idempotency key for a tender.

        Idempotency is client-driven: only a client-supplied `idempotency_key`
        participates in dedupe (a retried POST with the same key replays the
        existing row). A keyless tender is deliberately NON-idempotent: it gets
        a fresh random key per POST, so every POST records a new row and two
        genuine equal-amount tenders never collapse into one (B1). No
        server-side key is derived from the payload — derived keys were
        guessable and enabled cross-sale squatting (B2) and equal-tender
        collisions.

        Returns ``(effective_key, client_key)``: ``effective_key`` is always a
        valid 64-char key (client's or a fresh UUID); ``client_key`` is the
        client-supplied key or ``None`` for a keyless tender (only client
        keys participate in dedupe and cross-sale rejection).
        """
        supplied = request.data.get('idempotency_key')
        if supplied is None or str(supplied).strip() == '':
            return str(uuid.uuid4()), None
        key = str(supplied)
        if len(key) > 64:
            raise PaymentProcessingRejected('idempotency_key too long (max 64 characters)')
        return key, key

    def _record_payments(self, sale, method, amount, tendered, manager_approved, customer, splits, idempotency_key):
        """Create Payment row(s) through the payments.py processor framework.

        All writes happen inside a transaction.atomic() block (the caller
        owns the block); the idempotency `select_for_update` lookup has already
        passed. Returns a list of created Payment objects. Raises
        PaymentProcessingRejected on processor refusal, which the caller maps
        to an HTTP 400 (nothing is written on that path).
        """
        method_enum = PaymentMethod(method)

        # Split: one top-level Payment row per component method, so the cash
        # component is an independent method='cash' row that cash-up's
        # method='cash' aggregation can see and total. require_token=False so
        # a POS cash+card split records without a gateway token (B4).
        if method == Payment.METHOD_SPLIT:
            processor = get_processor(PaymentMethod.SPLIT, require_token=False)
            result = processor.process_payment(amount, PaymentMethod.SPLIT, {'splits': splits})
            if not result.success:
                raise PaymentProcessingRejected(result.error_message)
            rows = []
            for idx, comp in enumerate(result.metadata['splits']):
                comp_tendered = comp.get('tendered')
                if comp_tendered is not None:
                    try:
                        comp_tendered = Decimal(str(comp_tendered))
                    except InvalidOperation:
                        comp_tendered = None
                rows.append(Payment.objects.create(
                    sale=sale,
                    method=comp['method'],
                    amount=Decimal(comp['amount']),
                    tendered=comp_tendered,
                    change_due=Decimal(comp.get('change_due') or 0),
                    manager_approved=manager_approved,
                    idempotency_key=f"{idempotency_key}:{comp['method']}:{idx}",
                    source=Payment.SOURCE_UI,
                    transaction_id=comp.get('transaction_id'),
                    correlation_id=idempotency_key,
                ))
            return rows

        # Single-method tender. Card/mobile are recorded without a gateway
        # token (the real gateway hook stays in the processor); only cash
        # validates tendered and invoice carries the customer id.
        processor_kwargs = {}
        if method_enum in (PaymentMethod.CARD, PaymentMethod.MOBILE):
            processor_kwargs['require_token'] = False
        processor = get_processor(method_enum, **processor_kwargs)
        metadata = {}
        if method == Payment.METHOD_CASH and tendered is not None:
            metadata['tendered'] = tendered
        elif method == Payment.METHOD_INVOICE:
            metadata['customer_id'] = (customer or sale.customer).id
        result = processor.process_payment(amount, method_enum, metadata)
        if not result.success:
            raise PaymentProcessingRejected(result.error_message)

        # Over-tender change is the tender excess over the outstanding sale
        # balance this tender settles (existing POS reconciliation).
        change_due = Decimal('0')
        if method == Payment.METHOD_CASH and tendered is not None:
            settled = sum((p.amount for p in sale.payments.all()), Decimal('0'))
            remaining = max(Decimal('0'), sale.total - settled)
            change_due = max(Decimal('0'), tendered - min(tendered, remaining))

        if method == Payment.METHOD_INVOICE:
            sale.customer = customer
            sale.save(update_fields=['customer'])
            # Locked balance update (S2): re-read the customer row under
            # select_for_update inside the caller's atomic block, re-check the
            # credit limit against the locked value, then commit the increment
            # so concurrent invoice tenders cannot lose updates.
            locked = Customer.objects.select_for_update().get(pk=customer.pk)
            new_balance = locked.balance + amount
            if new_balance > locked.credit_limit and not manager_approved:
                raise PaymentProcessingRejected('credit limit exceeded')
            locked.balance = new_balance
            locked.save(update_fields=['balance'])

        row = Payment.objects.create(
            sale=sale,
            method=method,
            amount=amount,
            tendered=tendered,
            change_due=change_due,
            manager_approved=manager_approved,
            idempotency_key=idempotency_key,
            source=Payment.SOURCE_UI,
            transaction_id=result.transaction_id,
        )
        return [row]

    @action(detail=True, methods=['post'])
    def set_payment(self, request, pk=None):
        sale = self.get_object()
        if not sale.is_open():
            return Response({'detail': 'sale not open'}, status=400)
        method = request.data.get('method')
        if method not in dict(Payment.METHOD_CHOICES):
            return Response({'detail': 'invalid payment method'}, status=400)
        try:
            amount = Decimal(str(request.data.get('amount', 0)))
        except (InvalidOperation, TypeError, ValueError):
            return Response({'detail': 'invalid amount'}, status=400)
        if amount <= 0:
            return Response({'detail': 'amount must be positive'}, status=400)

        splits = None
        if method == Payment.METHOD_SPLIT:
            splits = request.data.get('splits')
            if not isinstance(splits, list) or not splits:
                return Response({'detail': 'split payment requires a splits list'}, status=400)

        tendered = request.data.get('tendered')
        if tendered is not None:
            try:
                tendered = Decimal(str(tendered))
            except (InvalidOperation, TypeError, ValueError):
                return Response({'detail': 'invalid tendered amount'}, status=400)
            if method == Payment.METHOD_CASH and tendered < amount:
                return Response({'detail': 'tendered amount is less than amount due'}, status=400)

        manager_approved = is_manager(request.user)
        # allow manager creds override
        mu = request.data.get('manager_username')
        mp = request.data.get('manager_password')
        if mu and mp:
            cand = authenticate(username=mu, password=mp)
            if cand and is_manager(cand):
                manager_approved = True

        # invoice gating (validation only; DB writes happen in the atomic block)
        customer = None
        if method == Payment.METHOD_INVOICE:
            customer = sale.customer
            if customer is None and request.data.get('customer') is not None:
                try:
                    customer = Customer.objects.filter(pk=request.data.get('customer')).first()
                except (TypeError, ValueError):
                    customer = None
            if customer is None:
                return Response({'detail': 'customer required'}, status=400)
            if not manager_approved:
                return Response({'detail': 'manager required'}, status=403)
            new_balance = customer.balance + amount
            if new_balance > customer.credit_limit and not manager_approved:
                return Response({'detail': 'credit limit exceeded'}, status=400)

        # Idempotency: a client-supplied key is the only thing that participates
        # in dedupe. A keyless tender gets a fresh random key (per-tender nonce)
        # and is therefore never replayed — two genuine equal-amount tenders
        # always record two rows (B1). We scope every lookup to this sale so a
        # key can never leak another sale's payment body (B2).
        effective_key, client_key = self._idempotency_key(request)

        if client_key is not None:
            # B2 (cross-sale): a client key already owned by another sale must
            # be rejected, never replayed and never shadowed. Client-supplied
            # keys must not collide with a key the server would derive (we no
            # longer derive guessable keys), so only a genuine reuse is caught
            # here.
            owned_by_other = (
                Payment.objects.filter(idempotency_key=client_key).exclude(sale=sale).exists()
                or Payment.objects.filter(correlation_id=client_key).exclude(sale=sale).exists()
            )
            if owned_by_other:
                return Response(
                    {'detail': 'idempotency key already used by another sale'},
                    status=status.HTTP_409_CONFLICT,
                )

        try:
            with db_transaction.atomic():
                # Dedupe applies only to a client-supplied key (keyless tenders
                # never replay). Scope to (sale, key) so retries re-read their
                # own sale's row, never a sibling's. Split replay reads by
                # correlation_id so it returns the full component set (B3).
                existing = None
                if client_key is not None:
                    if method == Payment.METHOD_SPLIT:
                        comps = list(
                            Payment.objects.filter(sale=sale, correlation_id=client_key)
                            .select_for_update()
                            .order_by('id')
                        )
                        if comps:
                            return Response(PaymentSerializer(comps, many=True).data, status=200)
                    else:
                        existing = Payment.objects.filter(
                            sale=sale, idempotency_key=client_key,
                        ).select_for_update().first()
                        if existing is not None:
                            return Response(PaymentSerializer(existing).data, status=200)

                created = self._record_payments(
                    sale, method, amount, tendered,
                    manager_approved, customer, splits, effective_key,
                )
                if method == Payment.METHOD_SPLIT:
                    payload = PaymentSerializer(created, many=True).data
                else:
                    payload = PaymentSerializer(created[0]).data
                return Response(payload, status=201)
        except PaymentProcessingRejected as e:
            return Response({'detail': str(e)}, status=400)
        except IntegrityError:
            # A concurrent request with the same key committed between our
            # select_for_update lookup and insert; the atomic block rolled
            # back, so return the committed (winning) row(s) as the idempotent
            # replay result — still scoped to this sale.
            if client_key is not None:
                if method == Payment.METHOD_SPLIT:
                    match = list(
                        Payment.objects.filter(sale=sale, correlation_id=client_key).order_by('id')
                    )
                    if match:
                        return Response(PaymentSerializer(match, many=True).data, status=200)
                else:
                    match = Payment.objects.filter(sale=sale, idempotency_key=client_key).first()
                    if match is not None:
                        return Response(PaymentSerializer(match).data, status=200)
            raise

    def _record_complete_cash_payment(self, sale):
        """Backward-compat single cash tender for the `complete` endpoint.

        Route through CashProcessor and apply the same idempotency/atomic
        guard as set_payment so a duplicated complete does not double-record.
        """
        amount = sale.total
        processor = get_processor(PaymentMethod.CASH)
        result = processor.process_payment(amount, PaymentMethod.CASH, {'tendered': amount})
        if not result.success:
            return False
        key = f'sale:{sale.id}:complete:backcompat'
        try:
            with db_transaction.atomic():
                existing = Payment.objects.filter(idempotency_key=key).select_for_update().first()
                if existing is not None:
                    return True
                settled = sum((p.amount for p in sale.payments.all()), Decimal('0'))
                remaining = max(Decimal('0'), sale.total - settled)
                change_due = max(Decimal('0'), amount - min(amount, remaining))
                Payment.objects.create(
                    sale=sale,
                    method=Payment.METHOD_CASH,
                    amount=amount,
                    tendered=amount,
                    change_due=change_due,
                    manager_approved=True,
                    idempotency_key=key,
                    source=Payment.SOURCE_COMPLETE_FLOW,
                    transaction_id=result.transaction_id,
                )
        except IntegrityError:
            return True  # a concurrent complete already recorded this cash tender
        return True

    @action(detail=True, methods=['post'])
    def complete(self, request, pk=None):
        """Complete a sale, supporting zero-value sales and multiple tenders."""
        sale = self.get_object()
        if sale.status != Sale.STATUS_OPEN and sale.status != Sale.STATUS_PENDING:
            return Response({'detail': 'not open'}, status=400)

        note = request.data.get('note', '')
        if sale.total == 0 and not note and not sale.note:
            return Response({'detail': 'a sales note is required for zero-value sales'}, status=400)

        if sale.total > 0 and not sale.payments.exists():
            # Backward-compatible single cash payment for older clients.
            self._record_complete_cash_payment(sale)

        if sale.total > 0:
            total_paid = sum((p.amount for p in sale.payments.all()), Decimal('0'))
            if total_paid < sale.total:
                return Response({'detail': 'recorded payments are less than the sale total'}, status=400)

        if note:
            sale.note = note
        if sale.total == 0:
            sale.needs_review = True

        # Calculate change from stored cash tender change values.
        change_due = sum((p.change_due for p in sale.payments.all() if p.method == Payment.METHOD_CASH), Decimal('0'))
        if change_due < 0:
            change_due = Decimal('0')

        # Use TransactionService to complete (stock movements only for stock products).
        try:
            if sale.total == 0:
                from django.db import transaction as db_transaction
                from django.utils import timezone
                with db_transaction.atomic():
                    sale.status = Sale.STATUS_COMPLETED
                    sale.completed_at = timezone.now()
                    sale.note = sale.note
                    sale.needs_review = True
                    sale.save(update_fields=['status', 'completed_at', 'note', 'needs_review'])
                    from inventory.models import StockMovement
                    for line in sale.lines.all():
                        if line.product.non_stock:
                            continue
                        StockMovement.objects.create(
                            product=line.product,
                            quantity=-line.quantity,
                            movement_type='sale',
                            reference=f'sale:{sale.number}',
                        )
                completed_sale = sale
            else:
                if not sale.payments.exists():
                    self._record_complete_cash_payment(sale)
                ts = TransactionService(require_payment_tokens=False)
                ts._current_sale = sale
                ts._cart = CartService(sale)
                ts._current_sale.note = sale.note
                ts._current_sale.needs_review = sale.needs_review
                completed_sale = ts.finalize_sale(sale, generate_receipt=True)
                completed_sale.note = sale.note
                completed_sale.needs_review = sale.needs_review
                completed_sale.save(update_fields=['note', 'needs_review'])
        except (TransactionError, InsufficientPaymentError) as e:
            return Response({'detail': str(e)}, status=400)

        data = SaleSerializer(completed_sale).data
        data['change_due'] = str(change_due)
        return Response(data)

    @action(detail=True, methods=['post'])
    def void(self, request, pk=None):
        sale = self.get_object()
        if sale.status == Sale.STATUS_COMPLETED:
            return Response({'detail': 'cannot void completed'}, status=400)
        if sale.status == Sale.STATUS_REFUNDED:
            return Response({'detail': 'cannot void refunded'}, status=400)
        
        # Use TransactionService to void
        try:
            ts = TransactionService()
            ts._current_sale = sale
            ts._cart = CartService(sale)
            ts.void_sale()
        except TransactionError as e:
            return Response({'detail': str(e)}, status=400)
        
        return Response(SaleSerializer(sale).data)

    @action(detail=True, methods=['post'])
    def refund(self, request, pk=None):
        """Refund a completed sale."""
        sale = self.get_object()
        if sale.status != Sale.STATUS_COMPLETED:
            return Response({'detail': 'can only refund completed sales'}, status=400)
        
        # manager gating
        manager_username = request.data.get('manager_username')
        manager_password = request.data.get('manager_password')
        manager_approved = is_manager(request.user)
        if manager_username and manager_password:
            cand = authenticate(username=manager_username, password=manager_password)
            if cand and is_manager(cand):
                manager_approved = True
        
        if not manager_approved:
            return Response({'detail': 'manager required'}, status=403)
        
        reason = request.data.get('reason', '')
        
        try:
            ts = TransactionService()
            refunded_sale = ts.refund_sale(
                sale_id=sale.id,
                reason=reason,
                manager_approved=manager_approved
            )
        except TransactionError as e:
            return Response({'detail': str(e)}, status=400)
        
        return Response(SaleSerializer(refunded_sale).data)
