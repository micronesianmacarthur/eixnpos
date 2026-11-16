import glob
import os
import tempfile
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from business.models import BusinessInfo
from products.models import Product
from sales.models import Payment, Sale
from suppliers.models import Supplier

from .models import Session

User = get_user_model()


class SessionApiTests(TestCase):
    def setUp(self):
        self.manager = User.objects.create_user(username='manager', password='pw', role='manager')
        self.client = APIClient()
        self.client.force_authenticate(self.manager)

        supplier = Supplier.objects.create(name='Acme')
        self.product = Product.objects.create(name='Widget', price=Decimal('10.00'), supplier=supplier)

    def make_session(self, starting_cash=Decimal('0.00'), **kwargs):
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

    def test_current_filters_by_cashier(self):
        other = User.objects.create_user(
            username='cashier_b', password='pw', role='cashier'
        )
        other_client = APIClient()
        other_client.force_authenticate(other)
        # Cashier B has an open session; cashier A (self.manager) has none.
        Session.objects.create(
            cashier=other, starting_cash=Decimal('20.00'), status=Session.STATUS_OPEN
        )

        response = self.client.get('/api/sessions/current/')
        other_response = other_client.get('/api/sessions/current/')

        self.assertEqual(response.data, {'open': False})
        self.assertEqual(other_response.data['open'], True)
        self.assertEqual(
            other_response.data['sessionId'],
            str(Session.objects.get(cashier=other).id),
        )

    def test_current_requires_authentication(self):
        anonymous = APIClient()

        response = anonymous.get('/api/sessions/current/')

        self.assertIn(response.status_code, (401, 403))
