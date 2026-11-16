from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.test import TestCase
from rest_framework.test import APIClient

from sales.models import Payment, Sale, SaleLine
from sessions.models import Register, Session

User = get_user_model()


class SessionCloseTests(TestCase):
    def setUp(self):
        self.manager = User.objects.create_user(username='manager', password='pw', role='manager')
        self.client = APIClient()
        self.client.force_authenticate(self.manager)
        self.register = Register.objects.order_by('id').first() or Register.objects.create(name='Test Register')
        self.today = date.today()

    def make_session(self, starting_cash=Decimal('0.00')):
        return Session.objects.create(
            cashier=self.manager, starting_cash=starting_cash,
            register=self.register, business_day=self.today,
        )

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

    def test_zreport_returns_pdf_after_close(self):
        session = self.make_session(starting_cash=Decimal('10.00'))
        self.close(session, {'tens': 1})

        response = self.client.get(f'/api/sessions/{session.id}/zreport/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/pdf')
        self.assertTrue(response.content.startswith(b'%PDF'))
        session.refresh_from_db()
        self.assertEqual(session.status, Session.STATUS_CLOSED)
        self.assertEqual(session.counted_cash, Decimal('10.00'))

    def test_closing_persists_the_denomination_counts(self):
        session = self.make_session()
        counts = {'twenties': 2, 'tens': 1, 'quarters': 3}

        response = self.close(session, counts)

        self.assertEqual(response.status_code, 200)
        session.refresh_from_db()
        self.assertEqual(session.cash_counts, counts)


class SessionSummaryEndpointTests(TestCase):
    def setUp(self):
        self.manager = User.objects.create_user(username='manager', password='pw', role='manager')
        self.client = APIClient()
        self.client.force_authenticate(self.manager)
        self.register = Register.objects.order_by('id').first() or Register.objects.create(name='Test Register')
        self.session = Session.objects.create(
            cashier=self.manager, starting_cash=Decimal('50.00'),
            register=self.register, business_day=date.today(),
        )

    def make_completed_sale(self, total, subtotal=None, cost=Decimal('4.00'), method='cash'):
        from products.models import Product
        from suppliers.models import Supplier
        supplier = Supplier.objects.create(name='Acme')
        product = Product.objects.create(name='Widget', price=Decimal('10.00'), cost=cost, supplier=supplier)
        sale = Sale.objects.create(
            cashier=self.manager,
            status=Sale.STATUS_COMPLETED,
            subtotal=subtotal if subtotal is not None else total,
            total=total,
        )
        Payment.objects.create(sale=sale, method=method, amount=total)
        SaleLine.objects.create(sale=sale, product=product, quantity=1, unit_price=product.price)
        return sale

    def test_summary_endpoint_returns_revenue_and_profit_loss(self):
        self.make_completed_sale(Decimal('10.00'))

        response = self.client.get(f'/api/sessions/{self.session.id}/summary/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['session']['id'], self.session.id)
        self.assertEqual(Decimal(response.data['summary']['revenue']), Decimal('10.00'))
        # revenue(10) - cost_of_goods(4) = profit 6
        self.assertEqual(Decimal(response.data['summary']['profit_loss']), Decimal('6.00'))

    def test_summary_endpoint_empty_session_returns_zeroes(self):
        response = self.client.get(f'/api/sessions/{self.session.id}/summary/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Decimal(response.data['summary']['revenue']), Decimal('0.00'))
        self.assertEqual(Decimal(response.data['summary']['profit_loss']), Decimal('0.00'))
        self.assertEqual(response.data['summary']['sale_count'], 0)


class SessionCurrentTests(TestCase):
    def setUp(self):
        self.manager = User.objects.create_user(username='manager', password='pw', role='manager')
        self.client = APIClient()
        self.client.force_authenticate(self.manager)
        self.register = Register.objects.order_by('id').first() or Register.objects.create(name='Test Register')
        self.today = date.today()

    def test_current_returns_not_open_when_no_session(self):
        response = self.client.get('/api/sessions/current/')

        self.assertEqual(response.status_code, 200)
        self.assertIs(response.data['open'], False)
        self.assertNotIn('sessionId', response.data)
        self.assertNotIn('startedAt', response.data)
        self.assertNotIn('startingCash', response.data)

    def test_current_reports_open_session_details(self):
        session = Session.objects.create(
            cashier=self.manager, starting_cash=Decimal('150.00'),
            register=self.register, business_day=self.today,
        )

        response = self.client.get('/api/sessions/current/')

        self.assertEqual(response.status_code, 200)
        self.assertIs(response.data['open'], True)
        self.assertEqual(response.data['sessionId'], str(session.id))
        self.assertEqual(response.data['startingCash'], '150.00')
        self.assertEqual(response.data['startedAt'], session.opened_at.isoformat())

    def test_current_ignores_closed_sessions(self):
        Session.objects.create(cashier=self.manager, starting_cash=Decimal('10.00'),
                               register=self.register, business_day=self.today,
                               status=Session.STATUS_CLOSED)

        response = self.client.get('/api/sessions/current/')

        self.assertEqual(response.status_code, 200)
        self.assertIs(response.data['open'], False)

    def test_current_requires_authentication(self):
        anon = APIClient()

        response = anon.get('/api/sessions/current/')

        self.assertEqual(response.status_code, 403)
        self.assertIn('Authentication credentials', response.data['detail'])


class SessionStartTests(TestCase):
    def setUp(self):
        self.manager = User.objects.create_user(username='manager', password='pw', role='manager')
        self.client = APIClient()
        self.client.force_authenticate(self.manager)
        self.register = Register.objects.order_by('id').first() or Register.objects.create(name='Test Register')
        self.today = date.today()

    def _post(self, payload):
        return self.client.post('/api/sessions/', payload, format='json')


    def test_creates_an_open_session_for_the_cashier(self):
        response = self._post({'starting_cash': '100.00'})

        self.assertEqual(response.status_code, 201)
        session = Session.objects.get(pk=response.data['id'])
        self.assertEqual(session.cashier, self.manager)
        self.assertEqual(session.status, Session.STATUS_OPEN)
        self.assertEqual(session.starting_cash, Decimal('100.00'))

    def test_requires_starting_cash(self):
        response = self._post({})

        self.assertEqual(response.status_code, 400)
        self.assertIn('starting_cash', response.data)
        self.assertEqual(Session.objects.count(), 0)

    def test_rejects_negative_starting_cash(self):
        response = self._post({'starting_cash': '-5.00'})

        self.assertEqual(response.status_code, 400)
        self.assertIn('starting_cash', response.data)
        self.assertEqual(Session.objects.count(), 0)

    def test_rejects_more_than_two_decimals(self):
        response = self._post({'starting_cash': '10.999'})

        self.assertEqual(response.status_code, 400)
        self.assertIn('starting_cash', response.data)
        self.assertEqual(Session.objects.count(), 0)

    def test_rejects_when_a_session_is_already_open(self):
        Session.objects.create(cashier=self.manager, starting_cash=Decimal('50.00'),
                               register=self.register, business_day=self.today)

        response = self._post({'starting_cash': '100.00'})

        self.assertEqual(response.status_code, 409)
        self.assertIn('already open', response.data['detail'])
        # No second session may be persisted on the same register/day.
        self.assertEqual(
            Session.objects.filter(register=self.register, business_day=self.today,
                                   status=Session.STATUS_OPEN).count(), 1,
        )

    def test_new_session_allowed_after_closing_the_open_one(self):
        session = Session.objects.create(cashier=self.manager, starting_cash=Decimal('50.00'),
                                         register=self.register, business_day=self.today)
        self.client.post(
            f'/api/sessions/{session.id}/close/', {'counts': {'fifties': 1}}, format='json'
        )

        response = self._post({'starting_cash': '200.00'})

        self.assertEqual(response.status_code, 201)
        self.assertEqual(
            Session.objects.filter(register=self.register, business_day=self.today,
                                   status=Session.STATUS_OPEN).count(), 1,
        )

    def test_create_requires_authentication(self):
        anon = APIClient()

        response = anon.post('/api/sessions/', {'starting_cash': '100.00'}, format='json')

        self.assertEqual(response.status_code, 403)
        self.assertEqual(Session.objects.count(), 0)

    def test_db_constraint_enforces_one_open_per_register_day(self):
        Session.objects.create(cashier=self.manager, starting_cash=Decimal('50.00'),
                               register=self.register, business_day=self.today)

        # A second open on the same register/day is rejected at the DB layer.
        # The inner atomic isolates the failed insert so the outer test
        # transaction stays usable for the assertions below.
        from django.db import transaction
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Session.objects.create(cashier=self.manager, starting_cash=Decimal('60.00'),
                                       register=self.register, business_day=self.today)

        # But another register/day is unaffected — the global one-session limit
        # is gone.
        other_register = Register.objects.create(name='Other')
        s2 = Session.objects.create(cashier=self.manager, starting_cash=Decimal('60.00'),
                                    register=other_register, business_day=self.today)
        self.assertEqual(s2.status, Session.STATUS_OPEN)

