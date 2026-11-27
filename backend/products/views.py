from decimal import Decimal
from django.db import IntegrityError, transaction
from django.db.models.deletion import ProtectedError
from django.utils import timezone
from rest_framework import serializers, viewsets, permissions, decorators, response, status
from rest_framework.exceptions import PermissionDenied
from accounts.roles import has_manager_role
from .models import Product, Category

class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = '__all__'

class CategoryViewSet(viewsets.ModelViewSet):
    queryset = Category.objects.filter(active=True).order_by('name')
    serializer_class = CategorySerializer
    permission_classes = [permissions.IsAuthenticated]

#: Product fields that make a product a *pack*: configuring or changing them
#: requires manager-tier authority (R2.4) and freezes once the pair has history.
MAPPING_FIELDS = {'unit_product', 'units_per_pack'}

class ProductSerializer(serializers.ModelSerializer):
    quantity = serializers.IntegerField(read_only=True)
    is_low_stock = serializers.BooleanField(read_only=True)
    supplier_name = serializers.CharField(source='supplier.name', read_only=True, allow_null=True)
    category_name = serializers.CharField(source='category.name', read_only=True, allow_null=True)
    sku = serializers.CharField(read_only=True)
    category = serializers.PrimaryKeyRelatedField(
        queryset=Category.objects.filter(active=True), required=True, allow_null=False
    )
    # Read-only pack-mapping lookups for the UI chooser and the repack dialog.
    unit_product_sku = serializers.CharField(source='unit_product.sku', read_only=True, allow_null=True)
    unit_product_name = serializers.CharField(source='unit_product.name', read_only=True, allow_null=True)
    pack_parents = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = '__all__'

    def get_pack_parents(self, obj):
        # ``obj.pack_products`` is the set of pack parents (products whose
        # ``unit_product`` points at this product). Only active parents are shown
        # to the chooser; the backend still rejects inactive/non-stock at request
        # time (R4.9/R4.10).
        return [
            {
                'id': p.id,
                'sku': p.sku,
                'name': p.name,
                'units_per_pack': p.units_per_pack,
            }
            for p in obj.pack_products.filter(active=True)
        ]

    def validate(self, attrs):
        # Validate the *resulting* state, not just the submitted fields, so a
        # partial PATCH that touches one side of the pair is checked against the
        # other side's stored value.
        instance = self.instance
        proposed_unit = attrs.get('unit_product', instance.unit_product if instance else None)
        proposed_units = attrs.get('units_per_pack', instance.units_per_pack if instance else None)

        # 1. Both null → not a pack (valid). Exactly one null → error on the null one.
        if proposed_unit is None and proposed_units is None:
            return attrs
        if proposed_unit is None:
            raise serializers.ValidationError(
                {'unit_product': 'unit_product is required when units_per_pack is set.'}
            )
        if proposed_units is None:
            raise serializers.ValidationError(
                {'units_per_pack': 'units_per_pack is required when unit_product is set.'}
            )

        # 2. Ratio bounds. The DB CHECK enforces 2..1,000,000, but a field-level
        # error is more helpful for the API than an uncaught constraint failure.
        if not (2 <= proposed_units <= 1_000_000):
            raise serializers.ValidationError({
                'units_per_pack': 'units_per_pack must be between 2 and 1000000.',
            })

        # 3. No self-reference.
        if instance is not None and proposed_unit.pk == instance.pk:
            raise serializers.ValidationError({
                'unit_product': 'A product cannot be its own unit_product.',
            })

        # 4. No cycle: walking unit_product from the proposed child must not loop
        #    back to this product. Bounded so pre-existing bad data cannot hang.
        current = proposed_unit
        hops = 0
        while current is not None and hops < 50:
            hops += 1
            if instance is not None and current.pk == instance.pk:
                raise serializers.ValidationError({
                    'unit_product': 'This mapping would create a cycle.',
                })
            # Walk up one level: the proposed child's own unit_product, etc.
            current = Product.objects.filter(pk=current.unit_product_id).first()

        return attrs


class ReceiveSerializer(serializers.Serializer):
    """Validate stock receiving input before any inventory mutation.

    quantity must be a positive integer bounded to 1_000_000 (rejecting
    non-numeric, negative, zero, huge and non-integral values would otherwise
    corrupt on_hand or overflow the SQLite INTEGER column). unit_cost is
    optional but, when supplied, must be a non-negative decimal with at most
    12 significant digits and 2 fraction places (NaN, Infinity and absurd
    digit counts are rejected by DecimalField before any ORM work). Ordering,
    booleans and floats that are not integral are all rejected by IntegerField,
    never silently coerced.
    """
    quantity = serializers.IntegerField(min_value=1, max_value=1_000_000)
    note = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    unit_cost = serializers.DecimalField(
        max_digits=12, decimal_places=2, required=False, min_value=Decimal('0'),
    )

class ProductViewSet(viewsets.ModelViewSet):
    queryset = Product.objects.prefetch_related('pack_products').all().order_by('sku')
    serializer_class = ProductSerializer
    permission_classes = [permissions.IsAuthenticated]

    def create(self, request, *args, **kwargs):
        self._assert_mapping_allowed(request)
        return super().create(request, *args, **kwargs)

    def update(self, request, *args, **kwargs):
        self._assert_mapping_allowed(request)
        return super().update(request, *args, **kwargs)

    def partial_update(self, request, *args, **kwargs):
        self._assert_mapping_allowed(request)
        return super().partial_update(request, *args, **kwargs)

    def perform_update(self, serializer):
        # Runs after serializer.is_valid() (so field-shape errors take precedence
        # over the policy error) and immediately before the row is saved.
        self._assert_mapping_not_frozen(serializer)
        super().perform_update(serializer)

    def _assert_mapping_allowed(self, request):
        """R2.4: setting/changing the pack mapping is manager/admin-only.

        Runs *before* ``super()`` so a payload that mixes a mapping field with
        other, allowed product fields applies nothing at all (no partial apply)
        when a cashier submits it.
        """
        if any(key in request.data for key in MAPPING_FIELDS):
            if not has_manager_role(request.user):
                raise PermissionDenied(
                    'Only managers and admins can configure pack mappings.'
                )

    def _assert_mapping_not_frozen(self, serializer):
        """R4.18: freeze the mapping for a pair once any Repack exists for it.

        Called from ``perform_update``, so the serializer has already validated
        shape and field errors take precedence. Only a *change* to the stored
        mapping of the pair with history is blocked; unrelated products stay
        editable (OQ-10). Re-saving identical values is allowed.
        """
        data = serializer.validated_data
        mapping_delta = {
            key: data[key] for key in MAPPING_FIELDS if key in data
        }
        if not mapping_delta:
            return

        instance = serializer.instance
        stored_child_id = instance.unit_product_id
        # A *change* is only blocked. Compare the submitted, validated values
        # against the stored ones; same value -> no change -> allowed.
        proposed_child = mapping_delta.get('unit_product')
        proposed_child_id = proposed_child.pk if proposed_child is not None else None
        proposed_units = mapping_delta.get('units_per_pack')
        child_changed = proposed_child_id != stored_child_id
        units_changed = proposed_units is not None and (
            instance.units_per_pack is None or proposed_units != instance.units_per_pack
        )
        if not (child_changed or units_changed):
            return

        from inventory.models import Repack
        pair_has_history = (
            Repack.objects.filter(
                source_product=instance, target_product_id=stored_child_id
            ).exists()
            or Repack.objects.filter(
                target_product=instance, source_product_id=stored_child_id
            ).exists()
        )
        if pair_has_history:
            raise serializers.ValidationError({
                'detail': 'This pack mapping cannot be changed because it is used '
                          'by existing repack history.',
                'code': 'mapping_in_use',
            })

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
        source_reference = request.data.get('source_reference') or note

        # Supplier defaults to the product's supplier and may be overridden by
        # an explicit ``supplier`` id. A Receipt always needs a supplier, so we
        # reject the request when none can be resolved.
        supplier = product.supplier
        supplier_override = request.data.get('supplier')
        if supplier_override is not None and str(supplier_override).strip() != '':
            from suppliers.models import Supplier
            try:
                supplier = Supplier.objects.get(pk=int(supplier_override))
            except (TypeError, ValueError, Supplier.DoesNotExist):
                return response.Response(
                    {'detail': 'Supplier does not exist.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        if supplier is None:
            return response.Response(
                {'detail': 'A supplier is required when receiving stock.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        from inventory.models import Receipt, ReceiptLine, StockMovement, CostLayer
        try:
            # Lock the product row, then record the Receipt audit trail, the
            # ReceiptLine, the stock movement (which applies
            # quantity = quantity + qty atomically via F() inside the same
            # transaction) and the CostLayer valuation row. The caller is
            # stamped as the operator on both the Receipt and the movement,
            # and the receipted unit_cost is persisted to the cost basis.
            with transaction.atomic():
                product = Product.objects.select_for_update().get(pk=product.pk)
                receipt = Receipt.objects.create(
                    supplier=supplier,
                    operator=request.user,
                    received_at=timezone.now(),
                    reference=source_reference,
                    note=note,
                )
                line = ReceiptLine.objects.create(
                    receipt=receipt,
                    product=product,
                    quantity=qty,
                    unit_cost=unit_cost or product.cost,
                )
                movement = StockMovement.objects.create(
                    product=product,
                    movement_type='receive',
                    quantity=qty,
                    reference=receipt.reference,
                    note=receipt.note,
                    adjusted_by=request.user,
                )
                layer = CostLayer.objects.create(
                    product=product, quantity=qty, unit_cost=unit_cost or product.cost,
                    received_at=timezone.now(), source_reference=source_reference,
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
            {
                'receipt_id': receipt.id,
                'receipt_line_id': line.id,
                'movement_id': movement.id,
                'cost_layer_id': layer.id,
                'quantity': product.quantity,
            },
            status=status.HTTP_201_CREATED,
        )
