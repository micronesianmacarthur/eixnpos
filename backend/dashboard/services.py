"""Dashboard metric aggregation, implemented against the live models.

Every function here is a pure "compute the payload" helper; the DRF views in
``views.py`` are thin wrappers that parse requests and return serializable
dicts. All date boundaries are computed in the store's business-local IANA
timezone (``BusinessInfo.timezone``, default UTC) against UTC-stored instants,
per the contract primitives (§3). Monetary values are 2-dp decimal *strings*;
counts are ints.

Query discipline: each payload is produced with a small, fixed set of aggregate
queries (``Count``/``Sum`` with ``filter=``, grouped ``values``+``annotate``,
``TruncMonth`` buckets). No per-row lookups, so there is no N+1.
"""
from __future__ import annotations

import glob
import os
from datetime import date, datetime, time as dtime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, List, Optional, Tuple

from django.db.models import Count, F, Q, Sum
from django.db.models.functions import Coalesce, TruncMonth
from django.utils import timezone

from accounts.roles import has_manager_role
from business.models import BusinessInfo
from inventory.models import StockMovement
from products.models import Product
from sales.models import Payment, Refund, Sale, SaleLine
from sessions.models import Session, business_tzinfo, local_date_of

CENTS = Decimal('0.01')
TOP_LIMIT = 5
MONTHS_DEFAULT = 12
MONTHS_MIN = 1
MONTHS_MAX = 24

# Sale statuses whose payments count toward cash/invoice totals. v1 never
# reverses a refunded sale's tenders (no refund-payment rows exist), so a
# refunded sale's cash/invoice payments stay counted — the documented "cash
# refund non-adjustment" limitation (contract §6.2). sales_count/revenue still
# count completed sales only; voided and open sales never reach the drawer.
SETTLED_STATUSES = (Sale.STATUS_COMPLETED, Sale.STATUS_REFUNDED)


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #
def _money(value) -> str:
    """Render a Decimal as a fixed 2-dp string (half-up rounding)."""
    qty = (value if value is not None else Decimal('0')).quantize(
        CENTS, rounding=ROUND_HALF_UP
    )
    return f'{qty:.2f}'


def _pq(value) -> Decimal:
    """Coerce an aggregate ``None``/``Decimal`` into a 2-dp Decimal."""
    return (value or Decimal('0')).quantize(CENTS, rounding=ROUND_HALF_UP)


def _now_utc():
    return timezone.now()


def _business_day(now_utc=None) -> date:
    """Business-local calendar date of ``now_utc`` (defaults to now)."""
    return local_date_of(now_utc or _now_utc())


def _local_midnight_utc(day: date):
    """UTC instant of 00:00 business-local on ``day``."""
    tz = business_tzinfo()
    return datetime.combine(day, dtime.min, tzinfo=tz).astimezone(timezone.UTC)


def _local_iso(dt) -> str:
    """ISO-8601 with offset, in the business-local timezone."""
    return timezone.localtime(dt, business_tzinfo()).isoformat()


def _business_tz_name() -> str:
    """IANA name of the store's configured business timezone (default UTC).

    ``business_tzinfo()`` already falls back to 'UTC', so this returns the
    exact zone the server used for bucketing and local-ISO rendering — the
    value the client passes to ``Intl.DateTimeFormat`` (contract §3.3).
    """
    return str(business_tzinfo().key)


def _next_month_first(day: date) -> date:
    if day.month == 12:
        return date(day.year + 1, 1, 1)
    return date(day.year, day.month + 1, 1)


# --------------------------------------------------------------------------- #
# Daily/range KPI (contract §4.1 / §4.2, endpoints 5.1 / 5.2)
# --------------------------------------------------------------------------- #
def _range_aggregates(start_utc, end_utc) -> Dict[str, Any]:
    """KPI figures for completed sales with ``created_at`` in [start, end)."""
    sales = Sale.objects.filter(
        created_at__gte=start_utc, created_at__lt=end_utc,
    )
    agg = sales.aggregate(
        sales_count=Count('id', filter=Q(status=Sale.STATUS_COMPLETED)),
        revenue=Sum('total', filter=Q(status=Sale.STATUS_COMPLETED)),
        void_count=Count('id', filter=Q(status=Sale.STATUS_VOIDED)),
        refund_count=Count('id', filter=Q(status=Sale.STATUS_REFUNDED)),
        open_count_today=Count(
            'id',
            filter=Q(status__in=[Sale.STATUS_PENDING, Sale.STATUS_OPEN]),
        ),
    )
    payments = Payment.objects.filter(
        sale__status__in=SETTLED_STATUSES,
        sale__created_at__gte=start_utc,
        sale__created_at__lt=end_utc,
        voided=False,
    ).aggregate(
        cash_sales=Sum('amount', filter=Q(method=Payment.METHOD_CASH)),
        invoice_count=Count('id', filter=Q(method=Payment.METHOD_INVOICE)),
        invoice_total=Sum('amount', filter=Q(method=Payment.METHOD_INVOICE)),
    )
    # Reconcile the doorstep totals against what was actually given back (S4):
    # a refunded/partially-refunded cash sale's tenders were returned, so the
    # drawer keeps only the net-of-refund amount. Subtract the real Refund rows
    # (cash refunds from cash_sales, invoice refunds from invoice_total) so the
    # dashboard agrees with the session close. Voided payments were already
    # excluded above (their sale is never 'completed').
    refunds = Refund.objects.filter(
        sale__status__in=SETTLED_STATUSES,
        sale__created_at__gte=start_utc,
        sale__created_at__lt=end_utc,
    ).aggregate(
        cash=Sum('amount', filter=Q(method=Payment.METHOD_CASH)),
        invoice=Sum('amount', filter=Q(method=Payment.METHOD_INVOICE)),
    )

    sales_count = agg['sales_count'] or 0
    revenue = _pq(agg['revenue'])
    avg_basket = (revenue / sales_count) if sales_count else Decimal('0')

    return {
        'sales_count': sales_count,
        'revenue': _money(revenue),
        'cash_sales': _money(
            _pq(payments['cash_sales']) - _pq(refunds['cash'])
        ),
        'invoice_count': payments['invoice_count'] or 0,
        'invoice_total': _money(
            _pq(payments['invoice_total']) - _pq(refunds['invoice'])
        ),
        'void_count': agg['void_count'] or 0,
        'refund_count': agg['refund_count'] or 0,
        'avg_basket': _money(avg_basket),
        'open_count_today': agg['open_count_today'] or 0,
    }


def today_payload() -> Dict[str, Any]:
    """Contract §4.1 / 5.1 — KPI for the business-local 'today' window."""
    now_utc = _now_utc()
    day = _business_day(now_utc)
    start_utc = _local_midnight_utc(day)
    today = _range_aggregates(start_utc, now_utc)
    tz = business_tzinfo()
    period = {
        'scope': 'today',
        'start': datetime.combine(day, dtime.min, tzinfo=tz).isoformat(),
        'end': timezone.localtime(now_utc, tz).isoformat(),
    }
    return {
        'business_day': day.isoformat(),
        'generated_at': _local_iso(now_utc),
        'business_timezone': _business_tz_name(),
        'period': period,
        'today': today,
    }


def period_payload(start_date: date, end_date: date) -> Dict[str, Any]:
    """Contract §4.2 / 5.2 — KPI for a custom half-open [start, end) range.

    ``end_date`` is the exclusive upper boundary (``[start_of(start),
    start_of(end))``). The caller is expected to pre-validate ordering; if
    ``start_date >= end_date`` the result is an empty (all-zero) state.
    """
    start_utc = _local_midnight_utc(start_date)
    end_utc = _local_midnight_utc(end_date)
    if start_utc >= end_utc:
        aggregates = {
            'sales_count': 0,
            'revenue': '0.00',
            'cash_sales': '0.00',
            'invoice_count': 0,
            'invoice_total': '0.00',
            'void_count': 0,
            'refund_count': 0,
            'avg_basket': '0.00',
        }
    else:
        raw = _range_aggregates(start_utc, end_utc)
        aggregates = {k: raw[k] for k in raw if k != 'open_count_today'}
    return {
        'period': {
            'scope': 'range',
            'start': start_date.isoformat(),
            'end': end_date.isoformat(),
        },
        **aggregates,
    }


# --------------------------------------------------------------------------- #
# Top items (contract §4.3, endpoint 5.3)
# --------------------------------------------------------------------------- #
def _top_items_for(start_utc, end_utc) -> Tuple[int, List[Dict[str, Any]]]:
    """Top-5 products by units sold in [start, end), deterministic ordering."""
    base = SaleLine.objects.filter(
        sale__status=Sale.STATUS_COMPLETED,
        sale__created_at__gte=start_utc,
        sale__created_at__lt=end_utc,
    )
    total_items = base.values('product_id').distinct().count()
    rows = (
        base
        .values('product_id', 'product__name')
        .annotate(units_sold=Sum('quantity'), revenue=Sum('line_total'))
        .order_by('-units_sold', 'product__name')
    )
    items: List[Dict[str, Any]] = []
    for rank, row in enumerate(rows[:TOP_LIMIT], start=1):
        revenue = _pq(row['revenue'])
        units = row['units_sold'] or 0
        avg_unit_price = (revenue / units) if units else Decimal('0')
        items.append({
            'rank': rank,
            'product_id': row['product_id'],
            'product_name': row['product__name'],
            'units_sold': units,
            'revenue': _money(revenue),
            'avg_unit_price': _money(avg_unit_price),
        })
    return total_items, items


def top_payload(scope: str, start_date: Optional[date] = None,
                end_date: Optional[date] = None) -> Dict[str, Any]:
    """Contract §4.3 / 5.3 — top items for a named bucket or custom range."""
    now_utc = _now_utc()
    today = _business_day(now_utc)

    if scope == 'range' and start_date is not None and end_date is not None:
        start_utc = _local_midnight_utc(start_date)
        end_utc = _local_midnight_utc(end_date)
        resolved = 'range'
    elif scope == 'week':
        monday = today - timedelta(days=today.weekday())
        start_utc = _local_midnight_utc(monday)
        end_utc = start_utc + timedelta(days=7)
        resolved = 'week'
    elif scope == 'month':
        first = today.replace(day=1)
        start_utc = _local_midnight_utc(first)
        end_utc = _local_midnight_utc(_next_month_first(first))
        resolved = 'month'
    else:  # 'today' is the default
        start_utc = _local_midnight_utc(today)
        end_utc = now_utc
        resolved = 'today'

    total_items, items = _top_items_for(start_utc, end_utc)
    return {'scope': resolved, 'total_items': total_items, 'top_items': items}


# --------------------------------------------------------------------------- #
# Monthly / month-over-month (contract §4.4, endpoint 5.4)
# --------------------------------------------------------------------------- #
def monthly_payload(months: int) -> Dict[str, Any]:
    """Contract §4.4 / 5.4 — trailing calendar months ending at the current
    business-local month, with month-over-month deltas for settled periods.
    """
    months = max(MONTHS_MIN, min(int(months), MONTHS_MAX))
    tz = business_tzinfo()
    current_first = _business_day().replace(day=1)

    # Ordered list of trailing month-start dates, oldest -> newest.
    period_starts: List[date] = []
    cursor = current_first
    for _ in range(months):
        period_starts.append(cursor)
        cursor = _prev_month_first(cursor)
    period_starts.reverse()

    window_start_utc = _local_midnight_utc(period_starts[0])

    completed = Sale.objects.filter(
        status=Sale.STATUS_COMPLETED, created_at__gte=window_start_utc,
    )
    sales_by_month = {
        row['month'].date(): row
        for row in (
            completed
            .annotate(month=TruncMonth('created_at', tzinfo=tz))
            .values('month')
            .annotate(sale_count=Count('id'), revenue=Sum('total'))
        )
    }
    cash_by_month = {
        row['month'].date(): _pq(row['cash_sales'])
        for row in (
            Payment.objects
            .filter(
                method=Payment.METHOD_CASH,
                sale__status__in=SETTLED_STATUSES,
                sale__created_at__gte=window_start_utc,
                voided=False,
            )
            .annotate(month=TruncMonth('sale__created_at', tzinfo=tz))
            .values('month')
            .annotate(cash_sales=Sum('amount'))
        )
    }
    # Handed back out of the drawer (S4): cash refunds net against cash sales
    # so the monthly figure agrees with the range KPI and the session close.
    cash_refunded_by_month = {
        row['month'].date(): _pq(row['cash_refunded'])
        for row in (
            Refund.objects
            .filter(
                method=Payment.METHOD_CASH,
                sale__status__in=SETTLED_STATUSES,
                sale__created_at__gte=window_start_utc,
            )
            .annotate(month=TruncMonth('sale__created_at', tzinfo=tz))
            .values('month')
            .annotate(cash_refunded=Sum('amount'))
        )
    }
    for month in set(list(cash_by_month) + list(cash_refunded_by_month)):
        cash_by_month[month] = (
            cash_by_month.get(month, Decimal('0'))
            - cash_refunded_by_month.get(month, Decimal('0'))
        )

    periods: List[Dict[str, Any]] = []
    for start in period_starts:
        row = sales_by_month.get(start, {})
        sale_count = row.get('sale_count') or 0
        revenue = _pq(row.get('revenue'))
        avg_basket = (revenue / sale_count) if sale_count else Decimal('0')
        periods.append({
            'period': start.strftime('%Y-%m'),
            'label': start.strftime('%b %Y'),
            'sale_count': sale_count,
            'revenue': _money(revenue),
            'cash_sales': _money(cash_by_month.get(start, Decimal('0'))),
            'avg_basket': _money(avg_basket),
            'mom_revenue': None,
            'mom_revenue_pct': None,
        })

    # Month-over-month for every returned period EXCEPT the first (no preceding
    # month in the returned window) and the most recent / in-progress one.
    # Matches the contract's worked example (first & latest periods are null).
    for i in range(1, len(periods) - 1):
        prev_rev = Decimal(periods[i - 1]['revenue'])
        cur_rev = Decimal(periods[i]['revenue'])
        mom = cur_rev - prev_rev
        periods[i]['mom_revenue'] = _money(mom)
        if prev_rev:
            pct = (mom / abs(prev_rev)) * Decimal('100')
            periods[i]['mom_revenue_pct'] = float(
                pct.quantize(CENTS, rounding=ROUND_HALF_UP)
            )
        else:
            periods[i]['mom_revenue_pct'] = None

    latest = periods[-1]['period'] if periods else None
    return {
        'months_requested': months,
        'periods': periods,
        'latest_period': latest,
    }


def _prev_month_first(day: date) -> date:
    if day.month == 1:
        return date(day.year - 1, 12, 1)
    return date(day.year, day.month - 1, 1)


# --------------------------------------------------------------------------- #
# Current session + opener (contract §4.5, endpoint 5.5)
# --------------------------------------------------------------------------- #
def _session_cashier_names(session) -> List[str]:
    """Usernames of the register's assigned cashiers (the ``cashiers`` M2M)."""
    ordered = sorted(
        (u.username for u in session.register.cashiers.all()),
        key=lambda n: n or '',
    )
    return ordered


def _session_dict(session) -> Dict[str, Any]:
    """Serialize one open Session into its contract §4.5/§5.5 object shape.

    The object carries both the canonical fields (``opened_at_local``,
    ``opened_by``, ``cashiers``, ``register_name``) and legacy aliases
    (``started_at_local``, ``cashier``) so callers reading the pre-B2 single
    ``session`` shape keep working.
    """
    cashier = session.cashier
    opened_by = {
        'id': cashier.id,
        'username': cashier.username,
        'role': getattr(cashier, 'role', 'cashier'),
    }
    return {
        'session_id': session.id,
        'register_name': session.register.name,
        'opened_at': session.opened_at.isoformat(),
        'opened_at_local': _local_iso(session.opened_at),
        'started_at': session.opened_at.isoformat(),      # legacy alias
        'started_at_local': _local_iso(session.opened_at),  # legacy alias
        'business_day': session.business_day.isoformat(),
        'register': {
            'id': session.register.id,
            'name': session.register.name,
            'identifier': session.register.identifier,
        },
        'opened_by': opened_by,
        'cashier': opened_by,                              # legacy alias
        'cashiers': _session_cashier_names(session),
        'starting_cash': _money(session.starting_cash),
    }


def current_session_payload(user) -> Dict[str, Any]:
    """All open shifts for the business day, scoped by the caller's tier.

    Manager-admin callers (per :func:`accounts.roles.has_manager_role`) see
    every open session for the business day, across all registers, regardless
    of each register's ``cashiers`` assignment — a manager must never be told
    "No open shift" because the open shift sits on a register that does not
    list them as a cashier. Non-manager callers keep the register-resolution
    rule shared with the sessions API: only registers with an empty ``cashiers``
    M2M (accessible by everyone) or which list the caller are considered.

    Response envelope: ``open`` (bool), ``count`` (int), ``business_day`` and
    a parallel ``sessions`` array (each a serialized open session). For
    backward compatibility the first open session is ALSO emitted under the
    historical ``session`` key (shape identical to the previous single-object
    response), or ``{'open': False}`` when none is open.
    """
    day = _business_day()
    manager = has_manager_role(user)

    qs = Session.objects.filter(
        business_day=day, status=Session.STATUS_OPEN,
    ).select_related(
        'register', 'cashier',
    ).prefetch_related(
        'register__cashiers',
    ).order_by('register__id', 'opened_at')

    if not manager:
        # Register-access rule shared with sessions/views.py: a register is
        # accessible when its cashiers M2M is empty (everyone) or lists the
        # caller. One query — a plain filter, no per-register lookups (S4).
        qs = qs.filter(
            Q(register__cashiers__isnull=True) | Q(register__cashiers=user),
        ).distinct()

    sessions = [_session_dict(s) for s in qs]
    if not sessions:
        return {
            'open': False,
            'count': 0,
            'business_day': day.isoformat(),
            'sessions': [],
            'session': {'open': False},
        }
    return {
        'open': True,
        'count': len(sessions),
        'business_day': day.isoformat(),
        'sessions': sessions,
        # Backward-compatible single-session accessor (first open session).
        'session': sessions[0],
    }


# --------------------------------------------------------------------------- #
# Low stock (contract §4.6, endpoint 5.6)
# --------------------------------------------------------------------------- #
def low_stock_payload() -> Dict[str, Any]:
    """Active stock-only products whose on-hand quantity is at/below threshold.

    quantity_on_hand is the aggregate of the StockMovement ledger in a single
    annotated query (0 when a product has no movements). Non-stock products are
    excluded by construction. One SQL query, no full-ledger/all-product scan
    (S3).
    """
    products = (
        Product.objects
        .filter(active=True, non_stock=False)
        .annotate(qty=Coalesce(Sum('movements__quantity'), 0))
        .filter(qty__lte=F('min_stock'))
        .select_related('category')
        .order_by('qty')  # ascending on-hand: negative net ledger sorts first
    )
    items: List[Dict[str, Any]] = []
    for product in products:
        item = {
            'product_id': product.id,
            'product_name': product.name,
            'quantity_on_hand': product.qty,
            'min_stock': product.min_stock,
        }
        if product.sku is not None:
            item['sku'] = product.sku
        if product.category is not None:
            item['category'] = product.category.name
        items.append(item)
    return {
        'generated_at': _local_iso(_now_utc()),
        'threshold_source': {
            'field': 'min_stock',
            'rule': 'quantity <= min_stock',
            'non_stock_excluded': True,
        },
        'low_stock_count': len(items),
        'items': items,
    }


# --------------------------------------------------------------------------- #
# Composite landing payload (contract §5.7)
# --------------------------------------------------------------------------- #
def composite_payload(user) -> Dict[str, Any]:
    """One first-paint payload: today KPIs + current session + low stock.

    Fields are verbatim copies of the individual payloads, mapped into the
    ``session`` and ``inventory`` keys. No per-KPI HTTP round-trip is needed.
    ``business_timezone`` rides on ``today_payload`` so the single composite
    call carries the IANA zone the client needs for all localized formatting.
    """
    payload = today_payload()
    payload['session'] = current_session_payload(user)
    payload['inventory'] = low_stock_payload()
    return payload


# --------------------------------------------------------------------------- #
# Last backup status (contract §4.7 / endpoint §5.8)
# --------------------------------------------------------------------------- #
def backup_payload() -> Dict[str, Any]:
    """Contract §4.7 / 5.8 — last-backup status and quick-action readiness.

    ``backup_path_configured`` reflects whether ``BusinessInfo.backup_path`` is
    set. When it is, we look for the newest ``backup_*.json`` dump the backup
    service writes there (``backups.utils.run_backup`` names them
    ``backup_<ts>.json``): ``last_backup_at`` is that file's write instant as
    ISO-8601 UTC, ``last_backup_at_local`` the same instant in the business
    IANA timezone (offset embedded) so a client can render the store's own
    clock, and ``last_backup_path`` its full path. All three datetime/path
    fields are ``null`` when no path is configured or no backup exists yet.
    """
    info = BusinessInfo.get()
    backup_path = (info.backup_path or '').strip()
    if not backup_path:
        return {
            'backup_path_configured': False,
            'last_backup_at': None,
            'last_backup_at_local': None,
            'last_backup_path': None,
        }

    files = []
    for candidate in glob.glob(os.path.join(backup_path, 'backup_*.json')):
        try:
            files.append((os.path.getmtime(candidate), candidate))
        except OSError:
            continue
    newest = max(files, default=None)

    last_utc = None
    if newest is not None:
        last_utc = datetime.fromtimestamp(newest[0], tz=timezone.UTC)

    return {
        'backup_path_configured': True,
        'last_backup_at': last_utc.isoformat() if last_utc else None,
        'last_backup_at_local': (
            timezone.localtime(last_utc, business_tzinfo()).isoformat()
            if last_utc else None
        ),
        'last_backup_path': newest[1] if newest is not None else None,
    }

