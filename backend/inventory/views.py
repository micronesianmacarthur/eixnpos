from django.db import transaction
from rest_framework import serializers, viewsets, permissions
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework import status

from accounts.permissions import IsManagerOrAdmin
from products.models import Product
from .models import StockMovement


class StockMovementSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source='product.name', read_only=True)
    adjusted_by_name = serializers.CharField(
        source='adjusted_by.username', read_only=True, allow_null=True
    )
    # Audit fields are operator-stamped server-side; clients must never be able
    # to set them directly (mass-assignment guard).
    adjusted_by = serializers.PrimaryKeyRelatedField(read_only=True)
    old_on_hand = serializers.IntegerField(read_only=True, allow_null=True)
    new_on_hand = serializers.IntegerField(read_only=True, allow_null=True)

    class Meta:
        model = StockMovement
        fields = '__all__'


class StockAdjustmentSerializer(serializers.Serializer):
    """Shared write payload for damage and cycle-count adjustments.

    ``product`` addresses a stock item; ``quantity`` carries the signed (or, for
    damage, the magnitude of) adjustment; ``note`` records the operator reason.
    """
    product = serializers.PrimaryKeyRelatedField(queryset=Product.objects.all())
    quantity = serializers.IntegerField()
    note = serializers.CharField(allow_blank=False)

    def validate_quantity(self, value):
        if value == 0:
            raise serializers.ValidationError(
                'Adjustment quantity must be a non-zero value.'
            )
        return value

    def validate_note(self, value):
        if not value or not value.strip():
            raise serializers.ValidationError(
                'A reason/note is required for inventory adjustments.'
            )
        return value


class StockMovementViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = StockMovement.objects.select_related('product', 'adjusted_by').all()
    serializer_class = StockMovementSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_permissions(self):
        # Read-only access stays available to any authenticated consumer; only
        # manager/admin-tier users may record adjustments.
        if self.action in ('damage', 'adjust'):
            return [IsManagerOrAdmin()]
        return super().get_permissions()

    def get_queryset(self):
        queryset = super().get_queryset()
        product = self.request.query_params.get('product')
        if product:
            queryset = queryset.filter(product_id=product)
        movement_type = self.request.query_params.get('movement_type')
        if movement_type:
            queryset = queryset.filter(movement_type=movement_type)
        return queryset

    @action(detail=False, methods=['post'])
    def damage(self, request):
        """Record a damage write-off: ``quantity`` is the (positive) units lost,
        which decrease on_hand."""
        return self._apply_adjustment(request, movement_type='damage')

    @action(detail=False, methods=['post'])
    def adjust(self, request):
        """Record a cycle-count correction: ``quantity`` is signed — positive
        increases on_hand, negative decreases on_hand."""
        return self._apply_adjustment(request, movement_type='adjustment')

    def _apply_adjustment(self, request, movement_type):
        serializer = StockAdjustmentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        product = data['product']

        if product.non_stock:
            return Response(
                {'detail': 'Non-stock products cannot be adjusted.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        requested = data['quantity']
        # damage is expressed as a magnitude of units lost; everything else as a
        # signed delta.
        delta = -abs(requested) if movement_type == 'damage' else requested

        # Apply the ledger change and capture the before/after balance in one
        # transaction so the movement row and its audit snapshot are atomic and
        # a concurrent adjustment cannot drive on_hand below zero.
        with transaction.atomic():
            product = Product.objects.select_for_update().get(pk=product.pk)
            old_on_hand = product.quantity
            new_on_hand = old_on_hand + delta
            if new_on_hand < 0:
                raise serializers.ValidationError(
                    f'Adjustment would make on-hand negative (current on-hand: '
                    f'{old_on_hand}).'
                )
            movement = StockMovement.objects.create(
                product=product,
                movement_type=movement_type,
                quantity=delta,
                reference=request.data.get('reference', ''),
                note=data['note'],
                adjusted_by=request.user,
                old_on_hand=old_on_hand,
                new_on_hand=new_on_hand,
            )

        return Response(
            {
                'movement_id': movement.id,
                'movement_type': movement_type,
                'quantity': delta,
                'old_on_hand': old_on_hand,
                'new_on_hand': new_on_hand,
                'note': data['note'],
                'adjusted_by': request.user.username,
            },
            status=status.HTTP_201_CREATED,
        )
