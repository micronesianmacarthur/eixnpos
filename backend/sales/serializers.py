from rest_framework import serializers
from .models import Sale, SaleLine, Payment
from products.models import Product


class SaleLineSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source='product.name', read_only=True)
    class Meta:
        model = SaleLine
        fields = ['id', 'product', 'product_name', 'quantity', 'unit_price', 'discount_type', 'discount_value', 'discount_amount', 'discount_authorizer', 'line_total']


class PaymentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Payment
        fields = ['id', 'method', 'amount', 'tendered', 'change_due', 'manager_approved', 'created_at']


class SaleSerializer(serializers.ModelSerializer):
    lines = SaleLineSerializer(many=True, read_only=True)
    payments = PaymentSerializer(many=True, read_only=True)
    class Meta:
        model = Sale
        fields = ['id', 'number', 'cashier', 'customer', 'status', 'subtotal', 'discount_total', 'total', 'lines', 'payments', 'note', 'needs_review', 'created_at', 'completed_at']
        read_only_fields = ['cashier', 'status', 'subtotal', 'discount_total', 'total', 'note', 'needs_review', 'created_at', 'completed_at']
