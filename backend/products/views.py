from rest_framework import serializers, viewsets, permissions, decorators, response, status
from .models import Product

class ProductSerializer(serializers.ModelSerializer):
    quantity = serializers.IntegerField(read_only=True)
    is_low_stock = serializers.BooleanField(read_only=True)
    supplier_name = serializers.CharField(source='supplier.name', read_only=True, allow_null=True)

    class Meta:
        model = Product
        fields = '__all__'

class ProductViewSet(viewsets.ModelViewSet):
    queryset = Product.objects.all()
    serializer_class = ProductSerializer
    permission_classes = [permissions.IsAuthenticated]

    @decorators.action(detail=True, methods=['post'])
    def receive(self, request, pk=None):
        product = self.get_object()
        qty = int(request.data.get('quantity', 0))
        note = request.data.get('note', '')
        from inventory.models import StockMovement
        m = StockMovement.objects.create(product=product, movement_type='receive', quantity=qty, note=note)
        return response.Response({'movement_id': m.id, 'quantity': product.quantity}, status=status.HTTP_201_CREATED)
