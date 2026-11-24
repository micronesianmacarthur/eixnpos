from rest_framework import viewsets, status, permissions
from rest_framework.decorators import action
from rest_framework.response import Response
from django.contrib.auth import authenticate
from django.utils import timezone
from decimal import Decimal, InvalidOperation

from .models import Sale, SaleLine, Payment
from .serializers import SaleSerializer, SaleLineSerializer, PaymentSerializer
from .services import TransactionService, CartService, CartError, CartItemNotFoundError, InvalidQuantityError, SaleNotModifiableError, TransactionError, InsufficientPaymentError, InvalidPaymentMethodError, RefundValidationError, RefundConflictError, VoidConflictError
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
        
        # invoice gating
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
            sale.customer = customer
            sale.save(update_fields=['customer'])
            new_balance = customer.balance + amount
            if new_balance > customer.credit_limit and not manager_approved:
                return Response({'detail': 'credit limit exceeded'}, status=400)
            customer.balance = new_balance
            customer.save(update_fields=['balance'])
        
        # Change due is the tender excess over the outstanding sale balance this
        # tender settles: an over-tender returns change, exact/under returns none.
        change_due = Decimal('0')
        if method == Payment.METHOD_CASH and tendered is not None:
            settled = sum((p.amount for p in sale.payments.all()), Decimal('0'))
            remaining = max(Decimal('0'), sale.total - settled)
            change_due = max(Decimal('0'), tendered - min(tendered, remaining))
        
        payment = Payment.objects.create(
            sale=sale,
            method=method,
            amount=amount,
            tendered=tendered,
            change_due=change_due,
            manager_approved=manager_approved
        )
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

        if sale.total > 0 and not sale.payments.exists():
            # Backward-compatible single cash payment for older clients.
            payment = Payment.objects.create(
                sale=sale,
                method=Payment.METHOD_CASH,
                amount=sale.total,
                tendered=sale.total,
                change_due=Decimal('0'),
                manager_approved=True,
            )
            _ = payment

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
                    Payment.objects.create(
                        sale=sale,
                        method=Payment.METHOD_CASH,
                        amount=sale.total,
                        tendered=sale.total,
                        change_due=Decimal('0'),
                        manager_approved=True,
                    )
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

        # Void is allowed on pending/open/completed sales. A voided or refunded
        # sale is rejected by the service as a 409 conflict so duplicate void
        # requests surface instead of being silently absorbed.
        try:
            ts = TransactionService()
            ts._current_sale = sale
            ts._cart = CartService(sale)
            ts.void_sale()
        except VoidConflictError as e:
            return Response({'detail': str(e)}, status=status.HTTP_409_CONFLICT)
        except TransactionError as e:
            return Response({'detail': str(e)}, status=status.HTTP_400_BAD_REQUEST)

        sale.refresh_from_db()
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
            )
        except (RefundConflictError, VoidConflictError) as e:
            return Response({'detail': str(e)}, status=status.HTTP_409_CONFLICT)
        except (RefundValidationError,) as e:
            return Response({'detail': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except TransactionError as e:
            return Response({'detail': str(e)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(SaleSerializer(refunded_sale).data)
