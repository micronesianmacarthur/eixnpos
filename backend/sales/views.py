from rest_framework import viewsets, status, permissions
from rest_framework.decorators import action
from rest_framework.response import Response
from django.contrib.auth import authenticate
from django.utils import timezone
from decimal import Decimal, InvalidOperation

from .models import Sale, SaleLine, Payment
from .serializers import SaleSerializer, SaleLineSerializer, PaymentSerializer
from .services import TransactionService, CartService, CartError, CartItemNotFoundError, InvalidQuantityError, SaleNotModifiableError, TransactionError, InsufficientPaymentError, InvalidPaymentMethodError
from products.models import Product
from customers.models import Customer
from accounts.utils import is_manager


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

        # All state writes (customer link, balance, payment insert) happen
        # inside a locked transaction that re-checks is_open() on the locked
        # instance. The pre-lock guard at the top is only a fast path: without
        # this re-check a concurrent complete/void/refund could finalize the
        # sale between the guard above and the insert, leaving an orphan
        # payment on a terminal sale. The Customer.balance read-modify-write
        # is a separate pre-existing lost-update outside this scope.
        from django.db import transaction as db_transaction

        class _SetPaymentError(Exception):
            """Domain error that maps to a specific HTTP status on the 400/403
            paths that must roll back the surrounding atomic write block."""

            def __init__(self, detail, http_status):
                super().__init__(detail)
                self.detail = detail
                self.http_status = http_status

        try:
            with db_transaction.atomic():
                sale = Sale.objects.select_for_update().get(pk=sale.pk)
                if not sale.is_open():
                    raise _SetPaymentError('sale not open', 400)

                # Resolve the invoice customer and validate every gating rule
                # BEFORE the first write, so a validation failure rolls the
                # whole block back (no partial customer/balance write survives
                # a 400) instead of committing a half-applied payment.
                if method == Payment.METHOD_INVOICE:
                    customer = sale.customer
                    if customer is None and request.data.get('customer') is not None:
                        try:
                            customer = Customer.objects.filter(pk=request.data.get('customer')).first()
                        except (TypeError, ValueError):
                            customer = None
                    if customer is None:
                        raise _SetPaymentError('customer required', 400)
                    if not manager_approved:
                        raise _SetPaymentError('manager required', 403)
                    new_balance = customer.balance + amount
                    if new_balance > customer.credit_limit and not manager_approved:
                        raise _SetPaymentError('credit limit exceeded', 400)

                # Change due is the tender excess over the outstanding sale balance this
                # tender settles: an over-tender returns change, exact/under returns none.
                change_due = Decimal('0')
                if method == Payment.METHOD_CASH and tendered is not None:
                    settled = sum((p.amount for p in sale.payments.all()), Decimal('0'))
                    remaining = max(Decimal('0'), sale.total - settled)
                    change_due = max(Decimal('0'), tendered - min(tendered, remaining))

                if method == Payment.METHOD_INVOICE:
                    sale.customer = customer
                    sale.save(update_fields=['customer'])
                    customer.balance = new_balance
                    customer.save(update_fields=['balance'])

                payment = Payment.objects.create(
                    sale=sale,
                    method=method,
                    amount=amount,
                    tendered=tendered,
                    change_due=change_due,
                    manager_approved=manager_approved
                )
        except _SetPaymentError as e:
            return Response({'detail': e.detail}, status=e.http_status)
        return Response(PaymentSerializer(payment).data, status=201)

    @action(detail=True, methods=['post'])
    def complete(self, request, pk=None):
        """Complete a sale, supporting zero-value sales and multiple tenders."""
        sale = self.get_object()
        if sale.status != Sale.STATUS_OPEN and sale.status != Sale.STATUS_PENDING:
            return Response({'detail': 'not open'}, status=400)

        note = request.data.get('note', '')
        if sale.total == 0 and not note and not sale.note:
            return Response({'detail': 'a sales note is required for zero-value sales'}, status=400)

        # Everything that writes state — the backward-compatible auto cash
        # payment, the note/needs_review folds, the terminal status write and
        # the stock movements — happens inside TransactionService.complete_view_sale
        # under a single select_for_update row lock. Two concurrent completes
        # on the same open sale therefore cannot both insert a payment or both
        # deduct stock: the loser waits on the lock, re-checks is_open() on the
        # now-completed locked instance, and its whole transaction rolls back.
        try:
            ts = TransactionService(require_payment_tokens=False)
            completed_sale = ts.complete_view_sale(
                sale.pk,
                note=note,
                auto_cash_payment=sale.total > 0,
                generate_receipt=True,
            )
        except (TransactionError, InsufficientPaymentError) as e:
            return Response({'detail': str(e)}, status=400)

        # Calculate change from stored cash tender change values.
        change_due = sum((p.change_due for p in completed_sale.payments.all() if p.method == Payment.METHOD_CASH), Decimal('0'))
        if change_due < 0:
            change_due = Decimal('0')

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
