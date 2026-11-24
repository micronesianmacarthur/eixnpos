"""QE regression tests for the dashboard aggregation service.

Focus areas the primary suite (dashboard/tests.py) leaves thin:

* business-local timezone boundaries — a sale one second before local
  midnight belongs to *yesterday's* numbers, not today's, and top-item
  week/month scopes must respect business-local calendar boundaries.
* payment-method splitting — one sale may carry cash, card, and invoice
  payments simultaneously; cash/invoice KPIs must reflect payment rows,
  not sale totals, while revenue counts the sale exactly once.
* month-over-month arithmetic — declines must produce negative signed
  values, not silently clamp to zero.
* low-stock threshold edge — quantity exactly equal to min_stock is
  "at threshold" and must be reported.

These tests fail if any manager dashboard question could be answered
wrongly by the aggregation (misleading data), per the acceptance criteria.
"""
from datetime import date, datetime, timedelta
from decimal import Decimal
from unittest import mock

from django.test import TestCase, override_settings
from django.utils import timezone

from business.models import BusinessInfo
from inventory.models import StockMovement
from products.models import Product
from sales.models import Payment, Sale, SaleLine
from sessions.models import Register, Session

from dashboard import services
from dashboard.tests import DashboardBase, _first_of_month_ago

UTC = timezone.UTC


def _aware(*args):
    """Build a UTC-aware datetime."""
    return datetime(*args, tzinfo=UTC)


class TimezoneBoundaryTests(DashboardBase):
    """A business day starts at local midnight, not UTC midnight."""

    @override_settings(TIME_ZONE='UTC')
    def test_sale_before_local_midnight_excluded_from_next_day(self):
        info = BusinessInfo.get()
        info.timezone = 'America/Los_Angeles'
        info.save(update_fields=['timezone'])

        # Sale at 2026-11-23 23:30 LA == 2026-11-24 07:30 UTC.
        self.make_sale(
            Decimal('75.00'),
            created_at=_aware(2026, 11, 24, 7, 30),
            method='cash',
        )
        # "Now" is 2026-11-24 00:30 LA == 08:30 UTC -> business day Nov 24.
        with mock.patch(
            'dashboard.services._now_utc',
            return_value=_aware(2026, 11, 24, 8, 30),
        ):
            payload = services.today_payload()

        self.assertEqual(payload['business_day'], '2026-11-24')
        # The 23:30-local sale is yesterday's business day: excluded.
        self.assertEqual(payload['today']['sales_count'], 0)
        self.assertEqual(payload['today']['revenue'], '0.00')
        self.assertEqual(payload['today']['cash_sales'], '0.00')

    @override_settings(TIME_ZONE='UTC')
    def test_sale_after_local_midnight_included_in_new_day(self):
        info = BusinessInfo.get()
        info.timezone = 'America/Los_Angeles'
        info.save(update_fields=['timezone'])

        # Sale at 2026-11-24 00:15 LA == 08:15 UTC.
        self.make_sale(
            Decimal('75.00'),
            created_at=_aware(2026, 11, 24, 8, 15),
            method='cash',
        )
        with mock.patch(
            'dashboard.services._now_utc',
            return_value=_aware(2026, 11, 24, 8, 30),
        ):
            payload = services.today_payload()

        self.assertEqual(payload['business_day'], '2026-11-24')
        self.assertEqual(payload['today']['sales_count'], 1)
        self.assertEqual(payload['today']['revenue'], '75.00')

    @override_settings(TIME_ZONE='UTC')
    def test_top_items_today_scope_uses_business_day(self):
        info = BusinessInfo.get()
        info.timezone = 'America/Los_Angeles'
        info.save(update_fields=['timezone'])

        yesterday = self.make_product('YesterdayItem')
        # Sold 2026-11-23 20:00 LA == 2026-11-24 04:00 UTC.
        self.make_sale(
            Decimal('20.00'),
            created_at=_aware(2026, 11, 24, 4, 0),
            lines=[(yesterday, 4, Decimal('20.00'))],
        )
        with mock.patch(
            'dashboard.services._now_utc',
            return_value=_aware(2026, 11, 24, 8, 30),
        ):
            data = services.top_payload('today')

        self.assertEqual(data['top_items'], [])

    @override_settings(TIME_ZONE='UTC')
    def test_week_scope_starts_on_business_local_monday(self):
        info = BusinessInfo.get()
        info.timezone = 'America/Los_Angeles'
        info.save(update_fields=['timezone'])

        # 2026-11-24 (LA) is a Tuesday. Last Sunday is 2026-11-22.
        sunday = self.make_product('SundayItem')
        monday = self.make_product('MondayItem')
        # Sunday 2026-11-22 15:00 LA == 22:00 UTC (previous week).
        self.make_sale(
            Decimal('10.00'),
            created_at=_aware(2026, 11, 22, 22, 0),
            lines=[(sunday, 2, Decimal('10.00'))],
        )
        # Monday 2026-11-23 09:00 LA == 17:00 UTC (this week).
        self.make_sale(
            Decimal('12.00'),
            created_at=_aware(2026, 11, 23, 17, 0),
            lines=[(monday, 3, Decimal('12.00'))],
        )
        with mock.patch(
            'dashboard.services._now_utc',
            return_value=_aware(2026, 11, 24, 18, 0),  # Tue 10:00 LA
        ):
            data = services.top_payload('week')

        names = [row['product_name'] for row in data['top_items']]
        self.assertEqual(names, ['MondayItem'])
        self.assertEqual(data['top_items'][0]['units_sold'], 3)

    @override_settings(TIME_ZONE='UTC')
    def test_month_scope_excludes_previous_month_tail(self):
        info = BusinessInfo.get()
        info.timezone = 'America/Los_Angeles'
        info.save(update_fields=['timezone'])

        oct_tail = self.make_product('OctTail')
        nov_item = self.make_product('NovItem')
        # 2026-10-31 20:00 LA == 2026-11-01 03:00 UTC (still October locally).
        self.make_sale(
            Decimal('9.00'),
            created_at=_aware(2026, 11, 1, 3, 0),
            lines=[(oct_tail, 9, Decimal('9.00'))],
        )
        # 2026-11-02 09:00 LA == 16:00 UTC (November locally).
        self.make_sale(
            Decimal('5.00'),
            created_at=_aware(2026, 11, 2, 16, 0),
            lines=[(nov_item, 5, Decimal('5.00'))],
        )
        with mock.patch(
            'dashboard.services._now_utc',
            return_value=_aware(2026, 11, 24, 18, 0),
        ):
            data = services.top_payload('month')

        names = [row['product_name'] for row in data['top_items']]
        self.assertEqual(names, ['NovItem'])
        self.assertEqual(data['scope'], 'month')


class PaymentSplitTests(DashboardBase):
    """Cash/invoice KPIs count payment rows, not sale totals."""

    def test_mixed_tender_sale_counts_each_method_once(self):
        product = self.make_product('Widget')
        sale = self.make_sale(
            Decimal('100.00'),
            method=None,
            lines=[(product, 10, Decimal('100.00'))],
        )
        Payment.objects.create(sale=sale, method=Payment.METHOD_CASH, amount=Decimal('40.00'))
        Payment.objects.create(sale=sale, method=Payment.METHOD_CARD, amount=Decimal('35.00'))
        Payment.objects.create(sale=sale, method=Payment.METHOD_INVOICE, amount=Decimal('25.00'))

        self.client.force_authenticate(self.manager)
        data = self.client.get('/api/dashboard/sales/today/').json()['today']

        # Revenue counts the sale exactly once, regardless of tender split.
        self.assertEqual(data['sales_count'], 1)
        self.assertEqual(data['revenue'], '100.00')
        # Cash is the cash payment row only — NOT the sale total.
        self.assertEqual(data['cash_sales'], '40.00')
        self.assertEqual(data['invoice_count'], 1)
        self.assertEqual(data['invoice_total'], '25.00')

    def test_card_only_sale_keeps_cash_and_invoice_at_zero(self):
        self.make_sale(Decimal('50.00'), method=Payment.METHOD_CARD)
        self.client.force_authenticate(self.manager)
        data = self.client.get('/api/dashboard/sales/today/').json()['today']
        self.assertEqual(data['revenue'], '50.00')
        self.assertEqual(data['cash_sales'], '0.00')
        self.assertEqual(data['invoice_count'], 0)
        self.assertEqual(data['invoice_total'], '0.00')

    def test_unpaid_completed_sale_still_counts_revenue(self):
        # A completed sale with no payment row yet: revenue counts it, the
        # drawer does not. Mislabelling it as cash would overstate the drawer.
        self.make_sale(Decimal('30.00'), method=None)
        self.client.force_authenticate(self.manager)
        data = self.client.get('/api/dashboard/sales/today/').json()['today']
        self.assertEqual(data['sales_count'], 1)
        self.assertEqual(data['revenue'], '30.00')
        self.assertEqual(data['cash_sales'], '0.00')


class MonthOverMonthTests(DashboardBase):
    def _sale_in_month(self, total, month_first):
        return self.make_sale(
            Decimal(str(total)),
            created_at=timezone.make_aware(
                datetime(month_first.year, month_first.month, 10, 12, 0)
            ),
        )

    def test_revenue_decline_reports_negative_mom(self):
        two_back = _first_of_month_ago(self.today, 2)   # month A
        one_back = _first_of_month_ago(self.today, 1)   # month B (decline)

        self._sale_in_month(100, two_back)   # month A: 100
        self._sale_in_month(50, one_back)    # month B: 50 (a decline)

        self.client.force_authenticate(self.manager)
        data = self.client.get('/api/dashboard/sales/monthly/?months=3').json()
        by_period = {p['period']: p for p in data['periods']}

        b = by_period[one_back.strftime('%Y-%m')]
        self.assertEqual(b['revenue'], '50.00')
        self.assertEqual(b['mom_revenue'], '-50.00')
        self.assertEqual(b['mom_revenue_pct'], -50.0)

    def test_all_zero_window_is_zero_with_null_mom(self):
        self.client.force_authenticate(self.manager)
        data = self.client.get('/api/dashboard/sales/monthly/?months=6').json()
        self.assertEqual(len(data['periods']), 6)
        for period in data['periods']:
            self.assertEqual(period['revenue'], '0.00')
            self.assertEqual(period['cash_sales'], '0.00')
            self.assertEqual(period['sale_count'], 0)
            self.assertEqual(period['avg_basket'], '0.00')

    def test_current_partial_month_counts_in_progress_sales(self):
        # A completed sale "now" lands in the current (in-progress) month.
        self.make_sale(Decimal('42.00'))
        self.client.force_authenticate(self.manager)
        data = self.client.get('/api/dashboard/sales/monthly/?months=3').json()
        current = data['periods'][-1]
        self.assertEqual(current['period'], self.today.strftime('%Y-%m'))
        self.assertEqual(current['revenue'], '42.00')
        # In-progress month has no MoM (nothing complete to compare yet).
        self.assertIsNone(current['mom_revenue'])


class LowStockBoundaryTests(DashboardBase):
    def test_exact_threshold_quantity_is_reported(self):
        exact = self.make_product('Exact', min_stock=4)
        StockMovement.objects.create(
            product=exact, movement_type='receive', quantity=4)
        above = self.make_product('Above', min_stock=4)
        StockMovement.objects.create(
            product=above, movement_type='receive', quantity=5)

        self.client.force_authenticate(self.manager)
        data = self.client.get('/api/dashboard/inventory/low-stock/').json()
        names = {i['product_name'] for i in data['items']}
        self.assertIn('Exact', names)    # qty == min_stock is at-threshold
        self.assertNotIn('Above', names)

    def test_net_ledger_negative_is_lowest_severity(self):
        # Negative net on-hand (over-sold ledger) sorts first and is reported.
        drained = self.make_product('Drained', min_stock=2)
        StockMovement.objects.create(
            product=drained, movement_type='receive', quantity=3)
        StockMovement.objects.create(
            product=drained, movement_type='sale', quantity=-5)
        healthy = self.make_product('Healthy', min_stock=2)
        StockMovement.objects.create(
            product=healthy, movement_type='receive', quantity=10)

        self.client.force_authenticate(self.manager)
        data = self.client.get('/api/dashboard/inventory/low-stock/').json()
        self.assertEqual(data['items'][0]['product_name'], 'Drained')
        self.assertEqual(data['items'][0]['quantity_on_hand'], -2)
        self.assertNotIn(
            'Healthy', [i['product_name'] for i in data['items']])


class LowStockQueryCountTests(DashboardBase):
    """S3: low-stock is a single annotated query — no full-ledger/all-product scan.

    The previous implementation grouped the entire StockMovement ledger and
    then iterated every product in Python. The annotated rewrite must issue a
    small constant number of queries regardless of how many products/movements
    exist.
    """

    @override_settings(TIME_ZONE='UTC')
    def test_query_count_is_constant_regardless_of_catalog_size(self):
        from django.test.utils import CaptureQueriesContext
        from django.db import connection

        # Warm the app once so BusinessInfo.get() (uncached get_or_create) and
        # connection warmup don't add one-off queries to either measurement.
        services.low_stock_payload()

        def count_queries():
            with CaptureQueriesContext(connection) as ctx:
                services.low_stock_payload()
            return len(ctx.captured_queries)

        baseline = count_queries()

        # Add many stock products + movements; the query count must not grow.
        for i in range(10):
            p = self.make_product(f'P{i}', min_stock=5)
            StockMovement.objects.create(
                product=p, movement_type='receive', quantity=i)

        self.assertEqual(count_queries(), baseline)


class SessionTimezoneTests(DashboardBase):
    @override_settings(TIME_ZONE='UTC')
    def test_session_opened_before_local_midnight_reports_local_day(self):
        info = BusinessInfo.get()
        info.timezone = 'America/Los_Angeles'
        info.save(update_fields=['timezone'])

        session = Session.objects.create(
            cashier=self.cashier, starting_cash=Decimal('100.00'),
            register=self.register, business_day=date(2026, 11, 23),
        )
        # Opened 2026-11-23 23:30 LA == 2026-11-24 07:30 UTC.
        Session.objects.filter(pk=session.pk).update(
            opened_at=_aware(2026, 11, 24, 7, 30))
        session.refresh_from_db()

        payload = services.current_session_payload(self.manager)
        self.assertTrue(payload['open'])
        # The single-session accessor preserves the pre-B2 shape; it also
        # carries the canonical ``started_at_local`` alias.
        session = payload['session']
        self.assertEqual(session['business_day'], '2026-11-23')
        self.assertEqual(session['starting_cash'], '100.00')
        # Local wall-clock rendering must show the LA calendar day and offset.
        self.assertTrue(session['started_at_local'].startswith('2026-11-23T23:30'))
        self.assertTrue(session['started_at_local'].endswith('-08:00'))


class RangeBoundaryTests(DashboardBase):
    """S1: custom range windows are half-open [start, end)."""

    @override_settings(TIME_ZONE='UTC')
    def test_sale_exactly_at_end_boundary_is_excluded(self):
        # Range [2026-11-01, 2026-11-02): a sale created exactly at local
        # midnight of the exclusive end date (00:00 UTC here) must be excluded.
        info = BusinessInfo.get()
        info.timezone = 'UTC'
        info.save(update_fields=['timezone'])

        # in-range sale (23:59 on the exclusive start day) + boundary sale.
        self.make_sale(
            Decimal('10.00'),
            created_at=_aware(2026, 11, 1, 23, 59),
            method='cash',
        )
        self.make_sale(
            Decimal('20.00'),
            created_at=_aware(2026, 11, 2, 0, 0),
            method='cash',
        )

        data = services.period_payload(date(2026, 11, 1), date(2026, 11, 2))
        self.assertEqual(data['sales_count'], 1)
        self.assertEqual(data['revenue'], '10.00')
        self.assertEqual(data['cash_sales'], '10.00')

    @override_settings(TIME_ZONE='UTC')
    def test_top_items_half_open_excludes_end_boundary(self):
        info = BusinessInfo.get()
        info.timezone = 'UTC'
        info.save(update_fields=['timezone'])

        self.make_product('AtEnd')
        self.make_sale(
            Decimal('5.00'),
            created_at=_aware(2026, 11, 2, 0, 0),
            lines=[(self.make_product('AtEnd2'), 5, Decimal('5.00'))],
        )
        data = services.top_payload(
            'range', start_date=date(2026, 11, 1), end_date=date(2026, 11, 2))
        self.assertEqual(data['top_items'], [])
        self.assertEqual(data['total_items'], 0)


class BusinessTimezonePayloadTests(DashboardBase):
    """S2: composite/today payloads carry the IANA business timezone."""

    @override_settings(TIME_ZONE='UTC')
    def test_composite_payload_includes_business_timezone(self):
        info = BusinessInfo.get()
        info.timezone = 'America/Los_Angeles'
        info.save(update_fields=['timezone'])

        self.client.force_authenticate(self.manager)
        data = self.client.get('/api/dashboard/').json()
        self.assertEqual(data['business_timezone'], 'America/Los_Angeles')

    @override_settings(TIME_ZONE='UTC')
    def test_today_payload_includes_default_business_timezone(self):
        info = BusinessInfo.get()
        info.timezone = ''
        info.save(update_fields=['timezone'])

        payload = services.today_payload()
        self.assertEqual(payload['business_timezone'], 'UTC')


class SessionQueryCountTests(DashboardBase):
    """S4: register resolution is a single query, independent of register count.

    The previous implementation issued 2 queries per register (exists() +
    filter()). The query count of the whole payload must be constant regardless
    of how many accessible registers exist — that is what "no N+1" means.
    """

    @override_settings(TIME_ZONE='UTC')
    def test_query_count_is_constant_across_register_count(self):
        info = BusinessInfo.get()
        info.timezone = 'UTC'
        info.save(update_fields=['timezone'])

        from django.test.utils import CaptureQueriesContext
        from django.db import connection

        def count_queries():
            with CaptureQueriesContext(connection) as ctx:
                services.current_session_payload(self.manager)
            return len(ctx.captured_queries)

        baseline = count_queries()

        # Adding more accessible (empty-cashiers) registers must NOT add any
        # queries to the register-resolution step.
        for name in ('Reg2', 'Reg3', 'Reg4', 'Reg5'):
            Register.objects.create(name=name)

        self.assertEqual(count_queries(), baseline)
