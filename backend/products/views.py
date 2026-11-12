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

class ProductViewSet(viewsets.ModelViewSet):
    queryset = Product.objects.all()
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
        qty = int(request.data.get('quantity', 0))
        note = request.data.get('note', '')
        from inventory.models import StockMovement
        m = StockMovement.objects.create(product=product, movement_type='receive', quantity=qty, note=note)
        return response.Response({'movement_id': m.id, 'quantity': product.quantity}, status=status.HTTP_201_CREATED)
