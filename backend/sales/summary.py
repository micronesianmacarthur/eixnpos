"""
Session summary computation using pandas.

Provides :func:`compute_session_summary`, a reusable data-processing entry
point that reads a session's transactions (completed sales, sale lines and
payments that fall within the session's time window) into pandas DataFrames
and derives the figures the Close-flow summary and the Z-report need:
revenue (net sales), discounts, tax, cost of goods sold and resulting
profit/loss, plus breakdowns by payment method and category.

Edge cases are handled deliberately:

* an empty session (no completed sales in the window) produces zeroed totals
  rather than raising;
* non-numeric / missing amount values are coerced to ``Decimal('0')`` and
  counted, so a single corrupt row cannot break the whole report;
* cost of goods is computed from each line's ``unit_cost`` (the cost snapshot
  taken at sale time), so later edits to a product's current cost never
  restate a closed period. Legacy lines with no snapshot contribute zero to
  COGS and are reported via ``missing_cost_lines``.

The returned structure is JSON-serializable (monetary figures as fixed 2-dp
strings, counts as ints) so it can be handed straight to the summary UI or
the Z-report PDF renderer without further conversion.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any, Dict, Optional

import pandas as pd

if TYPE_CHECKING:
    from sessions.models import Session

from django.db.models import Q

from sales.cash import compute_cash_sales

CENTS = Decimal('0.01')


def _to_decimal(value: Any) -> tuple[Decimal, bool]:
    """Convert ``value`` to a Decimal, returning ``(amount, valid)``.

    ``None`` and anything that is not a valid decimal token produce
    ``(Decimal('0'), False)``; the caller can surface the mask as a count.
    NaN / infinity are treated as invalid so they never poison totals.
    """
    if value is None:
        return Decimal('0'), False
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal('0'), False
    if not amount.is_finite():
        return Decimal('0'), False
    return amount, True


def _money(value: Decimal) -> str:
    """Render a Decimal as a fixed 2-dp string for UI/PDF rendering."""
    return f"{value.quantize(CENTS):.2f}"


def _finite(value: Decimal) -> bool:
    """True unless the Decimal is NaN or an infinity."""
    return value.is_finite()


@dataclass
class SessionWindow:
    """Time window a session's transactions must fall within."""
    session_id: int
    cashier_name: str
    status: str
    opened_at: datetime
    closed_at: Optional[datetime]

    @property
    def upper_bound(self) -> datetime:
        """Upper bound of the window: close time, or now for open sessions."""
        if self.closed_at is not None:
            return self.closed_at
        from django.utils import timezone as dj_timezone
        return dj_timezone.now()


def _session_sales_filter(window: SessionWindow, prefix: str = '') -> Q:
    """Q filter selecting completed sales that belong to THIS session.

    A sale is included when it is explicitly linked to the session
    (``session_id`` matches), OR — the legacy fallback — when it has no
    session link at all and was created inside the session's time window.
    The fallback keeps pre-migration sales (``session IS NULL``) reporting
    under the register/day session whose time window contains them; a sale
    that IS attributed to a specific session is never counted by a sibling or
    overlapping session, no matter how its timestamps align.

    ``prefix`` is ``'sale__'`` for queries that reach a Sale through a related
    model (lines, payments), ``''`` when querying Sale directly.
    """
    linked = Q(**{f'{prefix}session_id': window.session_id})
    legacy = Q(**{
        f'{prefix}session__isnull': True,
        f'{prefix}created_at__gte': window.opened_at,
        f'{prefix}created_at__lte': window.upper_bound,
    })
    return linked | legacy


def _read_sales(window: SessionWindow) -> pd.DataFrame:
    """Load completed sales for THIS session into a DataFrame."""
    from sales.models import Sale

    qs = (
        Sale.objects
        .filter(status=Sale.STATUS_COMPLETED)
        .filter(_session_sales_filter(window))
        .values('id', 'number', 'subtotal', 'discount_total', 'tax_total', 'total')
    )
    df = pd.DataFrame(list(qs))
    if df.empty:
        return pd.DataFrame(columns=['id', 'number', 'subtotal', 'discount_total', 'tax_total', 'total'])
    return df


def _read_lines(window: SessionWindow) -> pd.DataFrame:
    """Load sale lines (with cost snapshot, product cost, category) into a DataFrame.

    ``unit_cost`` is the sale-time cost snapshot captured when the line was
    created; ``product__cost`` is the product's *current* cost, kept only as the
    fallback/report reference for legacy rows that predate the snapshot
    (their ``unit_cost`` is null). COGS must be derived from ``unit_cost`` so
    later edits to ``Product.cost`` never restate closed periods.
    """
    from sales.models import Sale, SaleLine
    qs = (
        SaleLine.objects
        .filter(sale__status=Sale.STATUS_COMPLETED)
        .filter(_session_sales_filter(window, prefix='sale__'))
        .select_related('product__category')
        .values(
            'sale_id',
            'quantity',
            'unit_price',
            'discount_amount',
            'unit_cost',
            'product__cost',
            'product__category__name',
        )
    )
    return pd.DataFrame(list(qs))


def _read_payments(window: SessionWindow) -> pd.DataFrame:
    """Load payments on completed sales for THIS session."""
    from sales.models import Payment, Sale

    qs = (
        Payment.objects
        .filter(sale__status=Sale.STATUS_COMPLETED)
        .filter(_session_sales_filter(window, prefix='sale__'))
        .values('method', 'amount')
    )
    return pd.DataFrame(list(qs))


def _sum_column(df: pd.DataFrame, column: str) -> tuple[Decimal, int]:
    """Sum a monetary column into a Decimal, tolerating non-numeric values.

    Returns ``(total, invalid_count)``. The column is coerced through
    ``_to_decimal`` so a corrupt value contributes zero but is counted.
    """
    if df is None or df.empty or column not in df.columns:
        return Decimal('0'), 0
    total = Decimal('0')
    invalid = 0
    for raw in df[column].tolist():
        amount, valid = _to_decimal(raw)
        if not valid:
            invalid += 1
        total += amount
    return total, invalid


def _by_method(payments: pd.DataFrame) -> Dict[str, str]:
    """Payment-total breakdown by method (cash/card/split/invoice)."""
    result: Dict[str, Decimal] = {}
    if payments.empty:
        return {}
    for method, group in payments.groupby('method', dropna=False):
        total = Decimal('0')
        for raw in group['amount'].tolist():
            amount, _valid = _to_decimal(raw)
            total += amount
        result[str(method) or 'unknown'] = total
    return {k: _money(v) for k, v in result.items()}


def _by_category(lines: pd.DataFrame) -> Dict[str, str]:
    """Revenue (line total after discount) breakdown by product category."""
    result: Dict[str, Decimal] = {}
    for _, row in lines.iterrows():
        qty, _ok = _to_decimal(row.get('quantity'))
        unit, _u = _to_decimal(row.get('unit_price'))
        disc, _d = _to_decimal(row.get('discount_amount'))
        line_total = (qty * unit) - disc
        if line_total.is_nan() or not line_total.is_finite():
            line_total = Decimal('0')
        category = row.get('product__category__name') or 'Uncategorized'
        result[category] = result.get(category, Decimal('0')) + line_total
    return {k: _money(v) for k, v in result.items()}


def compute_session_summary(session_id: int) -> Dict[str, Any]:
    """Compute revenue and profit/loss for a session using pandas.

    Args:
        session_id: id of the Session to summarise.

    Returns:
        A JSON-serializable dict with three keys:

        ``session``
            metadata: ``id``, ``cashier``, ``status``, ``opened_at``,
            ``closed_at`` (ISO strings; ``closed_at`` may be null).
        ``summary``
            computed figures. ``revenue`` (net sales), ``gross_sales``,
            ``discounts``, ``tax_total``, ``cost_of_goods``, ``profit_loss``,
            ``profit_margin`` (ratio or ``None`` when revenue is zero),
            and counts ``sale_count``, ``item_count``, plus ``by_payment``
            and ``by_category`` breakdowns. Monetary fields are 2-dp strings.
        ``warnings``
            a dict reporting rows dropped or excluded from the aggregates:
            ``invalid_sale_amounts``, ``invalid_line_amounts``,
            ``invalid_payment_amounts``, and ``missing_cost_lines`` (sale
            lines with no sale-time cost snapshot whose unknown cost was
            excluded from COGS rather than restated from the product's
            current cost).

    Raises:
        Session.DoesNotExist if no Session matches ``session_id``.
    """
    from sessions.models import Session

    session = Session.objects.get(pk=session_id)
    window = SessionWindow(
        session_id=session.id,
        cashier_name=session.cashier.get_full_name() or session.cashier.username,
        status=session.status,
        opened_at=session.opened_at,
        closed_at=session.closed_at,
    )

    sales_df = _read_sales(window)
    lines_df = _read_lines(window)
    payments_df = _read_payments(window)

    gross_sales, invalid_sales = _sum_column(sales_df, 'subtotal')
    discounts, _sa = _sum_column(sales_df, 'discount_total')
    tax_total, _sb = _sum_column(sales_df, 'tax_total')
    revenue, _sc = _sum_column(sales_df, 'total')

    cogs = Decimal('0')
    invalid_lines = 0
    missing_cost_lines = 0
    if not lines_df.empty:
        cogs_total = Decimal('0')
        for _, row in lines_df.iterrows():
            qty, qok = _to_decimal(row.get('quantity'))
            unit_cost = row.get('unit_cost')
            if unit_cost is None:
                # No sale-time snapshot (legacy pre-migration row). Per
                # accounting policy, exclude the line's unknown cost from COGS
                # and report it — never restate a closed period from the
                # product's *current* cost.
                missing_cost_lines += 1
                if not qok:
                    invalid_lines += 1
                continue
            cost, cok = _to_decimal(unit_cost)
            if not (qok and cok):
                invalid_lines += 1
            elif _finite(qty) and _finite(cost):
                cogs_total += qty * cost
            else:
                invalid_lines += 1
        cogs = cogs_total

    invalid_payments = 0
    if not payments_df.empty:
        for raw in payments_df['amount'].tolist():
            _amt, valid = _to_decimal(raw)
            if not valid:
                invalid_payments += 1

    # Pandas-based aggregates (kept for the calculation path + testability).
    sale_count = int(len(sales_df.index))
    item_count = int(lines_df['quantity'].sum(skipna=True)) if not lines_df.empty else 0

    profit_loss = revenue - cogs
    margin = (profit_loss / revenue) if revenue > 0 else None

    # Canonical cash figures shared with shift close and the dashboard, so the
    # Z-summary reports the identical gross/refunds/net/by_tender as those two
    # surfaces must. ``by_tender`` is distinct from ``by_payment`` (kept below):
    # ``by_payment`` is the completed-sales-only method breakdown, while
    # ``by_tender`` reflects the shared convention (refunded sales net to zero
    # retained cash) that shift close / dashboard read.
    cash = compute_cash_sales(session)

    return {
        'session': {
            'id': session.id,
            'cashier': window.cashier_name,
            'status': window.status,
            'opened_at': window.opened_at.isoformat(),
            'closed_at': window.closed_at.isoformat() if window.closed_at else None,
        },
        'summary': {
            'sale_count': sale_count,
            'item_count': item_count,
            'gross_sales': _money(gross_sales),
            'discounts': _money(discounts),
            'tax_total': _money(tax_total),
            'revenue': _money(revenue),
            'cost_of_goods': _money(cogs),
            'profit_loss': _money(profit_loss),
            'profit_margin': round(float(margin), 4) if margin is not None else None,
            'by_payment': _by_method(payments_df),
            'by_category': _by_category(lines_df),
            'by_tender': {k: _money(v) for k, v in cash['by_tender'].items()},
            'gross': _money(cash['gross']),
            'refunds': _money(cash['refunds']),
            'net': _money(cash['net']),
        },
        'warnings': {
            'invalid_sale_amounts': invalid_sales,
            'invalid_line_amounts': invalid_lines,
            'invalid_payment_amounts': invalid_payments,
            'missing_cost_lines': missing_cost_lines,
        },
    }
