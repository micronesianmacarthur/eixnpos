"""Tests for the dashboard aggregation service (contract §9 acceptance suite)."""
import os
import tempfile
from datetime import date, datetime, time as dtime, timedelta
from decimal import Decimal
from typing import Optional
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from business.models import BusinessInfo
from inventory.models import StockMovement
from products.models import Product
from sales.models import Payment, Sale, SaleLine
from sessions.models import Register, Session

from dashboard import services

User = get_user_model()
UTC = timezone.UTC


def _first_of_month_ago(today: date, months_back: int) -> date:
    """The first day of the month ``months_back`` before ``today``'s month."""
    year = today.year
    month = today.month - months_back
    while month < 1:
        month += 12
        year -= 1
    return date(year, month, 1)


class DashboardBase(TestCase):
    def setUp(self):
        self.manager = User.objects.create_user(
            username='manager', password='pw', role='manager',
        )
        self.cashier = User.objects.create_user(
            username='cashier', password='pw', role='cashier',
        )
        self.admin = User.objects.create_user(
            username='admin', password='pw', role='admin',
        )
        self.register = Register.objects.order_by('id').first() or Register.objects.create(
            name='Front',
        )
        self.client = APIClient()
        self.today = date.today()

    # ------------------------------------------------------------------ #
    # Factories
    # ------------------------------------------------------------------ #
    def make_product(self, name, min_stock=0, non_stock=False, active=True,
                     sku=None, category=None, price=Decimal('1.00')):
        return Product.objects.create(
            name=name, min_stock=min_stock, non_stock=non_stock, active=active,
            sku=sku, category=category, price=price,
        )

    def make_sale(self, total, *, status='completed', created_at=None,
                  method: Optional[str] = 'cash', lines=None, subtotal=None):
        """Create a sale with an optional created_at override.

        ``lines`` is a list of ``(product, quantity, line_total)`` used to
        populate SaleLine rows (needed for the top-items metrics).
        """
        sale = Sale.objects.create(
            cashier=self.manager,
            status=status,
            subtotal=subtotal if subtotal is not None else total,
            discount_total=Decimal('0.00'),
            tax_total=Decimal('0.00'),
            total=total,
        )
        if created_at is not None:
            Sale.objects.filter(pk=sale.pk).update(created_at=created_at)
            sale.refresh_from_db()
        if method:
            Payment.objects.create(sale=sale, method=method, amount=total)
        for product, qty, line_total in (lines or []):
            SaleLine.objects.create(
                sale=sale, product=product, quantity=qty,
                unit_price=Decimal(str(line_total)),
                line_total=Decimal(str(line_total)),
            )
        return sale


class DashboardAccessTests(DashboardBase):
    def test_anonymous_is_rejected(self):
        # Anonymous callers are rejected. DRF returns either 401 (NotAuthenticated)
        # or 403 for a manager-gated endpoint without a successful authenticator;
        # the codebase convention treats both as the rejection state.
        response = self.client.get('/api/dashboard/sales/today/')
        self.assertIn(response.status_code, (401, 403))

    def test_cashier_gets_403(self):
        self.client.force_authenticate(self.cashier)
        for url in ('/api/dashboard/sales/today/',
                    '/api/dashboard/sales/period/?start=2026-11-01&end=2026-11-24',
                    '/api/dashboard/sales/top/',
                    '/api/dashboard/sales/monthly/',
                    '/api/dashboard/sessions/current/',
                    '/api/dashboard/inventory/low-stock/',
                    '/api/dashboard/backup/',
                    '/api/dashboard/'):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 403, msg=url)

    def test_manager_gets_200(self):
        self.client.force_authenticate(self.manager)
        for url in ('/api/dashboard/sales/today/',
                    '/api/dashboard/sales/period/?start=2026-11-01&end=2026-11-24',
                    '/api/dashboard/sales/top/',
                    '/api/dashboard/sales/monthly/',
                    '/api/dashboard/sessions/current/',
                    '/api/dashboard/inventory/low-stock/',
                    '/api/dashboard/backup/',
                    '/api/dashboard/'):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200, msg=url)

    def test_admin_gets_200(self):
        self.client.force_authenticate(self.admin)
        self.assertEqual(
            self.client.get('/api/dashboard/sales/today/').status_code, 200)


class SalesTodayTests(DashboardBase):
    def test_completed_sale_inclusion_and_void_refund_counts(self):
        self.make_sale(Decimal('100.00'), method='cash')          # completed
        self.make_sale(Decimal('50.00'), status='open', method=None)  # open
        self.make_sale(Decimal('30.00'), status='voided', method=None)  # voided
        self.make_sale(Decimal('40.00'), status='refunded', method=None)  # refunded

        self.client.force_authenticate(self.manager)
        data = self.client.get('/api/dashboard/sales/today/').json()['today']

        # Only the completed sale counts toward revenue/sales_count.
        self.assertEqual(data['sales_count'], 1)
        self.assertEqual(data['revenue'], '100.00')
        # open/void sales never reach the drawer; the refunded sale has no payment.
        self.assertEqual(data['cash_sales'], '100.00')
        self.assertEqual(data['invoice_count'], 0)
        self.assertEqual(data['invoice_total'], '0.00')
        self.assertEqual(data['void_count'], 1)
        self.assertEqual(data['refund_count'], 1)
        self.assertEqual(data['open_count_today'], 1)
        self.assertEqual(data['avg_basket'], '100.00')

    def test_avg_basket(self):
        self.make_sale(Decimal('100.00'))
        self.make_sale(Decimal('300.00'))
        self.client.force_authenticate(self.manager)
        data = self.client.get('/api/dashboard/sales/today/').json()['today']
        self.assertEqual(data['sales_count'], 2)
        self.assertEqual(data['revenue'], '400.00')
        self.assertEqual(data['avg_basket'], '200.00')

    def test_empty_day_returns_zeros(self):
        self.client.force_authenticate(self.manager)
        empty = self.client.get('/api/dashboard/sales/today/').json()
        self.assertEqual(empty['today']['sales_count'], 0)
        self.assertEqual(empty['today']['revenue'], '0.00')
        self.assertEqual(empty['today']['cash_sales'], '0.00')
        self.assertEqual(empty['today']['avg_basket'], '0.00')
        self.assertIn('business_day', empty)
        self.assertIn('generated_at', empty)

    def test_invoice_totals(self):
        self.make_sale(Decimal('60.00'), method='invoice')
        self.make_sale(Decimal('40.00'), method='cash')
        self.client.force_authenticate(self.manager)
        data = self.client.get('/api/dashboard/sales/today/').json()['today']
        self.assertEqual(data['invoice_count'], 1)
        self.assertEqual(data['invoice_total'], '60.00')

    def test_refund_cash_is_not_netted(self):
        # A refunded sale keeps its original cash payment in cash_sales (v1
        # documented limitation) while revenue/sales_count exclude it.
        self.make_sale(Decimal('80.00'), method='cash')
        self.make_sale(Decimal('20.00'), status='refunded', method='cash')
        self.client.force_authenticate(self.manager)
        data = self.client.get('/api/dashboard/sales/today/').json()['today']
        self.assertEqual(data['sales_count'], 1)
        self.assertEqual(data['revenue'], '80.00')
        self.assertEqual(data['cash_sales'], '100.00')

    def test_money_fields_are_two_decimal_strings(self):
        self.make_sale(Decimal('1234.5'))
        self.client.force_authenticate(self.manager)
        data = self.client.get('/api/dashboard/sales/today/').json()['today']
        self.assertIsInstance(data['revenue'], str)
        self.assertRegex(data['revenue'], r'^\d+\.\d{2}$')


class SalesPeriodTests(DashboardBase):
    def test_range_aggregation(self):
        self.make_sale(
            Decimal('100.00'),
            created_at=timezone.make_aware(datetime(self.today.year, 1, 5, 12, 0)),
        )
        # A sale outside the range must not be counted.
        self.make_sale(
            Decimal('999.00'),
            created_at=timezone.make_aware(datetime(self.today.year, 1, 20, 12, 0)),
        )
        self.client.force_authenticate(self.manager)
        start = date(self.today.year, 1, 1)
        end = date(self.today.year, 1, 10)
        data = self.client.get(
            f'/api/dashboard/sales/period/?start={start}&end={end}'
        ).json()
        self.assertEqual(data['sales_count'], 1)
        self.assertEqual(data['revenue'], '100.00')
        self.assertEqual(data['period']['scope'], 'range')

    def test_start_after_end_is_empty_not_error(self):
        self.make_sale(Decimal('50.00'))
        self.client.force_authenticate(self.manager)
        data = self.client.get(
            '/api/dashboard/sales/period/?start=2026-11-10&end=2026-11-01'
        ).json()
        self.assertEqual(data['sales_count'], 0)
        self.assertEqual(data['revenue'], '0.00')

    def test_missing_params_are_400(self):
        self.client.force_authenticate(self.manager)
        self.assertEqual(
            self.client.get('/api/dashboard/sales/period/').status_code, 400)
        self.assertEqual(
            self.client.get('/api/dashboard/sales/period/?start=2026-11-01')
            .status_code, 400)


class TopItemsTests(DashboardBase):
    def test_top_five_by_units_with_tie_break(self):
        products = [self.make_product(f'P{i:02d}') for i in range(10)]
        # Every product sold the same quantity -> rank tie broken by name asc.
        for product in products:
            self.make_sale(
                Decimal('10.00'),
                lines=[(product, 3, Decimal('30.00'))],
            )
        self.client.force_authenticate(self.manager)
        data = self.client.get('/api/dashboard/sales/top/?scope=today').json()
        self.assertEqual(len(data['top_items']), 5)
        self.assertEqual(data['total_items'], 10)
        names = [row['product_name'] for row in data['top_items']]
        self.assertEqual(names, sorted(names))
        self.assertEqual(data['top_items'][0]['rank'], 1)
        self.assertEqual(data['top_items'][0]['units_sold'], 3)

    def test_top_items_show_revenue_and_avg_price(self):
        prod = self.make_product('Widget')
        self.make_sale(Decimal('30.00'), lines=[(prod, 3, Decimal('30.00'))])
        self.client.force_authenticate(self.manager)
        top = self.client.get('/api/dashboard/sales/top/').json()['top_items'][0]
        self.assertEqual(top['product_name'], 'Widget')
        self.assertEqual(top['units_sold'], 3)
        self.assertEqual(top['revenue'], '30.00')
        self.assertEqual(top['avg_unit_price'], '10.00')

    def test_week_and_month_scope(self):
        prod = self.make_product('Sell')
        self.make_sale(Decimal('20.00'), lines=[(prod, 2, Decimal('20.00'))])
        self.client.force_authenticate(self.manager)
        for scope in ('week', 'month'):
            data = self.client.get(f'/api/dashboard/sales/top/?scope={scope}').json()
            self.assertEqual(data['scope'], scope)
            self.assertEqual(data['top_items'][0]['product_name'], 'Sell')

    def test_custom_range(self):
        prod = self.make_product('RangeProd')
        self.make_sale(
            Decimal('15.00'),
            # Fixed date inside the queried range below (any deterministic
            # instant works; must not track the live wall-clock year).
            created_at=timezone.make_aware(
                datetime(2026, 1, 5, 12, 0)),
            lines=[(prod, 5, Decimal('15.00'))],
        )
        self.client.force_authenticate(self.manager)
        data = self.client.get(
            '/api/dashboard/sales/top/?start=2026-01-01&end=2026-01-10'
        ).json()
        self.assertEqual(data['scope'], 'range')
        self.assertEqual(data['top_items'][0]['product_name'], 'RangeProd')

    def test_empty_state(self):
        self.client.force_authenticate(self.manager)
        data = self.client.get('/api/dashboard/sales/top/').json()
        self.assertEqual(data['top_items'], [])
        self.assertEqual(data['total_items'], 0)


class MonthlyTests(DashboardBase):
    def test_monthly_series_mom(self):
        two_back = _first_of_month_ago(self.today, 2)   # month A
        one_back = _first_of_month_ago(self.today, 1)   # month B
        now = self.today.replace(day=1)

        def sale_in(total, month_first):
            return self.make_sale(
                Decimal(str(total)),
                created_at=timezone.make_aware(
                    datetime(month_first.year, month_first.month, 10, 12, 0)
                ),
            )

        sale_in(100, two_back)   # A revenue = 100
        sale_in(150, one_back)   # B revenue = 150

        self.client.force_authenticate(self.manager)
        data = self.client.get('/api/dashboard/sales/monthly/?months=12').json()
        by_period = {p['period']: p for p in data['periods']}

        a_key = two_back.strftime('%Y-%m')
        b_key = one_back.strftime('%Y-%m')
        cur_key = now.strftime('%Y-%m')
        self.assertEqual(data['latest_period'], cur_key)

        # The first returned period (oldest in the window) has null mom.
        self.assertIsNone(data['periods'][0]['mom_revenue'])
        # Previous completed month (B) gets mom vs A (deterministic).
        self.assertEqual(by_period[b_key]['revenue'], '150.00')
        self.assertEqual(by_period[b_key]['mom_revenue'], '50.00')
        self.assertEqual(by_period[b_key]['mom_revenue_pct'], 50.0)
        # B's label/cash are populated from the sale data.
        self.assertEqual(by_period[a_key]['revenue'], '100.00')
        # Most recent / in-progress month reports null mom.
        self.assertIsNone(by_period[cur_key]['mom_revenue'])
        self.assertIsNone(by_period[cur_key]['mom_revenue_pct'])

    def test_month_count_clamped_and_empty_months_zero(self):
        self.client.force_authenticate(self.manager)
        data = self.client.get('/api/dashboard/sales/monthly/?months=1').json()
        self.assertEqual(len(data['periods']), 1)
        self.assertEqual(data['periods'][0]['revenue'], '0.00')
        self.assertIsNone(data['periods'][0]['mom_revenue'])

    def test_invalid_months_400(self):
        self.client.force_authenticate(self.manager)
        self.assertEqual(
            self.client.get('/api/dashboard/sales/monthly/?months=abc').status_code,
            400)


class SessionCurrentTests(DashboardBase):
    def test_open_session_shape(self):
        session = Session.objects.create(
            cashier=self.cashier, starting_cash=Decimal('55.00'),
            register=self.register, business_day=self.today,
        )
        self.client.force_authenticate(self.manager)
        data = self.client.get('/api/dashboard/sessions/current/').json()
        self.assertTrue(data['open'])
        self.assertEqual(data['count'], 1)
        self.assertEqual(data['business_day'], self.today.isoformat())
        sessions = data['sessions']
        self.assertEqual(len(sessions), 1)
        item = sessions[0]
        # Canonical B2 fields are present on each session object.
        self.assertEqual(item['session_id'], session.id)
        self.assertEqual(item['register_name'], self.register.name)
        self.assertEqual(item['opened_at_local'].endswith('+00:00'), True)
        self.assertEqual(item['business_day'], self.today.isoformat())
        self.assertEqual(item['register']['id'], self.register.id)
        self.assertEqual(item['opened_by']['username'], 'cashier')
        self.assertEqual(item['opened_by']['role'], 'cashier')
        self.assertEqual(item['cashiers'], [])
        self.assertEqual(item['starting_cash'], '55.00')
        # The single-session accessor stays backward-compatible (same object).
        self.assertEqual(data['session']['session_id'], session.id)
        self.assertTrue(data['session']['started_at_local'].endswith('+00:00'))

    def test_no_open_session(self):
        self.client.force_authenticate(self.manager)
        data = self.client.get('/api/dashboard/sessions/current/').json()
        self.assertFalse(data['open'])
        self.assertEqual(data['count'], 0)
        self.assertEqual(data['sessions'], [])
        self.assertEqual(data['session'], {'open': False})

    def test_manager_not_in_cashiers_still_sees_open_session(self):
        """Blocker B2 regression: a manager who is NOT a register cashier must
        still see the open session on that register."""
        # Register with an explicit cashier list that does NOT include the manager.
        cashiers_register = Register.objects.create(name='Back')
        cashiers_register.cashiers.set([self.cashier])
        Session.objects.create(
            cashier=self.cashier, starting_cash=Decimal('10.00'),
            register=cashiers_register, business_day=self.today,
        )
        self.client.force_authenticate(self.manager)
        data = self.client.get('/api/dashboard/sessions/current/').json()['sessions']
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]['register']['id'], cashiers_register.id)
        self.assertEqual(data[0]['cashiers'], ['cashier'])
        self.assertEqual(data[0]['opened_by']['username'], 'cashier')

    def test_manager_sees_open_sessions_across_all_registers(self):
        """A manager sees every open session for the business day, regardless
        of register cashier assignment."""
        reg_a = Register.objects.create(name='A')
        reg_b = Register.objects.create(name='B', identifier='B')
        Session.objects.create(
            cashier=self.cashier, register=reg_a, business_day=self.today,
        )
        Session.objects.create(
            cashier=self.manager, register=reg_b, business_day=self.today,
        )
        self.client.force_authenticate(self.manager)
        data = self.client.get('/api/dashboard/sessions/current/').json()
        self.assertTrue(data['open'])
        self.assertEqual(data['count'], 2)
        names = {s['register_name'] for s in data['sessions']}
        self.assertEqual(names, {'A', 'B'})

    def test_cashier_sees_only_assigned_registers(self):
        """A cashier only sees sessions on registers they are assigned to.

        The dashboard HTTP endpoint is manager-gated (cashiers get 403), so the
        cashier-side scoping is exercised at the service-function level.
        """
        self.register.cashiers.set([self.cashier])
        other = Register.objects.create(name='Other')
        other.cashiers.set([self.manager])
        Session.objects.create(
            cashier=self.cashier, register=self.register, business_day=self.today,
        )
        Session.objects.create(
            cashier=self.manager, register=other, business_day=self.today,
        )
        data = services.current_session_payload(self.cashier)
        self.assertTrue(data['open'])
        self.assertEqual(data['count'], 1)
        self.assertEqual(data['sessions'][0]['register']['id'], self.register.id)

    def test_cashier_sees_open_session_on_shared_register(self):
        """A register with an empty cashiers M2M is accessible by everyone;
        a cashier sees an open session there even if not explicitly assigned
        (service-level scoping modelled on the sessions API rule)."""
        Session.objects.create(
            cashier=self.manager, starting_cash=Decimal('20.00'),
            register=self.register, business_day=self.today,
        )
        data = services.current_session_payload(self.cashier)
        self.assertTrue(data['open'])
        self.assertEqual(data['count'], 1)
        self.assertEqual(data['sessions'][0]['register']['id'], self.register.id)


class LowStockTests(DashboardBase):
    def test_low_stock_threshold_and_exclusions(self):
        low = self.make_product('Low', min_stock=5)
        StockMovement.objects.create(product=low, movement_type='receive', quantity=2)
        ok = self.make_product('OK', min_stock=5)
        StockMovement.objects.create(product=ok, movement_type='receive', quantity=9)
        non_stock = self.make_product('NonStock', min_stock=0, non_stock=True)
        inactive = self.make_product('Inactive', min_stock=5, active=False)
        StockMovement.objects.create(product=inactive, movement_type='receive', quantity=1)

        self.client.force_authenticate(self.manager)
        data = self.client.get('/api/dashboard/inventory/low-stock/').json()
        names = {item['product_name'] for item in data['items']}
        self.assertEqual(data['low_stock_count'], 1)
        self.assertIn('Low', names)
        self.assertNotIn('OK', names)
        self.assertNotIn('NonStock', names)
        self.assertNotIn('Inactive', names)
        item = next(i for i in data['items'] if i['product_name'] == 'Low')
        self.assertEqual(item['quantity_on_hand'], 2)
        self.assertEqual(item['min_stock'], 5)

    def test_empty_state_and_product_with_no_movements(self):
        bare = self.make_product('NoMoves', min_stock=0)
        self.client.force_authenticate(self.manager)
        data = self.client.get('/api/dashboard/inventory/low-stock/').json()
        # active stock product with 0 on hand and min_stock 0 is at/below threshold.
        self.assertGreaterEqual(data['low_stock_count'], 1)
        names = {i['product_name'] for i in data['items']}
        self.assertIn('NoMoves', names)


class CompositeTests(DashboardBase):
    def test_composite_includes_today_session_and_inventory(self):
        self.make_sale(Decimal('20.00'))
        Session.objects.create(
            cashier=self.cashier, starting_cash=Decimal('0.00'),
            register=self.register, business_day=self.today,
        )
        self.client.force_authenticate(self.manager)
        data = self.client.get('/api/dashboard/').json()
        self.assertIn('today', data)
        self.assertEqual(data['today']['revenue'], '20.00')
        self.assertTrue(data['session']['open'])
        self.assertIn('inventory', data)
        self.assertIn('low_stock_count', data['inventory'])

    def test_composite_manager_sees_all_open_shifts(self):
        """Integration: the manager's first-paint composite surfaces every open
        shift, including registers they are not a cashier of."""
        back = Register.objects.create(name='Back')
        back.cashiers.set([self.cashier])
        Session.objects.create(
            cashier=self.cashier, register=back, business_day=self.today,
        )
        Session.objects.create(
            cashier=self.manager, register=self.register, business_day=self.today,
        )
        self.client.force_authenticate(self.manager)
        data = self.client.get('/api/dashboard/').json()['session']
        self.assertTrue(data['open'])
        self.assertEqual(data['count'], 2)
        names = {s['register_name'] for s in data['sessions']}
        self.assertEqual(names, {'Back', self.register.name})


class TimezoneTests(DashboardBase):
    @override_settings(TIME_ZONE='UTC')
    def test_business_local_day_mapping(self):
        info = BusinessInfo.get()
        info.timezone = 'America/Los_Angeles'
        info.save(update_fields=['timezone'])

        # 2026-11-24T01:30Z is 2026-11-23 17:30 in LA (UTC-8)
        with mock.patch(
            'dashboard.services._now_utc',
            return_value=datetime(2026, 11, 24, 1, 30, tzinfo=UTC),
        ):
            payload = services.today_payload()
            self.assertEqual(payload['business_day'], '2026-11-23')
            today = payload['today']
            self.assertEqual(today['sales_count'], 0)
            self.assertNotEqual(payload['period']['start'][:10], '2026-11-24')

        # 2026-11-24T08:30Z is 2026-11-24 00:30 in LA -> new business day.
        with mock.patch(
            'dashboard.services._now_utc',
            return_value=datetime(2026, 11, 24, 8, 30, tzinfo=UTC),
        ):
            payload = services.today_payload()
            self.assertEqual(payload['business_day'], '2026-11-24')

    def test_monthly_latest_label_uses_business_local_month(self):
        info = BusinessInfo.get()
        info.timezone = 'America/Los_Angeles'
        info.save(update_fields=['timezone'])
        with mock.patch(
            'dashboard.services._now_utc',
            return_value=datetime(2026, 11, 24, 1, 30, tzinfo=UTC),
        ):
            # LA local date is 2026-11-23; latest month label is still Nov 2026
            # (business-local calendar month), never "today" from the client.
            payload = services.monthly_payload(2)
            self.assertEqual(payload['latest_period'], '2026-11')
            self.assertEqual(payload['periods'][-1]['label'], 'Nov 2026')


class BackupStatusTests(DashboardBase):
    """GET /api/dashboard/backup/ (contract §4.7 / §5.8)."""

    def assert_not_configured(self, data):
        self.assertIs(data['backup_path_configured'], False)
        self.assertIsNone(data['last_backup_at'])
        self.assertIsNone(data['last_backup_path'])

    def test_returns_null_status_when_no_backup_path_configured(self):
        info = BusinessInfo.get()
        info.backup_path = ''
        info.save(update_fields=['backup_path'])

        self.client.force_authenticate(self.manager)
        data = self.client.get('/api/dashboard/backup/').json()

        self.assert_not_configured(data)

    def test_returns_newest_backup_at_and_path_when_configured(self):
        info = BusinessInfo.get()
        with tempfile.TemporaryDirectory() as tmp:
            info.backup_path = tmp
            info.save(update_fields=['backup_path'])

            # Two dumps with distinct mtimes; the newer must win.
            old = os.path.join(tmp, 'backup_20260101_000000.json')
            new = os.path.join(tmp, 'backup_20261124_120000.json')
            for p in (old, new):
                with open(p, 'w', encoding='utf-8') as f:
                    f.write('[]')
            os.utime(old, (1767225600, 1767225600))
            os.utime(new, (1795521600, 1795521600))

            self.client.force_authenticate(self.manager)
            data = self.client.get('/api/dashboard/backup/').json()

            self.assertIs(data['backup_path_configured'], True)
            self.assertEqual(data['last_backup_path'], new)
            self.assertTrue(data['last_backup_at'].startswith('2026-11-24T'))
            # local variant is ISO-8601 with an offset
            self.assertIn('+', data['last_backup_at_local'])

    def test_reports_configured_but_no_backup_yet(self):
        info = BusinessInfo.get()
        with tempfile.TemporaryDirectory() as tmp:
            info.backup_path = tmp
            info.save(update_fields=['backup_path'])

            self.client.force_authenticate(self.manager)
            data = self.client.get('/api/dashboard/backup/').json()

            self.assertIs(data['backup_path_configured'], True)
            self.assertIsNone(data['last_backup_at'])
            self.assertIsNone(data['last_backup_path'])

    def test_cashier_gets_403(self):
        self.client.force_authenticate(self.cashier)
        response = self.client.get('/api/dashboard/backup/')
        self.assertEqual(response.status_code, 403)

    def test_anonymous_is_rejected(self):
        response = self.client.get('/api/dashboard/backup/')
        self.assertIn(response.status_code, (401, 403))
