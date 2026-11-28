"""Manual-only session lifecycle tests.

The register shift (``sessions.models.Session``) has no scheduled or
automatic close: nothing in the codebase — no management command, Celery
worker, signal handler, middleware or cron job — may move an open session to
``closed``. The only open→closed write is ``SessionViewSet.close``, an
explicit, authenticated endpoint that records who closed the shift.

These tests lock that invariant in from three angles:

1. Architecture tripwires — no auto-close machinery may exist (a management
   command, a background worker in ``INSTALLED_APPS``, an ``AUTO_CLOSE``
   setting, or a ``post_save`` receiver on ``Session``). If any of these
   appear, the lifecycle has changed and these tests fail on purpose.
2. Behaviour — a session opened before midnight stays open and usable the
   next day (readable, attributable, reconcilable) until someone closes it.
3. Roles — Cashier, Manager and Admin can all perform the explicit close,
   and each is recorded as the closer.
"""
from datetime import date, datetime, time as dtime, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import get_commands
from django.db.models.signals import post_save
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from sales.models import Payment, Sale
from sessions.models import Register, Session, business_tzinfo, local_date_of

User = get_user_model()

# Django's built-in ``clearsessions`` purges the *auth* session table
# (django.contrib.sessions) — it does not touch the register-shift model.
# It is the only allowlisted command name; anything else whose name suggests
# session closing must be reviewed before these tests pass.
ALLOWED_COMMAND_NAMES = {'clearsessions'}


class NoAutomaticCloseArchitectureTests(TestCase):
    """Tripwires: nothing in the process tree closes sessions by itself."""

    def test_no_management_command_targets_the_register_shift(self):
        suspicious = [
            name
            for name in get_commands()
            if name not in ALLOWED_COMMAND_NAMES
            and any(
                token in name.lower()
                for token in ('close', 'rollover', 'roll_over', 'midnight',
                              'day_end', 'dayend', 'shift', 'session')
            )
        ]
        self.assertEqual(
            suspicious, [],
            'Management commands that look like automatic session close were '
            f'installed: {suspicious}. The register shift closes only via the '
            'explicit /api/sessions/<id>/close/ endpoint; if a new command is '
            'intentional, this allowlist test and ADR-001 must be revisited.',
        )

    def test_no_background_worker_or_auto_close_configuration(self):
        self.assertNotIn(
            'celery', [app.lower() for app in settings.INSTALLED_APPS],
            'Celery appeared in INSTALLED_APPS: verify no beat task closes '
            'open sessions before trusting the manual-only lifecycle.',
        )
        auto_close_settings = [
            name for name in dir(settings)
            if 'AUTO_CLOSE' in name.upper() and not name.startswith('_')
        ]
        self.assertEqual(
            auto_close_settings, [],
            f'Auto-close settings exist ({auto_close_settings}) but nothing '
            'reads them: either wire them up and review the lifecycle, or do '
            'not add the flag.',
        )

    def test_no_signal_handler_closes_the_register_shift(self):
        self.assertEqual(
            post_save._live_receivers(sender=Session), ([], []),
            'A post_save receiver is registered on Session. Signals that '
            'mutate the shift on save are how silent auto-close regressions '
            'happen; the only open→closed write must be SessionViewSet.close.',
        )


class CrossDayOpenSessionTests(TestCase):
    """A shift opened on day 1 stays open and usable through day 2."""

    def setUp(self):
        self.manager = User.objects.create_user(
            username='manager', password='pw', role='manager'
        )
        self.client = APIClient()
        self.client.force_authenticate(self.manager)
        self.register = (
            Register.objects.order_by('id').first()
            or Register.objects.create(name='Test Register')
        )
        self.business_tz = business_tzinfo()
        self.day1 = local_date_of(timezone.now()) - timedelta(days=1)
        self.day2 = self.day1 + timedelta(days=1)

    def _at(self, day, hour, minute):
        return datetime.combine(day, dtime(hour, minute), tzinfo=self.business_tz)

    def _open_session(self, starting_cash='100.00'):
        with patch('django.utils.timezone.now', return_value=self._at(self.day1, 8, 0)):
            response = self.client.post(
                '/api/sessions/',
                {'register': self.register.id, 'starting_cash': starting_cash},
                format='json',
            )
        self.assertEqual(response.status_code, 201, response.data)
        session = Session.objects.get(pk=response.data['id'])
        self.assertEqual(session.business_day, self.day1)
        self.assertEqual(session.status, Session.STATUS_OPEN)
        return session

    def _make_cash_sale(self, total, cashier=None, at=None):
        sale = Sale.objects.create(
            cashier=cashier or self.manager,
            status=Sale.STATUS_COMPLETED,
            subtotal=total, total=total,
        )
        Payment.objects.create(sale=sale, method='cash', amount=total)
        if at is not None:
            # Pin the sale inside the (mocked) session window; auto_now_add
            # would otherwise stamp it with real wall-clock time.
            Sale.objects.filter(pk=sale.pk).update(created_at=at)
        return sale

    def test_full_day_passing_leaves_the_session_open(self):
        """List, retrieve, current and summary reads spanning midnight close
        nothing: the row is still ``open`` with no closer, past every read."""
        session = self._open_session()
        self._make_cash_sale(Decimal('25.00'), at=self._at(self.day1, 12, 0))

        for moment in (self._at(self.day1, 23, 59), self._at(self.day2, 9, 0),
                       self._at(self.day2, 23, 59)):
            with self.subTest(moment=moment), patch(
                'django.utils.timezone.now', return_value=moment
            ):
                self.assertEqual(
                    self.client.get('/api/sessions/').status_code, 200)
                self.assertEqual(
                    self.client.get(
                        f'/api/sessions/{session.id}/').status_code, 200)
                current = self.client.get(
                    f'/api/sessions/current/?register={self.register.id}'
                    f'&day={self.day1.isoformat()}'
                )
                self.assertEqual(current.status_code, 200)
                self.assertTrue(current.data['open'])
                self.assertEqual(
                    self.client.get(
                        f'/api/sessions/{session.id}/summary/').status_code, 200)

            session.refresh_from_db()
            self.assertEqual(session.status, Session.STATUS_OPEN)
            self.assertIsNone(session.closed_at)
            self.assertIsNone(session.closed_by)
            self.assertEqual(session.business_day, self.day1)

    def test_day2_read_traffic_does_not_mutate_the_session(self):
        """The close transition is not triggered by any read endpoint: after
        a day's worth of traffic on day 2 the session is byte-identical on
        the lifecycle fields."""
        session = self._open_session()
        snapshot = {
            'status': session.status, 'closed_at': session.closed_at,
            'closed_by': session.closed_by, 'counted_cash': session.counted_cash,
            'expected_cash': session.expected_cash,
        }
        with patch('django.utils.timezone.now',
                   return_value=self._at(self.day2, 12, 0)):
            for _ in range(3):
                self.client.get('/api/sessions/')
                self.client.get(f'/api/sessions/{session.id}/')
                self.client.get(
                    f'/api/sessions/{session.id}/zreport/')  # preview on open
        session.refresh_from_db()
        self.assertEqual(session.status, snapshot['status'])
        self.assertEqual(session.closed_at, snapshot['closed_at'])
        self.assertEqual(session.closed_by, snapshot['closed_by'])
        self.assertEqual(session.counted_cash, snapshot['counted_cash'])
        self.assertEqual(session.expected_cash, snapshot['expected_cash'])

    def test_day1_sales_reconcile_when_the_session_is_closed_on_day2(self):
        """Cross-day persistence: sales made before midnight are still
        attributed to the open session and reconcile correctly when a human
        explicitly closes it the next day."""
        session = self._open_session()
        self._make_cash_sale(Decimal('40.00'), at=self._at(self.day1, 10, 0))
        self._make_cash_sale(Decimal('10.00'), at=self._at(self.day1, 11, 0))

        with patch('django.utils.timezone.now',
                   return_value=self._at(self.day2, 9, 0)):
            # The next business day has no open session of its own yet; the
            # old one is still open and reconcilable.
            self.assertTrue(
                Session.objects.filter(
                    pk=session.pk, status=Session.STATUS_OPEN).exists())
            response = self.client.post(
                f'/api/sessions/{session.id}/close/',
                {'counts': {'fifties': 3}},
                format='json',
            )

        self.assertEqual(response.status_code, 200, response.data)
        session.refresh_from_db()
        self.assertEqual(session.status, Session.STATUS_CLOSED)
        self.assertEqual(session.expected_cash, Decimal('150.00'))  # 100 + 50
        self.assertEqual(session.counted_cash, Decimal('150.00'))
        self.assertEqual(session.sales_total, Decimal('50.00'))
        self.assertEqual(session.closed_by, self.manager)
        self.assertIsNotNone(session.closed_at)
        self.assertEqual(session.business_day, self.day1)  # never rewritten


class ManualCloseRoleTests(TestCase):
    """Cashier, Manager and Admin can all explicitly close/reconcile."""

    def setUp(self):
        self.register = (
            Register.objects.order_by('id').first()
            or Register.objects.create(name='Test Register')
        )
        self.opener = User.objects.create_user(
            username='opener', password='pw', role='cashier'
        )
        self.today = date.today()

    def test_each_role_can_close_and_is_recorded_as_closer(self):
        for role in ('cashier', 'manager', 'admin'):
            with self.subTest(role=role):
                session = Session.objects.create(
                    cashier=self.opener, starting_cash=Decimal('20.00'),
                    register=self.register, business_day=self.today,
                )
                user = User.objects.create_user(
                    username=f'closer_{role}', password='pw', role=role
                )
                self.register.cashiers.add(user)
                client = APIClient()
                client.force_authenticate(user)

                response = client.post(
                    f'/api/sessions/{session.id}/close/',
                    {'counts': {'twenties': 1}},
                    format='json',
                )

                self.assertEqual(response.status_code, 200, response.data)
                session.refresh_from_db()
                self.assertEqual(session.status, Session.STATUS_CLOSED)
                self.assertEqual(session.closed_by, user)
                self.assertEqual(session.cashier, self.opener)
                self.assertIsNotNone(session.closed_at)
                self.assertEqual(response.data['closed_by'], user.id)
