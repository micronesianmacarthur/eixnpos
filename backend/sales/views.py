from rest_framework import viewsets, status, permissions
from rest_framework.decorators import action
from rest_framework.response import Response
from django.contrib.auth import authenticate
from django.db import transaction as db_transaction, IntegrityError
from django.db.models import Q
from django.utils import timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import uuid

from .models import Sale, SaleLine, Payment, ImmutableSaleError
from .serializers import SaleSerializer, SaleLineSerializer, PaymentSerializer
from .services import TransactionService, CartService, CartError, CartItemNotFoundError, InvalidQuantityError, SaleNotModifiableError, TransactionError, InsufficientPaymentError, InvalidPaymentMethodError, RefundValidationError, RefundConflictError, VoidConflictError, complete_sale_stock
from .payments import PaymentMethod, get_processor
from products.models import Product
from customers.models import Customer
from customers.services import LedgerService, LedgerError, can_override_credit
from accounts.utils import is_manager
from accounts.roles import has_admin_role


class PaymentProcessingRejected(Exception):
    """Raised when a payment processor refuses a tender (HTTP 400-equivalent)."""


class SaleViewSet(viewsets.ModelViewSet):
    queryset = Sale.objects.all()
    serializer_class = SaleSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        # A manager/admin may operate on any sale. A cashier is scoped to the
        # sales they handle: open/pending sales (which they run at the POS) and
        # completed sales they themselves recorded. This is both a data-scope
        # guard and the substrate for the B2 authorisation gate on voiding a
        # completed sale (only managers/admin may void a financial sale).
        if is_manager(self.request.user):
            return Sale.objects.all()
        return Sale.objects.filter(
            Q(status__in=[Sale.STATUS_PENDING, Sale.STATUS_OPEN])
            | Q(cashier=self.request.user)
        )
    
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

    def _immutability_guard(self, request, sale):
        """Reject a PATCH/PUT that would mutate an immutable sale's identity.

        Completed, voided and refunded sales are immutable records. Any payload
        touching an inviolate field (number, cashier, customer, session, totals)
        on a non-open sale is rejected regardless of role — there is no
        correction path through generic update; corrections only happen via the
        dedicated void/refund actions which write the audit trail. Returns an
        error Response to short-circuit, or None to allow.
        """
        if sale.is_open():
            return None
        data_keys = set(request.data.keys()) if hasattr(request.data, 'keys') else set()
        touched = sorted(sale.INVIOLATE_FIELDS.intersection(data_keys))
        if touched:
            return Response(
                {'detail': f'Cannot change field(s) on a completed sale: {", ".join(touched)}.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return None

    def update(self, request, *args, **kwargs):
        """Update a sale; gate `cashier` (manager) and immutable identity (all)."""
        instance = self.get_object()
        guard = self._reassign_guard(request, instance)
        if guard is not None:
            return guard
        guard = self._immutability_guard(request, instance)
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

    def destroy(self, request, *args, **kwargs):
        """Delete a sale is admin-only and open-sale-only.

        Only a hard-delete of an open/pending (draft) sale is ever allowed, and
        only by admin authority. Completed/corrected sales refuse deletion at
        the model level (``Sale.delete`` raises ``ImmutableSaleError``) — a
        completed sale must be voided/refunded, never erased. A non-admin gets
        403; any attempt to hard-delete an immutable sale gets 400.
        """
        if not has_admin_role(request.user):
            return Response(
                {'detail': 'Only admins can delete a sale.'},
                status=status.HTTP_403_FORBIDDEN,
            )
        instance = self.get_object()
        if not instance.is_open():
            return Response(
                {'detail': 'Only open (draft) sales can be deleted.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            return super().destroy(request, *args, **kwargs)
        except ImmutableSaleError as e:
            return Response({'detail': str(e)}, status=status.HTTP_400_BAD_REQUEST)

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

    def _record_payments(self, sale, method, amount, tendered, manager_approved, customer, splits, idempotency_key, *, operator=None, account_exception=False):
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
            for comp in result.metadata['splits']:
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
                    # R4: each split component gets a fresh UUID key. Deriving a
                    # component key from the client key (f"{idempotency_key}:{method}:
                    # {idx}") overflowed the 64-char column for a long client key
                    # (accepted by SQLite, DataError → 500 on PostgreSQL). Idempotent
                    # replay of a split is keyed by correlation_id (== client key),
                    # never by the per-component key, so a fixed-length UUID is safe.
                    idempotency_key=str(uuid.uuid4()),
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
            # Authoritative ledger write: appends the invoice charge and
            # re-derives the balance under a row lock (the ceiling is
            # re-enforced there, race-safely). This keeps the ledger and the
            # cached Customer.balance from diverging.
            LedgerService.record_invoice_payment(
                customer, amount, operator=operator, sale=sale,
                account_exception=account_exception,
            )

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
        method = request.data.get('method')

        # Resolve the idempotency key up front so key-length validation returns
        # a clean 400 and the R5 replay guard can run before the open-status
        # check.
        try:
            effective_key, client_key = self._idempotency_key(request)
        except PaymentProcessingRejected as e:
            return Response({'detail': str(e)}, status=400)

        # R5 — a tender already recorded under this client key replays its
        # existing row(s) even if the sale has since been completed. Without
        # this, tender-with-key → complete → resubmit-same-key returned 400
        # "sale not open" even though the row exists. Scoped to this sale only
        # (never another sale's payment body).
        if client_key is not None:
            if method == Payment.METHOD_SPLIT:
                comps = list(
                    Payment.objects.filter(sale=sale, correlation_id=client_key).order_by('id')
                )
                if comps:
                    return Response(PaymentSerializer(comps, many=True).data, status=200)
            else:
                existing = Payment.objects.filter(
                    sale=sale, idempotency_key=client_key,
                ).first()
                if existing is not None:
                    return Response(PaymentSerializer(existing).data, status=200)

        if not sale.is_open():
            return Response({'detail': 'sale not open'}, status=400)
        if method not in dict(Payment.METHOD_CHOICES):
            return Response({'detail': 'invalid payment method'}, status=400)
        try:
            amount = Decimal(str(request.data.get('amount', 0)))
        except (InvalidOperation, TypeError, ValueError):
            return Response({'detail': 'invalid amount'}, status=400)
        if amount <= 0:
            return Response({'detail': 'amount must be positive'}, status=400)
        # Quantize once at the request boundary (ROUND_HALF_UP, matching
        # LedgerService.coerce_amount) so the stored Payment.amount and the
        # ledger entry record the identical Decimal. DecimalField storage
        # would otherwise apply ROUND_HALF_EVEN to an unquantized 3-decimal
        # input, splitting the two records (e.g. 20.005 -> Payment 20.00,
        # ledger 20.01).
        amount = amount.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

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
        account_exception = False
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
            sale.customer = customer
            sale.save(update_fields=['customer'])
            # Credit-limit semantics: manager approval authorizes the invoice
            # sale itself; the credit ceiling is a separate, hard policy on the
            # running ledger balance and is enforced even for manager-approved
            # invoices. Breaching it needs an account exception, which only an
            # operator with ``can_override_credit`` authority may grant.
            account_exception = can_override_credit(request.user)
            try:
                LedgerService.preflight(
                    customer, amount, direction='credit',
                    approved=account_exception, operator=request.user,
                )
            except LedgerError as e:
                return Response({'detail': str(e)}, status=400)

        # Idempotency: a client-supplied key is the only thing that participates
        # in dedupe. A keyless tender gets a fresh random key (per-tender nonce)
        # and is therefore never replayed — two genuine equal-amount tenders
        # always record two rows (B1). We scope every lookup to this sale so a
        # key can never leak another sale's payment body (B2). The key is
        # resolved at the top of set_payment (see R5 guard).

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
                # Re-fetch + lock the sale row inside the atomic: the open-status
                # guard above ran on the view's (unlocked) instance, so a
                # completion landing between that guard and this write would
                # otherwise append a Payment to an already-completed sale. The
                # authoritative is_open() check now runs against the LOCKED row;
                # the loser 400s and its block rolls back (no orphan Payment).
                sale = Sale.objects.select_for_update().get(pk=sale.pk)
                if not sale.is_open():
                    return Response({'detail': 'sale not open'}, status=400)

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
                    operator=request.user, account_exception=account_exception,
                )
                if method == Payment.METHOD_SPLIT:
                    payload = PaymentSerializer(created, many=True).data
                else:
                    payload = PaymentSerializer(created[0]).data
                return Response(payload, status=201)
        except PaymentProcessingRejected as e:
            return Response({'detail': str(e)}, status=400)
        except LedgerError as e:
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
                # Lock the sale row FIRST and re-validate against the LOCKED
                # row, mirroring set_payment: a tender recorded by set_payment,
                # or a completion, landing concurrently must suppress this
                # backcompat cash tender instead of double-recording money. If
                # any non-voided payment already covers the sale, or the sale
                # is no longer open, defer to that payment/completion (the
                # caller's finalize_sale re-validates coverage on the locked
                # row).
                sale = Sale.objects.select_for_update().get(pk=sale.pk)
                if not sale.is_open():
                    return True  # completed/voided/refunded; nothing to add
                covered = Payment.objects.filter(
                    sale=sale, voided=False
                ).exists()
                if covered:
                    return True  # a concurrent tender already paid this sale

                # R2a: scope the dedupe to THIS sale (a key is only meaningful on
                # one sale) AND to genuine complete-flow rows, so a client row
                # that squats the backcompat key can never shadow the real cash
                # tender. Because idempotency_key is globally unique, a squat on
                # ANY sale (this or another) makes the deterministic key unusable
                # for this write — in that case record the genuine tender under a
                # fresh UUID so the payment is never lost and never collides.
                effective_key = key
                existing = Payment.objects.filter(
                    sale=sale,
                    idempotency_key=key,
                    source=Payment.SOURCE_COMPLETE_FLOW,
                ).select_for_update().first()
                if existing is not None:
                    return True
                if Payment.objects.filter(idempotency_key=key).exists():
                    effective_key = str(uuid.uuid4())
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
                    idempotency_key=effective_key,
                    source=Payment.SOURCE_COMPLETE_FLOW,
                    transaction_id=result.transaction_id,
                )
        except IntegrityError:
            return True  # a concurrent complete already recorded this cash tender
        return True

    @action(detail=True, methods=['post'])
    def complete(self, request, pk=None):
        """Complete a sale, supporting zero-value sales and multiple tenders.

        Routes BOTH the positive-total and zero-total paths through
        ``TransactionService.finalize_sale`` (the single service completion
        operation), so the two paths share the same no-items rejection, stock
        movements and atomic status transition and cannot drift.
        """
        sale = self.get_object()
        if sale.status != Sale.STATUS_OPEN and sale.status != Sale.STATUS_PENDING:
            return Response({'detail': 'not open'}, status=400)

        note = request.data.get('note', '')

        if sale.total > 0 and not sale.payments.exists():
            # Backward-compatible single cash payment for older clients.
            self._record_complete_cash_payment(sale)

        if note:
            sale.note = note

        # Calculate change from stored cash tender change values.
        change_due = sum((p.change_due for p in sale.payments.all() if p.method == Payment.METHOD_CASH), Decimal('0'))
        if change_due < 0:
            change_due = Decimal('0')

        # Use TransactionService.finalize_sale for both paths: it does the
        # no-lines/empty-sale check, the zero-total note + needs_review handling,
        # the payment coverage check (positive-total) and the stock movements.
        try:
            # The zero-total and positive-total paths both route through
            # finalize_sale so they cannot drift (validation, stock movement
            # and the sanctioned completed transition are identical).
            ts = TransactionService(require_payment_tokens=False)
            ts._current_sale = sale
            ts._cart = CartService(sale)
            completed_sale = ts.finalize_sale(sale, generate_receipt=True)
        except (TransactionError, InsufficientPaymentError) as e:
            return Response({'detail': str(e)}, status=400)

        data = SaleSerializer(completed_sale).data
        data['change_due'] = str(change_due)
        return Response(data)

    @action(detail=True, methods=['post'])
    def void(self, request, pk=None):
        """Void an open/pending sale; manager/admin-only, records audit trail."""
        if not is_manager(request.user):
            return Response(
                {'detail': 'Only managers and admins can void a sale.'},
                status=status.HTTP_403_FORBIDDEN,
            )
        sale = self.get_object()
        # Duplicate/terminal void requests surface as a conflict so a retry is
        # never silently absorbed; anything else that is not open/pending (a
        # completed sale) is a 400 — completed sales are corrected by refund.
        if sale.status == Sale.STATUS_VOIDED:
            return Response({'detail': 'already voided'}, status=status.HTTP_409_CONFLICT)
        if sale.status == Sale.STATUS_REFUNDED:
            return Response(
                {'detail': 'cannot void a refunded sale'},
                status=status.HTTP_409_CONFLICT,
            )
        if not (sale.status == Sale.STATUS_OPEN or sale.status == Sale.STATUS_PENDING):
            return Response(
                {'detail': 'Only open/pending sales can be voided.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        reason = request.data.get('reason', '')
        # Use TransactionService to void (writes voided_by/voided_at/void_reason
        # atomically with the status change; a completed sale is corrected with
        # a refund instead).
        try:
            ts = TransactionService()
            ts._current_sale = sale
            ts._cart = CartService(sale)
            sale = ts.void_sale(by=request.user, reason=reason)
        except VoidConflictError as e:
            return Response({'detail': str(e)}, status=status.HTTP_409_CONFLICT)
        except (TransactionError, ImmutableSaleError) as e:
            return Response({'detail': str(e)}, status=400)

        return Response(SaleSerializer(sale).data)

    @action(detail=True, methods=['post'])
    def refund(self, request, pk=None):
        """Refund a completed sale (full, or partial per-line line items)."""
        sale = self.get_object()

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
        items = request.data.get('items')
        if items is not None and not isinstance(items, list):
            return Response({'detail': 'items must be a list'}, status=400)

        try:
            ts = TransactionService()
            refunded_sale = ts.refund_sale(
                sale_id=sale.id,
                reason=reason,
                manager_approved=manager_approved,
                items=items,
                by=request.user,
            )
        except (RefundConflictError, VoidConflictError) as e:
            return Response({'detail': str(e)}, status=status.HTTP_409_CONFLICT)
        except (RefundValidationError,) as e:
            return Response({'detail': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except TransactionError as e:
            return Response({'detail': str(e)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(SaleSerializer(refunded_sale).data)
