from rest_framework import serializers
from django.contrib.auth import get_user_model
from .models import Sale, SaleLine, Payment
from products.models import Product

User = get_user_model()


class SaleLineSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source='product.name', read_only=True)
    class Meta:
        model = SaleLine
        fields = ['id', 'product', 'product_name', 'quantity', 'unit_price', 'discount_type', 'discount_value', 'discount_amount', 'discount_authorizer', 'line_total']


class PaymentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Payment
        fields = ['id', 'method', 'amount', 'tendered', 'change_due', 'manager_approved', 'policy_authorized', 'voided', 'voided_at', 'created_at']


class SaleSerializer(serializers.ModelSerializer):
    lines = SaleLineSerializer(many=True, read_only=True)
    payments = PaymentSerializer(many=True, read_only=True)
    cashier_username = serializers.CharField(source='cashier.username', read_only=True)
    # Writable PK so manager/admin can reassign an open sale (see SaleViewSet,
    # where the manager-only gate lives and returns the correct 403). Optional
    # because sale creation omits it and the viewset attributes `request.user`.
    cashier = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.all(),
        required=False,
    )
    class Meta:
        model = Sale
        fields = ['id', 'number', 'cashier', 'cashier_username', 'customer', 'status', 'subtotal', 'discount_total', 'total', 'lines', 'payments', 'note', 'needs_review', 'created_at', 'completed_at']
        read_only_fields = ['cashier_username', 'status', 'subtotal', 'discount_total', 'total', 'note', 'needs_review', 'created_at', 'completed_at']

    def validate_cashier(self, value):
        # Reassignment target must be an active user with cashier/manager-tier
        # authority. (The "manager/admin may reassign" gate lives in the
        # viewset so it can return an HTTP 403 instead of a field 400.)
        from accounts.roles import MANAGER_ROLES
        if getattr(value, 'role', '') not in (*MANAGER_ROLES, 'cashier'):
            raise serializers.ValidationError(
                'Target user is not a cashier or manager.'
            )
        if not getattr(value, 'is_active', True):
            raise serializers.ValidationError('Target cashier is inactive.')
        return value
