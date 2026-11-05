from rest_framework import serializers, viewsets, status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import Customer, PayOnAccount


class PayOnAccountSerializer(serializers.ModelSerializer):
    class Meta:
        model = PayOnAccount
        fields = ['id', 'customer', 'amount', 'note', 'created_at']
        read_only_fields = ['id', 'created_at']


class CustomerSerializer(serializers.ModelSerializer):
    class Meta:
        model = Customer
        fields = ['id', 'name', 'phone', 'balance', 'credit_limit', 'notes', 'created_at', 'updated_at']


class CustomerViewSet(viewsets.ModelViewSet):
    queryset = Customer.objects.all()
    serializer_class = CustomerSerializer
    permission_classes = [IsAuthenticated]

    @action(detail=True, methods=['get'], url_path='transactions')
    def transactions(self, request, pk=None):
        customer = self.get_object()
        from sales.models import Sale
        sales = Sale.objects.filter(customer=customer).order_by('-created_at')
        from django.utils import timezone
        data = {
            'sales': [{'id': s.id, 'number': s.number, 'total': str(s.total), 'status': s.status, 'created_at': s.created_at} for s in sales],
            'pay_on_account': PayOnAccountSerializer(customer.payments.all(), many=True).data,
        }
        return Response(data)

    @action(detail=True, methods=['post'])
    def pay_on_account(self, request, pk=None):
        customer = self.get_object()
        amount = request.data.get('amount')
        note = request.data.get('note', '')
        try:
            amt = float(amount)
        except (TypeError, ValueError):
            return Response({'detail': 'amount required'}, status=status.HTTP_400_BAD_REQUEST)
        if amt <= 0:
            return Response({'detail': 'amount must be positive'}, status=status.HTTP_400_BAD_REQUEST)
        from decimal import Decimal
        customer.balance = (customer.balance or 0) - Decimal(str(amt))
        customer.save()
        PayOnAccount.objects.create(customer=customer, amount=amt, note=note)
        return Response(CustomerSerializer(customer).data)
