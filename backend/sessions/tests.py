import glob
import os
import tempfile
from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from business.models import BusinessInfo
from products.models import Product
from sales.models import Payment, Sale
from suppliers.models import Supplier

from .models import Register, Session

User = get_user_model()


class SessionApiTests(TestCase):
    def setUp(self):
        self.manager = User.objects.create_user(username='manager', password='pw', role='manager')
        self.client = APIClient()
        self.client.force_authenticate(self.manager)

        supplier = Supplier.objects.create(name='Acme')
        self.product = Product.objects.create(name='Widget', price=Decimal('10.00'), supplier=supplier)
        # The register/day scope: default to the seeded register so helper
        # sessions line up with the default register/day the endpoints resolve.
        self.register = Register.objects.order_by('id').first() or Register.objects.create(name='Test Register')
        self.today = date.today()

    def make_session(self, starting_cash=Decimal('0.00'), **kwargs):
        kwargs.setdefault('register', self.register)
        kwargs.setdefault('business_day', self.today)
        return Session.objects.create(cashier=self.manager, starting_cash=starting_cash, **kwargs)

    def make_completed_sale(self, total, method='cash', amount=None):
        sale = Sale.objects.create(
            cashier=self.manager,
            status=Sale.STATUS_COMPLETED,
            subtotal=total,
            total=total,
        )
        Payment.objects.create(
            sale=sale,
            method=method,
            amount=amount if amount is not None else total,
        )
        return sale

    def test_create_session_uses_authenticated_cashier(self):
        response = self.client.post(
            '/api/sessions/',
            {'cashier': 9999, 'starting_cash': '100.00'},
            format='json',
        )

        self.assertEqual(response.status_code, 201)
        session = Session.objects.get(pk=response.data['id'])
        self.assertEqual(session.cashier, self.manager)
        self.assertEqual(session.starting_cash, Decimal('100.00'))
        self.assertEqual(session.status, Session.STATUS_OPEN)

    def test_create_session_rejects_missing_starting_cash(self):
        response = self.client.post('/api/sessions/', {}, format='json')

        self.assertEqual(response.status_code, 400)
        self.assertIn('starting_cash', response.data)
        self.assertFalse(Session.objects.filter(cashier=self.manager).exists())

    def test_create_session_rejects_negative_starting_cash(self):
        response = self.client.post(
            '/api/sessions/',
            {'starting_cash': '-5.00'},
            format='json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(Session.objects.filter(cashier=self.manager).exists())

    def test_close_reconciles_cash_and_persists_zreport(self):
        session = self.make_session(starting_cash=Decimal('100.00'))
        self.make_completed_sale(Decimal('50.00'), method='cash', amount=Decimal('50.00'))

        response = self.client.post(
            f'/api/sessions/{session.id}/close/',
            {'counts': {'hundreds': 1, 'twenties': 2, 'tens': 1}, 'cash_removed': '40.00'},
            format='json',
        )

        self.assertEqual(response.status_code, 200)
        session.refresh_from_db()
        self.assertEqual(session.status, Session.STATUS_CLOSED)
        self.assertIsNotNone(session.closed_at)
        self.assertEqual(session.counted_cash, Decimal('150.00'))
        self.assertEqual(session.expected_cash, Decimal('150.00'))
        self.assertEqual(session.variance, Decimal('0.00'))
        self.assertEqual(session.sales_total, Decimal('50.00'))
        self.assertEqual(session.cash_removed, Decimal('40.00'))
        self.assertEqual(session.cash_counts['twenties'], 2)

    def test_close_reports_short_cash(self):
        session = self.make_session(starting_cash=Decimal('100.00'))
        self.make_completed_sale(Decimal('50.00'), method='cash', amount=Decimal('50.00'))

        response = self.client.post(
            f'/api/sessions/{session.id}/close/',
            {'counts': {'hundreds': 1, 'twenties': 2}},
            format='json',
        )

        self.assertEqual(response.status_code, 200)
        session.refresh_from_db()
        self.assertEqual(session.counted_cash, Decimal('140.00'))
        self.assertEqual(session.expected_cash, Decimal('150.00'))
        self.assertEqual(session.variance, Decimal('10.00'))

    def test_close_only_counts_cash_payments_in_expected_cash(self):
        session = self.make_session(starting_cash=Decimal('0.00'))
        self.make_completed_sale(Decimal('20.00'), method='cash', amount=Decimal('20.00'))
        self.make_completed_sale(Decimal('30.00'), method='card', amount=Decimal('30.00'))

        response = self.client.post(
            f'/api/sessions/{session.id}/close/',
            {'counts': {'twenties': 1}},
            format='json',
        )

        self.assertEqual(response.status_code, 200)
        session.refresh_from_db()
        self.assertEqual(session.expected_cash, Decimal('20.00'))
        self.assertEqual(session.counted_cash, Decimal('20.00'))
        self.assertEqual(session.sales_total, Decimal('50.00'))
        self.assertEqual(session.variance, Decimal('0.00'))

    def test_close_twice_is_rejected(self):
        session = self.make_session()
        self.client.post(f'/api/sessions/{session.id}/close/', {'counts': {}}, format='json')

        response = self.client.post(
            f'/api/sessions/{session.id}/close/',
            {'counts': {}},
            format='json',
        )

        self.assertEqual(response.status_code, 400)

    def test_close_triggers_backup_when_path_is_configured(self):
        session = self.make_session()
        with tempfile.TemporaryDirectory() as destination:
            business = BusinessInfo.get()
            business.backup_path = destination
            business.save(update_fields=['backup_path'])

            response = self.client.post(
                f'/api/sessions/{session.id}/close/',
                {'counts': {}},
                format='json',
            )

            self.assertEqual(response.status_code, 200)
            backups = glob.glob(os.path.join(destination, 'backup_*.json'))
            self.assertEqual(len(backups), 1)

    def test_zreport_returns_pdf(self):
        session = self.make_session(starting_cash=Decimal('25.00'))

        response = self.client.get(f'/api/sessions/{session.id}/zreport/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/pdf')
        self.assertTrue(response.content.startswith(b'%PDF'))

    def test_removal_uses_requested_available_counts(self):
        session = self.make_session(cash_counts={'twenties': 2, 'tens': 1})

        response = self.client.post(
            f'/api/sessions/{session.id}/removal/',
            {'amount': '30.00', 'available_counts': {'twenties': 2, 'tens': 1}},
            format='json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['counts']['twenties'], 1)
        self.assertEqual(response.data['counts']['tens'], 1)
        self.assertEqual(Decimal(response.data['total_removed']), Decimal('30.00'))
        self.assertEqual(Decimal(response.data['remaining']), Decimal('0'))

    def test_removal_falls_back_to_session_cash_counts(self):
        session = self.make_session(cash_counts={'twenties': 1})

        response = self.client.post(
            f'/api/sessions/{session.id}/removal/',
            {'amount': '20.00'},
            format='json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['counts']['twenties'], 1)
        self.assertEqual(Decimal(response.data['total_removed']), Decimal('20.00'))

    def test_sessions_api_requires_authentication(self):
        anonymous = APIClient()

        response = anonymous.get('/api/sessions/')

        self.assertIn(response.status_code, (401, 403))

    def test_current_returns_open_false_when_no_open_session(self):
        response = self.client.get('/api/sessions/current/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, {'open': False})

    def test_current_returns_open_true_with_session_details(self):
        session = self.make_session(starting_cash=Decimal('100.00'))

        response = self.client.get('/api/sessions/current/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['open'], True)
        self.assertEqual(response.data['sessionId'], str(session.id))
        self.assertEqual(
            response.data['startedAt'], session.opened_at.isoformat()
        )
        self.assertEqual(response.data['startingCash'], '100.00')

    def test_current_ignores_closed_sessions(self):
        session = self.make_session(starting_cash=Decimal('50.00'))
        session.status = Session.STATUS_CLOSED
        session.closed_at = '2026-11-16T00:00:00Z'
        session.save()

        response = self.client.get('/api/sessions/current/')

        self.assertEqual(response.data, {'open': False})

    def test_current_session_is_shared_across_cashiers_on_same_register_day(self):
        """A session opened by User A is visible to any cashier with access to
        the same register/day — not just the cashier who opened it."""
        session = self.make_session(starting_cash=Decimal('20.00'))
        other = User.objects.create_user(
            username='cashier_b', password='***', role='cashier'
        )
        other_client = APIClient()
        other_client.force_authenticate(other)

        manager_response = self.client.get('/api/sessions/current/')
        other_response = other_client.get('/api/sessions/current/')

        self.assertTrue(manager_response.data['open'])
        self.assertTrue(other_response.data['open'])
        self.assertEqual(other_response.data['sessionId'], str(session.id))

    def test_current_ignores_sessions_on_a_different_register(self):
        """The open session on another register must not surface as 'current'."""
        other_register = Register.objects.create(name='Bar Register')
        self.make_session(starting_cash=Decimal('20.00'), register=other_register)

        response = self.client.get('/api/sessions/current/')

        self.assertEqual(response.data, {'open': False})

    def test_current_with_explicit_register_param(self):
        session = self.make_session(starting_cash=Decimal('30.00'))

        response = self.client.get(
            f'/api/sessions/current/?register={self.register.id}&day={self.today.isoformat()}'
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data['open'])
        self.assertEqual(response.data['sessionId'], str(session.id))
        self.assertEqual(response.data['day'], self.today.isoformat())

    def test_current_rejects_access_to_unknown_or_restricted_register(self):
        unknown = self.client.get('/api/sessions/current/?register=999999')
        self.assertEqual(unknown.status_code, 404)

        restricted = Register.objects.create(name='Private')
        owner = User.objects.create_user(username='register_owner', password='***', role='cashier')
        restricted.cashiers.add(owner)
        other = User.objects.create_user(username='cashier_c', password='***', role='cashier')
        other_client = APIClient()
        other_client.force_authenticate(other)
        denied = other_client.get(f'/api/sessions/current/?register={restricted.id}')
        self.assertEqual(denied.status_code, 403)

    def test_current_requires_authentication(self):
        anonymous = APIClient()

        response = anonymous.get('/api/sessions/current/')

        self.assertIn(response.status_code, (401, 403))

    def test_duplicate_open_on_same_register_day_is_rejected(self):
        """A second cashier cannot open a session on an already-open register/day."""
        self.make_session(starting_cash=Decimal('50.00'))

        response = self.client.post(
            '/api/sessions/',
            {'register': self.register.id, 'starting_cash': '100.00'},
            format='json',
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            Session.objects.filter(
                register=self.register, business_day=self.today, status=Session.STATUS_OPEN
            ).count(), 1,
        )

    def test_two_cashiers_can_open_on_different_registers_same_day(self):
        """The old global one-open-session limit is gone: each register/day is
        independent, so two registers can both hold an open session today."""
        register_a = self.register
        register_b = Register.objects.create(name='Register B')

        first = self.client.post(
            '/api/sessions/', {'register': register_a.id, 'starting_cash': '10.00'}, format='json'
        )
        second = self.client.post(
            '/api/sessions/', {'register': register_b.id, 'starting_cash': '20.00'}, format='json'
        )

        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 201)
        self.assertEqual(Session.objects.filter(status=Session.STATUS_OPEN).count(), 2)

    def test_create_without_register_defaults_to_accessible_register(self):
        response = self.client.post(
            '/api/sessions/', {'starting_cash': '60.00'}, format='json'
        )

        self.assertEqual(response.status_code, 201)
        created = Session.objects.get(pk=response.data['id'])
        self.assertEqual(created.register, self.register)
        self.assertEqual(created.business_day, self.today)

    def test_create_rejects_access_to_restricted_register(self):
        restricted = Register.objects.create(name='Private Only')
        other = User.objects.create_user(username='cashier_d', password='***', role='cashier')
        restricted.cashiers.add(other)

        response = self.client.post(
            '/api/sessions/', {'register': restricted.id, 'starting_cash': '5.00'}, format='json'
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(Session.objects.filter(register=restricted).count(), 0)

    def test_registers_endpoint_lists_accessible_registers(self):
        """The register picker helper lists only registers the caller may use."""
        hidden = Register.objects.create(name='Hidden Register')
        other = User.objects.create_user(username='cashier_hidden', password='***', role='cashier')
        hidden.cashiers.add(other)

        response = self.client.get('/api/sessions/registers/')

        self.assertEqual(response.status_code, 200)
        ids = [r['id'] for r in response.data]
        self.assertIn(self.register.id, ids)
        self.assertNotIn(hidden.id, ids)
        self.assertEqual(
            next(r for r in response.data if r['id'] == self.register.id)['name'],
            self.register.name,
        )
