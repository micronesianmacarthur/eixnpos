from rest_framework import viewsets, status, permissions
from rest_framework.decorators import action
from rest_framework.response import Response
from django.contrib.auth import authenticate
from django.utils import timezone
from decimal import Decimal
from .models import Sale, SaleLine, Payment
from .serializers import SaleSerializer, SaleLineSerializer, PaymentSerializer
from products.models import Product
from customers.models import Customer


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
        product_id = request.data.get('product')
        qty = int(request.data.get('quantity', 1))
        product = Product.objects.get(pk=product_id)
        line = SaleLine.objects.create(sale=sale, product=product, quantity=qty, unit_price=product.price)
        sale.recompute()
        return Response(SaleLineSerializer(line).data, status=201)

    @action(detail=True, methods=['patch'], url_path='lines/(?P<line_id>[^/.]+)')
    def update_line(self, request, pk=None, line_id=None):
        sale = self.get_object()
        line = SaleLine.objects.get(pk=line_id, sale=sale)
        if 'quantity' in request.data:
            line.quantity = int(request.data['quantity'])
            line.save(update_fields=['quantity'])
        sale.recompute()
        return Response(SaleLineSerializer(line).data)

    @action(detail=True, methods=['delete'], url_path='lines/(?P<line_id>[^/.]+)')
    def remove_line(self, request, pk=None, line_id=None):
        sale = self.get_object()
        SaleLine.objects.filter(pk=line_id, sale=sale).delete()
        sale.recompute()
        return Response(status=204)

    @action(detail=True, methods=['post'], url_path='lines/(?P<line_id>[^/.]+)/discount')
    def apply_discount(self, request, pk=None, line_id=None):
        sale = self.get_object()
        line = SaleLine.objects.get(pk=line_id, sale=sale)
        # manager gating
        manager_username = request.data.get('manager_username')
        manager_password = request.data.get('manager_password')
        user = authenticate(username=manager_username, password=manager_password)
        if not user or not (user.is_staff or getattr(user, 'role', '') in ('manager', 'admin')):
            # allow current user if manager
            if not (request.user.is_staff or getattr(request.user, 'role', '') in ('manager', 'admin')):
                return Response({'detail': 'manager required'}, status=403)
            user = request.user
        dtype = request.data.get('discount_type', 'none')
        value = Decimal(str(request.data.get('discount_value', 0)))
        line.discount_type = dtype
        line.discount_value = value
        base = line.unit_price * line.quantity
        if dtype == 'percent':
            line.discount_amount = (base * value / Decimal('100')).quantize(Decimal('0.01'))
        elif dtype == 'fixed':
            line.discount_amount = value
        else:
            line.discount_amount = Decimal('0')
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
        amount = Decimal(str(request.data.get('amount', 0)))
        tendered = request.data.get('tendered')
        tendered = Decimal(str(tendered)) if tendered is not None else None
        manager_approved = bool(request.data.get('manager_approved', False))
        # invoice gating
        if method == 'invoice':
            customer = sale.customer or (Customer.objects.filter(pk=request.data.get('customer')).first())
            if customer is None:
                return Response({'detail': 'customer required'}, status=400)
            sale.customer = customer
            sale.save(update_fields=['customer'])
            if not manager_approved and not (request.user.is_staff or getattr(request.user, 'role', '') in ('manager', 'admin')):
                return Response({'detail': 'manager required'}, status=403)
            new_balance = customer.balance + amount
            if new_balance > customer.credit_limit and not manager_approved:
                return Response({'detail': 'credit limit exceeded'}, status=400)
            customer.balance = new_balance
            customer.save()
        change_due = Decimal('0')
        if method == 'cash' and tendered is not None:
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
