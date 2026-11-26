from decimal import Decimal, InvalidOperation
from django.db import transaction
from django.db.models.deletion import ProtectedError
from django.utils import timezone
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

        # Quantity must be a positive integer.
        try:
            qty = int(request.data.get('quantity'))
        except (TypeError, ValueError):
            return response.Response(
                {'detail': 'Quantity must be a positive integer.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if qty <= 0:
            return response.Response(
                {'detail': 'Quantity must be a positive integer.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Unit cost is required and must be a valid non-negative amount. We
        # reject a missing/invalid value instead of silently recording a zero
        # cost, which would otherwise make the product impossible to value.
        unit_cost_raw = request.data.get('unit_cost')
        if unit_cost_raw is None or str(unit_cost_raw).strip() == '':
            return response.Response(
                {'detail': 'Unit cost is required when receiving stock.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            unit_cost = Decimal(str(unit_cost_raw))
        except (InvalidOperation, TypeError, ValueError):
            return response.Response(
                {'detail': 'Unit cost must be a valid number.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if unit_cost < 0:
            return response.Response(
                {'detail': 'Unit cost cannot be negative.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        unit_cost = unit_cost.quantize(Decimal('0.01'))

        note = request.data.get('note', '')
        source_reference = request.data.get('source_reference') or note

        from inventory.models import StockMovement, CostLayer
        with transaction.atomic():
            m = StockMovement.objects.create(
                product=product, movement_type='receive', quantity=qty,
                note=note, reference=source_reference,
            )
            layer = CostLayer.objects.create(
                product=product, quantity=qty, unit_cost=unit_cost,
                received_at=timezone.now(), source_reference=source_reference,
            )
        return response.Response(
            {'movement_id': m.id, 'cost_layer_id': layer.id, 'quantity': product.quantity},
            status=status.HTTP_201_CREATED,
        )
