from django.db import IntegrityError, transaction
from rest_framework import serializers, viewsets, permissions, status
from rest_framework.decorators import action
from rest_framework.response import Response

from accounts.permissions import IsManagerOrAdmin
from products.models import Product
from .models import StockMovement

# Serializer-level bounds are the first line of defense for B1: they reject
# absurd or non-integral quantities before any ORM work, and the Product
# ``quantity >= 0`` CHECK constraint is the DB-level guarantee (B2).
ADJUST_QUANTITY_MIN = -1_000_000
ADJUST_QUANTITY_MAX = 1_000_000


class StrictStringField(serializers.CharField):
    """``CharField`` that rejects non-string input instead of stringifying it.

    DRF's ``CharField`` silently converts numbers to ``"123"``; the audit
    ``reference`` must stay a genuine string, so reject anything else (S2).
    """

    def to_internal_value(self, data):
        if not isinstance(data, str):
            raise serializers.ValidationError(
                'This field must be a string.', code='incorrect_type'
            )
        return super().to_internal_value(data)


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

    B1: quantity is a bounded integral value (-1..1M for adjust, interpreted as
    a positive magnitude for damage). Non-integral JSON numbers (``1.9``) and
    booleans are rejected by ``IntegerField`` rather than coerced to int.
    """
    product = serializers.PrimaryKeyRelatedField(queryset=Product.objects.all())
    quantity = serializers.IntegerField(
        min_value=ADJUST_QUANTITY_MIN, max_value=ADJUST_QUANTITY_MAX
    )
    note = serializers.CharField(allow_blank=False)
    reference = StrictStringField(
        required=False, allow_blank=True, trim_whitespace=False, max_length=128
    )

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
        """Record a damage write-off: ``quantity`` is a positive integer of
        units lost, which decrease on_hand. Negative quantities are rejected
        (S4) — they would otherwise book an unremarked loss."""
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
        requested = data['quantity']

        if product.non_stock:
            return Response(
                {'detail': 'Non-stock products cannot be adjusted.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # damage expresses a positive magnitude of units lost; everything else
        # is a signed delta. Any non-positive damage value is rejected instead
        # of being silently ``abs()``-ed (S4).
        if movement_type == 'damage':
            if requested <= 0:
                return Response(
                    {'detail': 'Damage quantity must be a positive number of '
                               'units lost.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            delta = -requested
        else:
            delta = requested

        try:
            # The Product row is locked, the balance is read under that lock,
            # and the movement insert (which applies the delta to the
            # materialized quantity atomically via F()) all happen inside one
            # transaction. On backends lacking row locks (SQLite) the
            # ``quantity >= 0`` CHECK constraint is the real guard: a racing
            # adjustment that would undersell raises IntegrityError below,
            # translated to a clean HTTP 409.
            with transaction.atomic():
                product = Product.objects.select_for_update().get(pk=product.pk)
                old_on_hand = product.quantity
                new_on_hand = old_on_hand + delta
                if new_on_hand < 0:
                    raise serializers.ValidationError(
                        f'Adjustment would make on-hand negative (current '
                        f'on-hand: {old_on_hand}).'
                    )
                movement = StockMovement.objects.create(
                    product=product,
                    movement_type=movement_type,
                    quantity=delta,
                    reference=data.get('reference', ''),
                    note=data['note'],
                    adjusted_by=request.user,
                    old_on_hand=old_on_hand,
                    new_on_hand=new_on_hand,
                )
        except IntegrityError:
            return Response(
                {'detail': 'Stock changed concurrently; please retry.'},
                status=status.HTTP_409_CONFLICT,
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
