"""
Canonical cash-sales calculation shared by shift close, the Z-summary, and the
dashboard so all three surfaces report identical figures for the same shift.

This is the single source of truth for turning a shift's sales into cash-in,
cash-out and the expected drawer amount. Before this module existed,
``sessions/views.py`` (close ``expected_cash``), ``sales/summary.py``
(Z-summary revenue) and ``dashboard/services.py`` (``cash_sales``) each
re-derived cash their own way and disagreed: a refunded cash sale counted as
cash-in on the dashboard but not on close/summary (the "0.00 vs 10.00" bug),
and the cash portion of a split tender was silently dropped by every surface.

Sign convention (the core rule)
------------------------------

* ``gross``   : total of *completed* sales (money taken in that stays in the
                drawer).
* ``refunds`` : total of *refunded* sales (a positive number; money sent back
                out to customers).
* ``net``     : ``gross - refunds``.
* ``by_tender`` : per-tender cash *retained* in the drawer. A completed sale
                contributes ``+amount`` per tender; a refunded sale contributes
                ``0`` per tender — the money came in while the sale was
                completed and went back out on the refund, leaving no retained
                cash. This is the convention that makes drawer reconciliation
                correct: ``expected_cash = starting_cash + by_tender['cash']``,
                so a shift with only a refunded sale reconciles to its starting
                cash, not to ``starting_cash - amount``.

Split tenders
-------------

Each ``Payment`` row attributes its full ``amount`` to its own ``method``
(cash, card, ...), so a ``$10`` sale paid ``$4`` cash + ``$6`` card (two rows)
counts ``$4`` toward ``by_tender['cash']`` and ``$6`` toward
``by_tender['card']``. "Counted in full, broken out by tender." A single
``method='split'`` Payment row may carry an optional ``tender_breakdown``
JSONField (a ``{tender_type: amount}`` map); when present it is decomposed,
otherwise the row's full ``amount`` lands under the literal ``'split'`` key
(the documented-known gap for legacy split rows that never recorded what they
contained).

Rounding
--------

All money is Python ``Decimal`` quantized to 2dp with ``ROUND_HALF_UP``; the
returned values are ``Decimal`` (each caller renders them as strings the way
its API already does). No ``float`` anywhere.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
from typing import Any, Dict, Tuple, Union

from django.utils import timezone

CENTS = Decimal("0.01")
ROUNDING = ROUND_HALF_UP

Shift = Union["Any", Tuple[datetime, datetime]]  # a Session OR a (start,end) UTC window


def _q(value) -> Decimal:
    """Coerce to a 2dp Decimal (ROUND_HALF_UP), tolerating None/non-numeric."""
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal("0").quantize(CENTS, rounding=ROUNDING)
    if not amount.is_finite():
        return Decimal("0").quantize(CENTS, rounding=ROUNDING)
    return amount.quantize(CENTS, rounding=ROUNDING)


def _payment_breakdown(payment, amount: Decimal) -> Dict[str, Decimal]:
    """Decompose one Payment row into ``{tender_type: Decimal}``.

    A row whose ``method`` is not ``split`` (the normal case, including the
    multi-row representation of a split tender) attributes its full ``amount``
    to its own ``method``. A ``method='split'`` row with a ``tender_breakdown``
    map is decomposed per tender; a ``split`` row without one falls back to the
    literal ``'split'`` key (documented-known gap — no decomposition to read).
    """
    method = getattr(payment, "method", None)
    breakdown = getattr(payment, "tender_breakdown", None)
    if method == "split" and isinstance(breakdown, dict) and breakdown:
        return {str(tender): _q(raw) for tender, raw in breakdown.items()}
    return {method: amount}


def _window_and_scope(shift: Shift) -> Tuple[datetime, datetime, bool, Any]:
    """Resolve a shift into ``(start, end, is_session, session_id)``.

    A ``Session`` scopes to sales linked to it (or NULL-session legacy sales
    created inside the session's time window) and ends at ``closed_at``, or
    *now* for an open shift. A ``(start, end)`` tuple is a pure UTC time window
    (all sales whose ``created_at`` falls in ``[start, end)``).

    The upper bound is half-open (``created_at < end``) so a sale landing
    exactly on a boundary is never double-counted by adjacent windows.
    """
    if isinstance(shift, tuple):
        start, end = shift
        return start, end, False, None
    end = shift.closed_at or timezone.now()
    return shift.opened_at, end, True, shift.id


def compute_cash_sales(shift: Shift) -> Dict[str, Any]:
    """Canonical cash-sales figures for a shift or a UTC time window.

    Args:
        shift: a ``Session`` (shift-close / Z-summary scope) or a
            ``(start_utc, end_utc)`` window (dashboard today/period/monthly).

    Returns:
        {
            "gross": Decimal,      # total of COMPLETED sales
            "refunds": Decimal,    # total of REFUNDED sales (positive)
            "net": Decimal,        # gross - refunds
            "by_tender": Dict[tender_type, Decimal],  # cash retained per tender
        }

    ``by_tender`` contains only tender types with a non-zero retained amount;
    read it with ``.get('cash', Decimal('0'))`` for the cash figure.
    """
    from django.db.models import Q

    from sales.models import Sale

    start, end, is_session, session_id = _window_and_scope(shift)

    scope = Q(session_id=session_id) | Q(
        session__isnull=True, created_at__gte=start, created_at__lt=end,
    ) if is_session else Q(created_at__gte=start, created_at__lt=end)

    settled = (
        Sale.objects
        .filter(scope)
        .filter(status__in=(Sale.STATUS_COMPLETED, Sale.STATUS_REFUNDED))
        .prefetch_related("payments")
    )

    gross = Decimal("0")
    refunds = Decimal("0")
    by_tender: Dict[str, Decimal] = {}

    for sale in settled:
        if sale.status == Sale.STATUS_COMPLETED:
            gross += _q(sale.total)
            for payment in sale.payments.all():
                for tender, amount in _payment_breakdown(payment, _q(payment.amount)).items():
                    by_tender[tender] = by_tender.get(tender, Decimal("0")) + amount
        else:  # refunded — net cash retained is zero; the refund is surfaced below.
            refunds += _q(sale.total)

    gross = _q(gross)
    refunds = _q(refunds)
    by_tender = {tender: _q(total) for tender, total in by_tender.items() if total != 0}

    return {
        "gross": gross,
        "refunds": refunds,
        "net": _q(gross - refunds),
        "by_tender": by_tender,
    }
