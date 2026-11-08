from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from sales.models import Payment, Sale
from sessions.models import Session

User = get_user_model()


class SessionCloseTests(TestCase):
    def setUp(self):
        self.manager = User.objects.create_user(username='manager', password='pw', role='manager')
        self.client = APIClient()
        self.client.force_authenticate(self.manager)

    def make_session(self, starting_cash=Decimal('0.00')):
        return Session.objects.create(cashier=self.manager, starting_cash=starting_cash)

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

    def close(self, session, counts=None, **extra):
        return self.client.post(
            f'/api/sessions/{session.id}/close/',
            {'counts': counts or {}, **extra},
            format='json',
        )

    def test_close_with_no_sales_reconciles_the_starting_cash(self):
        session = self.make_session(starting_cash=Decimal('50.00'))

        response = self.close(session, {'fifties': 1})

        self.assertEqual(response.status_code, 200)
        session.refresh_from_db()
        self.assertEqual(session.expected_cash, Decimal('50.00'))
        self.assertEqual(session.counted_cash, Decimal('50.00'))
        self.assertEqual(session.variance, Decimal('0.00'))
        self.assertEqual(session.sales_total, Decimal('0.00'))

    def test_payments_on_open_sales_are_not_counted_as_cash_sales(self):
        session = self.make_session()
        sale = Sale.objects.create(cashier=self.manager, status=Sale.STATUS_OPEN, total=Decimal('30.00'))
        Payment.objects.create(sale=sale, method='cash', amount=Decimal('30.00'))

        response = self.close(session)

        self.assertEqual(response.status_code, 200)
        session.refresh_from_db()
        self.assertEqual(session.expected_cash, Decimal('0.00'))
        self.assertEqual(session.sales_total, Decimal('0.00'))

    def test_sales_created_before_the_session_are_excluded(self):
        session = self.make_session()
        old_sale = self.make_completed_sale(Decimal('30.00'), method='cash')
        Sale.objects.filter(pk=old_sale.pk).update(
            created_at=session.opened_at - timedelta(minutes=5)
        )
        current_sale = self.make_completed_sale(Decimal('10.00'), method='cash')

        response = self.close(session, {'tens': 1})

        self.assertEqual(response.status_code, 200)
        session.refresh_from_db()
        self.assertEqual(session.expected_cash, Decimal('10.00'))
        self.assertEqual(session.counted_cash, Decimal('10.00'))
        self.assertEqual(session.sales_total, Decimal('10.00'))
        self.assertEqual(Sale.objects.get(pk=current_sale.pk).status, Sale.STATUS_COMPLETED)

    def test_multiple_cash_payments_are_summed(self):
        session = self.make_session(starting_cash=Decimal('5.00'))
        self.make_completed_sale(Decimal('10.00'), method='cash')
        self.make_completed_sale(Decimal('20.00'), method='cash')
        self.make_completed_sale(Decimal('30.00'), method='cash')

        response = self.close(session, {'fifties': 1, 'tens': 1, 'fives': 1})

        self.assertEqual(response.status_code, 200)
        session.refresh_from_db()
        self.assertEqual(session.expected_cash, Decimal('65.00'))
        self.assertEqual(session.counted_cash, Decimal('65.00'))
        self.assertEqual(session.variance, Decimal('0.00'))
        self.assertEqual(session.sales_total, Decimal('60.00'))

    def test_post_ignores_read_only_status_and_totals(self):
        response = self.client.post(
            '/api/sessions/',
            {
                'starting_cash': '10.00',
                'status': 'closed',
                'expected_cash': '999.00',
                'counted_cash': '999.00',
            },
            format='json',
        )

        self.assertEqual(response.status_code, 201)
        session = Session.objects.get(pk=response.data['id'])
        self.assertEqual(session.status, Session.STATUS_OPEN)
        self.assertEqual(session.expected_cash, Decimal('0.00'))
        self.assertEqual(session.counted_cash, Decimal('0.00'))

    def test_zreport_returns_persisted_totals_after_close(self):
        session = self.make_session(starting_cash=Decimal('10.00'))
        self.close(session, {'tens': 1})

        response = self.client.get(f'/api/sessions/{session.id}/zreport/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['status'], Session.STATUS_CLOSED)
        self.assertEqual(Decimal(response.data['counted_cash']), Decimal('10.00'))
        self.assertEqual(Decimal(response.data['expected_cash']), Decimal('10.00'))

    def test_closing_persists_the_denomination_counts(self):
        session = self.make_session()
        counts = {'twenties': 2, 'tens': 1, 'quarters': 3}

        response = self.close(session, counts)

        self.assertEqual(response.status_code, 200)
        session.refresh_from_db()
        self.assertEqual(session.cash_counts, counts)
