from decimal import Decimal
from django.db import IntegrityError, transaction
from django.db.models.deletion import ProtectedError
from rest_framework import serializers, viewsets, permissions, decorators, response, status
from .models import Product, Category

class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = '__all__'

class CategoryViewSet(viewsets.ModelViewSet):
    queryset = Category.objects.filter(active=True).order_by('name')
    serializer_class = CategorySerializer
    permission_classes = [permissions.IsAuthenticated]

class ProductSerializer(serializers.ModelSerializer):
    quantity = serializers.IntegerField(read_only=True)
    is_low_stock = serializers.BooleanField(read_only=True)
    supplier_name = serializers.CharField(source='supplier.name', read_only=True, allow_null=True)
    category_name = serializers.CharField(source='category.name', read_only=True, allow_null=True)
    sku = serializers.CharField(read_only=True)
    category = serializers.PrimaryKeyRelatedField(
        queryset=Category.objects.filter(active=True), required=True, allow_null=False
    )

    class Meta:
        model = Product
        fields = '__all__'


class ReceiveSerializer(serializers.Serializer):
    """Validate stock receiving input before any inventory mutation.

    quantity must be a positive integer bounded to 1_000_000 (rejecting
    non-numeric, negative, zero, huge and non-integral values would otherwise
    corrupt on_hand or overflow the SQLite INTEGER column). unit_cost is
    optional but, when supplied, must be a non-negative decimal with at most
    12 significant digits and 2 fraction places (NaN, Infinity and absurd
    digit counts are rejected by DecimalField before any ORM work).
    """
    quantity = serializers.IntegerField(min_value=1, max_value=1_000_000)
    note = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    unit_cost = serializers.DecimalField(
        max_digits=12, decimal_places=2, required=False, min_value=Decimal('0'),
    )

class ProductViewSet(viewsets.ModelViewSet):
    queryset = Product.objects.all().order_by('sku')
    serializer_class = ProductSerializer
    permission_classes = [permissions.IsAuthenticated]

    def destroy(self, request, *args, **kwargs):
        try:
            return super().destroy(request, *args, **kwargs)
        except ProtectedError:
            return response.Response(
                {'detail': 'This product cannot be deleted because it is used in sales history.'},
                status=status.HTTP_409_CONFLICT,
            )

    @decorators.action(detail=True, methods=['post'])
    def receive(self, request, pk=None):
        product = self.get_object()
        if product.non_stock:
            return response.Response(
                {'detail': 'Non-stock products cannot receive stock.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        serializer = ReceiveSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        qty = data['quantity']
        note = data.get('note', '')
        unit_cost = data.get('unit_cost')
        from inventory.models import StockMovement
        try:
            # Lock the product row, record the receive movement (which applies
            # quantity = quantity + qty atomically via F() inside the same
            # transaction), and, when supplied, persist unit_cost to the cost
            # basis (S5). The caller is stamped as the operator (S1).
            with transaction.atomic():
                product = Product.objects.select_for_update().get(pk=product.pk)
                movement = StockMovement.objects.create(
                    product=product,
                    movement_type='receive',
                    quantity=qty,
                    note=note,
                    adjusted_by=request.user,
                )
                if unit_cost is not None:
                    product.cost = unit_cost
                    product.save(update_fields=['cost'])
                product.refresh_from_db(fields=['quantity'])
        except IntegrityError:
            return response.Response(
                {'detail': 'Stock changed concurrently; please retry.'},
                status=status.HTTP_409_CONFLICT,
            )
        return response.Response(
            {'movement_id': movement.id, 'quantity': product.quantity},
            status=status.HTTP_201_CREATED,
        )
