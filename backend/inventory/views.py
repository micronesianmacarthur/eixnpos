from django.db import IntegrityError, transaction
from django.db.models import Q
from rest_framework import serializers, viewsets, permissions, status
from rest_framework.decorators import action
from rest_framework.exceptions import NotAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import IsManagerOrAdmin
from products.models import Product
from .models import Repack, StockMovement
from .permissions import CanPerformRepack, RepackSessionAuthentication
from .services import (
    REPACK_QTY_MIN,
    REPACK_QTY_MAX,
    RepackError,
    perform_repack,
)
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


class StrictIntegerField(serializers.IntegerField):
    """Whole JSON integers only: no strings, floats, or booleans (R1.5/R4.3).

    DRF's ``IntegerField`` would coerce ``"3"``, ``3.0`` and ``" 3 "`` into an int
    in this version, which silently relaxes the "no rounding / no coercion" rule.
    Any non-int input (including booleans, which are ``bool`` subclasses) is
    rejected as a field error before any ORM work.
    """

    def to_internal_value(self, data):
        if isinstance(data, bool) or not isinstance(data, int):
            raise serializers.ValidationError(
                'A whole-number integer is required.'
            )
        return super().to_internal_value(data)


class RepackCreateSerializer(serializers.Serializer):
    """The create payload: only the four fields the client may supply (R1.2).

    ``source_product``/``target_product`` must exist (FK resolution) and
    ``source_quantity`` must be a whole JSON integer in 1..1,000,000. Direction,
    ratio, computed target quantity, location, operator, and SKUs are all derived
    server-side from the stored pair; any client-supplied audit fields are simply
    ignored because they are not declared here.
    """
    source_product = serializers.PrimaryKeyRelatedField(queryset=Product.objects.all())
    target_product = serializers.PrimaryKeyRelatedField(queryset=Product.objects.all())
    source_quantity = StrictIntegerField(
        min_value=REPACK_QTY_MIN, max_value=REPACK_QTY_MAX
    )
    note = serializers.CharField(
        required=False, allow_blank=True, allow_null=True, max_length=500
    )


class RepackSerializer(serializers.ModelSerializer):
    """Read view of a Repack for create responses and history."""

    performed_by_name = serializers.CharField(
        source='performed_by.username', read_only=True
    )
    source_on_hand = serializers.SerializerMethodField()
    target_on_hand = serializers.SerializerMethodField()

    class Meta:
        model = Repack
        fields = [
            'id', 'reference', 'direction', 'location', 'performed_by',
            'performed_by_name', 'created_at', 'note', 'units_per_pack',
            'source_product', 'source_sku', 'source_quantity', 'source_on_hand',
            'target_product', 'target_sku', 'target_quantity', 'target_on_hand',
        ]

    def get_source_on_hand(self, obj):
        # The resulting on-hand for the source after this repack is recorded on its
        # ``repack_out`` movement.
        return obj.source_movement.new_on_hand

    def get_target_on_hand(self, obj):
        return obj.target_movement.new_on_hand


class RepackCreateView(APIView):
    """Create one repack: ``POST /api/inventory/repack/``.

    Authenticated cashiers/managers/admins/superusers may repack (R2.2); a rejected
    request writes nothing partial. A domain failure is a 400 carrying a stable
    machine ``code`` alongside the human ``detail``; a concurrent stock conflict is
    a 409. The full transaction lives in ``inventory.services.perform_repack`` so
    the view only interprets the outcome.
    """

    permission_classes = [CanPerformRepack]
    # Primarily for the 401 contract: see RepackSessionAuthentication.
    authentication_classes = [RepackSessionAuthentication]

    def permission_denied(self, request, message=None, code=None):
        # Spec R2.2/R1.9 mandates a 401 for an anonymous request. With only
        # Session auth configured, ``authenticate_header()`` returns None, so
        # DRF's ``handle_exception`` coerces the NotAuthenticated this method
        # raises back down to 403. The per-view ``RepackSessionAuthentication``
        # supplies a real WWW-Authenticate challenge so the 401 survives; this
        # override also makes the intent explicit. An authenticated-but-denied
        # user (e.g. inactive) still gets 403 via ``super()``.
        if not request.user.is_authenticated:
            raise NotAuthenticated()
        return super().permission_denied(request, message, code)

    def post(self, request):
        serializer = RepackCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        try:
            repack = perform_repack(
                source_product=data['source_product'],
                target_product=data['target_product'],
                source_quantity=data['source_quantity'],
                note=data.get('note') or '',
                user=request.user,
            )
        except RepackError as exc:
            return self._error_response(exc)
        except IntegrityError:
            return Response(
                {'detail': 'Stock changed concurrently; please retry.'},
                status=status.HTTP_409_CONFLICT,
            )

        out = RepackSerializer(repack)
        return Response(out.data, status=status.HTTP_201_CREATED)

    @staticmethod
    def _error_response(exc):
        # Field-scoped errors (e.g. sku_required) get a DRF-style field dict, but
        # the machine ``code`` is always included so consumers can branch on it.
        body = {'detail': exc.message, 'code': exc.code}
        if exc.field:
            body = {exc.field: [exc.message], 'code': exc.code}
        return Response(body, status=status.HTTP_400_BAD_REQUEST)


class RepackListView(APIView):
    """Read-only history: ``GET /api/inventory/repacks/``.

    Any authenticated user may list repacks (R2.3). Returns newest first as an
    unpaginated array (matching the existing inventory list). ``?product=<id>``
    matches a record when the id is either side of the pair; ``?direction=split|merge``
    filters. Invalid filter values return 400 (``invalid_direction``/``invalid_product``)
    rather than a silent empty list.
    """

    permission_classes = [permissions.IsAuthenticated]
    # Keep the anonymous->401 contract (see RepackSessionAuthentication) while
    # the rest of the site's Session auth stays untouched.
    authentication_classes = [RepackSessionAuthentication]

    def permission_denied(self, request, message=None, code=None):
        # Spec R2.3/AC-36 mandates a 401 for an anonymous read of repack
        # history. With only Session auth configured, ``authenticate_header()``
        # returns None and DRF's ``handle_exception`` would coerce a
        # NotAuthenticated back to 403; the per-view ``RepackSessionAuthentication``
        # keeps the 401, and this override makes the intent explicit.
        if not request.user.is_authenticated:
            raise NotAuthenticated()
        return super().permission_denied(request, message, code)

    def get(self, request):
        queryset = Repack.objects.select_related(
            'source_product', 'target_product', 'performed_by',
            'source_movement', 'target_movement',
        ).all()

        product = request.query_params.get('product')
        if product is not None:
            try:
                product_id = int(product)
            except (TypeError, ValueError):
                return Response(
                    {'detail': 'product must be an integer product id.',
                     'code': 'invalid_product'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            queryset = queryset.filter(
                Q(source_product_id=product_id) | Q(target_product_id=product_id)
            )

        direction = request.query_params.get('direction')
        if direction is not None:
            if direction not in Repack.Direction.values:
                return Response(
                    {'detail': 'direction must be one of split or merge.',
                     'code': 'invalid_direction'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            queryset = queryset.filter(direction=direction)

        # Meta.ordering is '-created_at', '-id' -> newest first already.
        data = RepackSerializer(queryset, many=True)
        return Response(data.data)


class RepackDetailView(APIView):
    """Explicit no-mutation detail route so PUT/PATCH/DELETE return 405.

    The plan (AC-32 / R1.8) requires ``PUT``/``PATCH``/``DELETE`` on an individual
    repack to return 405, but there is deliberately no mutation route. The router's
    catch-all detail regex (``^(?P<pk>[^/.]+)/$``) does not match the two-segment
    ``repacks/5/`` path, so without an explicit entry a detail request would fall
    through to a bare 404 instead of the documented 405. This view defines no
    method handler, so every verb returns 405 Method Not Allowed. History remains
    list-only (``GET /api/inventory/repacks/``), matching the spec.
    """

    permission_classes = [permissions.IsAuthenticated]
