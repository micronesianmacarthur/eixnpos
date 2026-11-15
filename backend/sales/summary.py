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
  counted, so a single corrupt row cannot break the whole report.

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


def _read_sales(window: SessionWindow) -> pd.DataFrame:
    """Load completed sales in the session window into a DataFrame."""
    from sales.models import Sale

    later = window.upper_bound
    qs = (
        Sale.objects
        .filter(
            status=Sale.STATUS_COMPLETED,
            created_at__gte=window.opened_at,
            created_at__lte=later,
        )
        .values('id', 'number', 'subtotal', 'discount_total', 'tax_total', 'total')
    )
    df = pd.DataFrame(list(qs))
    if df.empty:
        return pd.DataFrame(columns=['id', 'number', 'subtotal', 'discount_total', 'tax_total', 'total'])
    return df


def _read_lines(window: SessionWindow) -> pd.DataFrame:
    """Load sale lines (with product cost and category) into a DataFrame."""
    from sales.models import Sale, SaleLine
    later = window.upper_bound
    qs = (
        SaleLine.objects
        .filter(
            sale__status=Sale.STATUS_COMPLETED,
            sale__created_at__gte=window.opened_at,
            sale__created_at__lte=later,
        )
        .select_related('product__category')
        .values(
            'sale_id',
            'quantity',
            'unit_price',
            'discount_amount',
            'product__cost',
            'product__category__name',
        )
    )
    return pd.DataFrame(list(qs))


def _read_payments(window: SessionWindow) -> pd.DataFrame:
    """Load payments on completed sales in the session window."""
    from sales.models import Payment, Sale

    later = window.upper_bound
    qs = (
        Payment.objects
        .filter(
            sale__status=Sale.STATUS_COMPLETED,
            sale__created_at__gte=window.opened_at,
            sale__created_at__lte=later,
        )
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
            a dict reporting rows dropped for non-numeric amounts:
            ``invalid_sale_amounts``, ``invalid_line_amounts``,
            ``invalid_payment_amounts``.

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
    if not lines_df.empty:
        cogs_total = Decimal('0')
        for _, row in lines_df.iterrows():
            qty, qok = _to_decimal(row.get('quantity'))
            cost, cok = _to_decimal(row.get('product__cost'))
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
        },
        'warnings': {
            'invalid_sale_amounts': invalid_sales,
            'invalid_line_amounts': invalid_lines,
            'invalid_payment_amounts': invalid_payments,
        },
    }
