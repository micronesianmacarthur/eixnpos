"""Core repack service: validates a pair, computes direction/ratio/quantities
server-side, and writes one ``Repack`` + two ``StockMovement`` rows atomically.

Everything a view needs to create a repack lives here so the DRF view stays thin
and the transactional/locking behavior is unit-testable independently of HTTP.

Domain failures are raised as :class:`RepackError` carrying a stable machine ``code``
plus a human ``message``; the view translates them to HTTP 400 with the
``{"detail": ..., "code": ...}`` body the frontend already surfaces. A concurrent
stock conflict surfaces as ``IntegrityError`` and the view translates it to HTTP 409.
"""

import secrets

from django.db import IntegrityError, transaction
from django.db.models import F
from rest_framework import serializers

from products.models import Product
from .models import REPACK_LOCATION, Repack, StockMovement


class RepackError(Exception):
    """A domain validation failure with a stable machine ``code``.

    ``field`` is optional; when set the message is emitted as a field error dict
    ``{field: [message]}`` (used for ``sku_required`` so the frontend can attach it
    to the right row). Otherwise the response is the shared
    ``{"detail": message, "code": code}`` body.
    """

    def __init__(self, message, code, field=None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.field = field


#: Whole-unit quantity/direction bounds (mirrors the adjustment bounds).
REPACK_QTY_MIN = 1
REPACK_QTY_MAX = 1_000_000


def generate_reference():
    """Return a unique repack reference: ``RP-`` + 12 uppercase hex chars.

    12 hex chars give a negligible collision chance; the unique DB index on
    ``Repack.reference`` is the last-resort guard and the caller retries on the
    rare collision (see :func:`_insert_with_reference_retry`).
    """
    return 'RP-' + secrets.token_hex(6).upper()


def _resolve_pair(source, target):
    """Derive direction and ratio from the stored pair, never from the client.

    Returns ``(direction, ratio)`` or raises ``RepackError('not_a_repack_pair')``.
    Split is checked first: a both-ways pair (source.unit_product == target and
    target.unit_product == source) is an invalid configuration that only a
    serializer-bypass could produce, and is treated as not a valid direct link
    (OQ-10) rather than somehow being both.
    """
    if source.pk == target.pk:
        raise RepackError(
            'Source and target cannot be the same product.',
            code='same_product',
        )
    if source.unit_product_id == target.id:
        return Repack.Direction.SPLIT, source.units_per_pack
    if target.unit_product_id == source.id:
        return Repack.Direction.MERGE, target.units_per_pack
    raise RepackError(
        'The selected products are not a configured repack pair.',
        code='not_a_repack_pair',
    )


def _validate_products(source, target, direction):
    """Every product-level guard required before stock moves (R4.9, R4.10, R4.22)."""
    for label, product in (('source', source), ('target', target)):
        if product.non_stock:
            raise RepackError(
                f'Non-stock products cannot be repacked ({label} SKU {product.sku or ""}).',
                code='invalid_repack',
            )
        if not product.active:
            raise RepackError(
                f'Inactive products cannot be repacked ({label} SKU {product.sku or ""}).',
                code='invalid_repack',
            )
        if not product.sku or not product.sku.strip():
            raise RepackError(
                f'{label.capitalize()} product must have an SKU to record a repack.',
                code='sku_required',
                field='source_product' if label == 'source' else 'target_product',
            )


def perform_repack(*, source_product, target_product, source_quantity, note, user):
    """Create one repack atomically.

    ``source_product``/``target_product`` are validated ``Product`` instances and
    ``source_quantity`` an ``int`` already bounded to ``1..1_000_000`` by the
    serializer; this function re-checks the pair invariants against the *locked*
    rows (the source of truth at write time) so a mapping change or a rival repack
    cannot slip through even if the serializer saw a stale value.

    Returns the created :class:`Repack` (with movements accessible via
    ``source_movement``/``target_movement``).

    Raises:
        RepackError: a domain failure (no rows written).
        IntegrityError: a concurrent stock conflict (checked in the view -> 409).
    """
    with transaction.atomic():
        # Lock both product rows in ascending pk order so a concurrent split/merge
        # on the same pair never deadlocks (R4.16). Unknown ids return 400 as a
        # field error at the serializer; this is a defensive second look.
        ids = sorted({source_product.pk, target_product.pk})
        products = list(
            Product.objects.select_for_update().filter(pk__in=ids).order_by('pk')
        )
        by_id = {p.pk: p for p in products}
        source = by_id.get(source_product.pk)
        target = by_id.get(target_product.pk)
        if source is None or target is None:
            raise serializers.ValidationError(
                'A repack product no longer exists.'
            )

        direction, ratio = _resolve_pair(source, target)
        _validate_products(source, target, direction)

        if ratio is None or not (REPACK_QTY_MIN <= ratio <= REPACK_QTY_MAX):
            raise RepackError(
                'The pack mapping is not properly configured for this pair.',
                code='not_a_repack_pair',
            )

        # Direction-specific arithmetic (R1.3/R1.4), validated before any write.
        if direction == Repack.Direction.SPLIT:
            target_quantity = source_quantity * ratio
            if target_quantity > REPACK_QTY_MAX:
                raise RepackError(
                    f'Computed target quantity {target_quantity} exceeds the '
                    f'allowed maximum of {REPACK_QTY_MAX}.',
                    code='target_quantity_too_large',
                )
        else:  # merge
            remainder = source_quantity % ratio
            if remainder != 0:
                raise RepackError(
                    f'{source_quantity} is not an exact multiple of {ratio} '
                    f'(remainder {remainder}). Select a whole multiple to merge.',
                    code='remainder_not_allowed',
                )
            target_quantity = source_quantity // ratio

        # Stock sufficiency (R4.1) against the locked on-hand; message states the
        # current (never negative) on-hand.
        if source.quantity < source_quantity:
            raise RepackError(
                f'Insufficient stock: only {source.quantity} units of '
                f'{source.sku} are on hand.',
                code='insufficient_stock',
            )

        reference = _insert_with_reference_retry(
            source, target, direction, source_quantity, target_quantity, ratio,
            note, user,
        )
        repack = Repack.objects.get(reference=reference)

        # Report accurate resulting on-hand after the F() updates.
        source.refresh_from_db(fields=['quantity'])
        target.refresh_from_db(fields=['quantity'])
        return repack


def _insert_with_reference_retry(
    source, target, direction, source_quantity, target_quantity, ratio, note, user,
):
    """Create the two movements then the Repack row, retrying a rare reference
    collision.

    The movements are created before the Repack row because ``Repack`` holds
    non-null FKs to both of them (reversing the order would need nullable FKs;
    R3.1). Each attempt runs inside its own nested savepoint, so a uniqueness
    failure rolls back that attempt's partial rows and on-hand updates before the
    next attempt re-reads fresh on-hand (OQ-14). At most 3 retries, then the
    ``IntegrityError`` propagates to the view's 409 handler.

    Retries are confined to the ``Repack.reference`` unique violation (N2). A
    stock CHECK violation (concurrent stock race) or any other integrity error
    is *not* a reference collision, so it is not masked by retrying doomed
    writes — it surfaces immediately as the 409 the view reports.
    """
    for attempt in range(4):
        reference = generate_reference()
        try:
            # A savepoint per attempt: an IntegrityError here rolls back only this
            # attempt's writes while the outer transaction keeps the product locks.
            with transaction.atomic():
                _write_rows(
                    source, target, direction, source_quantity, target_quantity,
                    ratio, note, user, reference,
                )
                return reference
        except IntegrityError as exc:
            if attempt == 3 or not _is_reference_unique_violation(exc):
                raise


def _is_reference_unique_violation(exc):
    """True only when ``exc`` is a unique-constraint failure on Repack.reference.

    ``Repack.reference`` is the sole retryable collision. On PostgreSQL the
    constraint name is exposed via ``exc.__cause__.diag.constraint_name``; on
    SQLite the message names the column. Everything else (e.g. the ``stock``
    quantity CHECK violation) returns False so the caller stops retrying.
    """

    # PostgreSQL: the psycopg error carries the diagnostic constraint name.
    cause = getattr(exc, '__cause__', None)
    diag = getattr(cause, 'diag', None)
    constraint = getattr(diag, 'constraint_name', None)
    if constraint:
        return constraint == 'inventory_repack_reference_key'

    # SQLite backend: integrity messages read ``UNIQUE constraint failed:
    # inventory_repack.reference``.
    message = exc.args[0] if exc.args else ''
    return 'UNIQUE constraint failed: inventory_repack.reference' in message


def _write_rows(
    source, target, direction, source_quantity, target_quantity, ratio, note, user,
    reference,
):
    """Write the two movements and the Repack row for one reference."""
    source_old = source.quantity
    target_old = target.quantity

    repack_out_delta = -source_quantity
    repack_in_delta = target_quantity

    src_move = StockMovement.objects.create(
        product=source,
        movement_type='repack_out',
        quantity=repack_out_delta,
        cost=source.cost,
        reference=reference,
        note=f'Repack {source.sku} x{source_quantity} to {target.sku} x{target_quantity} ({ratio}/pack)',
        adjusted_by=user,
        old_on_hand=source_old,
        new_on_hand=source_old + repack_out_delta,
    )
    tgt_move = StockMovement.objects.create(
        product=target,
        movement_type='repack_in',
        quantity=repack_in_delta,
        cost=target.cost,
        reference=reference,
        note=f'Repack {target.sku} x{target_quantity} from {source.sku} x{source_quantity} ({ratio}/pack)',
        adjusted_by=user,
        old_on_hand=target_old,
        new_on_hand=target_old + repack_in_delta,
    )

    Repack.objects.create(
        reference=reference,
        location=REPACK_LOCATION,
        direction=direction,
        source_product=source,
        source_sku=source.sku,
        source_quantity=source_quantity,
        target_product=target,
        target_sku=target.sku,
        target_quantity=target_quantity,
        units_per_pack=ratio,
        performed_by=user,
        note=note,
        source_movement=src_move,
        target_movement=tgt_move,
    )
