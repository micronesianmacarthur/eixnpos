from rest_framework import viewsets, status, permissions
from rest_framework.decorators import action
from rest_framework.response import Response
from django.contrib.auth import authenticate
from django.utils import timezone
from decimal import Decimal, InvalidOperation
from .models import Sale, SaleLine, Payment
from .serializers import SaleSerializer, SaleLineSerializer, PaymentSerializer
from products.models import Product
from customers.models import Customer
from accounts.utils import is_manager


class SaleViewSet(viewsets.ModelViewSet):
    queryset = Sale.objects.all()
    serializer_class = SaleSerializer
    permission_classes = [permissions.IsAuthenticated]

    def perform_create(self, serializer):
        serializer.save(cashier=self.request.user)

    @action(detail=True, methods=['post'])
    def add_line(self, request, pk=None):
        sale = self.get_object()
        if sale.status != Sale.STATUS_OPEN:
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
        line = SaleLine.objects.create(sale=sale, product=product, quantity=qty, unit_price=product.price)
        sale.recompute()
        return Response(SaleLineSerializer(line).data, status=201)

    @action(detail=True, methods=['patch', 'delete'], url_path='lines/(?P<line_id>[^/.]+)')
    def line_detail(self, request, pk=None, line_id=None):
        sale = self.get_object()
        if sale.status != Sale.STATUS_OPEN:
            return Response({'detail': 'sale not open'}, status=400)
        try:
            line = SaleLine.objects.get(pk=line_id, sale=sale)
        except (SaleLine.DoesNotExist, TypeError, ValueError):
            return Response({'detail': 'line not found'}, status=404)
        if request.method == 'DELETE':
            line.delete()
            sale.recompute()
            return Response(status=204)
        if 'quantity' in request.data:
            try:
                qty = int(request.data['quantity'])
            except (TypeError, ValueError):
                return Response({'detail': 'invalid quantity'}, status=400)
            if qty < 1:
                return Response({'detail': 'quantity must be positive'}, status=400)
            line.quantity = qty
            line.save(update_fields=['quantity'])
        sale.recompute()
        return Response(SaleLineSerializer(line).data)

    @action(detail=True, methods=['post'], url_path='lines/(?P<line_id>[^/.]+)/discount')
    def apply_discount(self, request, pk=None, line_id=None):
        sale = self.get_object()
        if sale.status != Sale.STATUS_OPEN:
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
        base = line.unit_price * line.quantity
        if dtype == 'percent':
            if value > Decimal('100'):
                return Response({'detail': 'percent discount cannot exceed 100'}, status=400)
            discount_amount = (base * value / Decimal('100')).quantize(Decimal('0.01'))
        elif dtype == 'fixed':
            if value > base:
                return Response({'detail': 'discount cannot exceed line total'}, status=400)
            discount_amount = value.quantize(Decimal('0.01'))
        else:
            discount_amount = Decimal('0')
        line.discount_type = dtype
        line.discount_value = value
        line.discount_amount = discount_amount
        line.discount_authorizer = user
        line.save()
        sale.recompute()
        return Response(SaleLineSerializer(line).data)

    @action(detail=True, methods=['post'])
    def set_payment(self, request, pk=None):
        sale = self.get_object()
        if sale.status != Sale.STATUS_OPEN:
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
            customer.save()
        change_due = Decimal('0')
        if method == Payment.METHOD_CASH and tendered is not None:
            change_due = tendered - amount
        payment = Payment.objects.create(sale=sale, method=method, amount=amount, tendered=tendered, change_due=change_due, manager_approved=manager_approved)
        return Response(PaymentSerializer(payment).data, status=201)

    @action(detail=True, methods=['post'])
    def complete(self, request, pk=None):
        sale = self.get_object()
        if sale.status != Sale.STATUS_OPEN:
            return Response({'detail': 'not open'}, status=400)
        sale.status = Sale.STATUS_COMPLETED
        sale.completed_at = timezone.now()
        sale.save(update_fields=['status', 'completed_at'])
        # stock movement
        from inventory.models import StockMovement
        for line in sale.lines.all():
            StockMovement.objects.create(product=line.product, quantity=-line.quantity, movement_type='sale', reference=f'sale:{sale.number}')
        return Response(SaleSerializer(sale).data)

    @action(detail=True, methods=['post'])
    def void(self, request, pk=None):
        sale = self.get_object()
        if sale.status == Sale.STATUS_COMPLETED:
            return Response({'detail': 'cannot void completed'}, status=400)
        sale.status = Sale.STATUS_VOIDED
        sale.save(update_fields=['status'])
        return Response(SaleSerializer(sale).data)
