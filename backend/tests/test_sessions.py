from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from products.models import Product
from sales.models import Payment, Sale, SaleLine
from sales.summary import compute_session_summary
from sessions.models import Register, Session, ZReportSnapshot
from suppliers.models import Supplier

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


class SessionSaleAttributionTests(TestCase):
    """Sale->session attribution and close/summary/Z-report scoping.

    Regression for the review blocker: now that overlapping open sessions are
    legal (one per register/business day), money paths must attribute sales to
    the session they belong to via ``Sale.session``, not by the session's time
    window alone. Probes from the review are re-run here and must produce
    ``sales_total=0.00`` for the session that had no sales.
    """

    def setUp(self):
        self.manager = User.objects.create_user(username='manager', password='pw', role='manager')
        self.client = APIClient()
        self.client.force_authenticate(self.manager)
        self.register = Register.objects.order_by('id').first() or Register.objects.create(name='Test Register')
        self.today = date.today()

    def open_session(self, register=None, business_day=None, starting_cash=Decimal('0.00')):
        return Session.objects.create(
            cashier=self.manager, starting_cash=starting_cash,
            register=register or self.register,
            business_day=business_day or self.today,
        )

    def completed_sale(self, total, session=None):
        """Create a completed sale with a cash payment, optionally attributed."""
        sale = Sale.objects.create(
            cashier=self.manager, status=Sale.STATUS_COMPLETED,
            subtotal=total, total=total, session=session,
        )
        Payment.objects.create(sale=sale, method='cash', amount=total)
        return sale

    def close(self, session, counts=None):
        return self.client.post(
            f'/api/sessions/{session.id}/close/',
            {'counts': counts or {}},
            format='json',
        )

    def test_sale_creation_attributes_to_open_session_on_register_day(self):
        """POST /api/sales/ sets Sale.session to the cashier's open session."""
        session = self.open_session(starting_cash=Decimal('50.00'))

        response = self.client.post('/api/sales/', {'cashier': self.manager.id}, format='json')

        self.assertEqual(response.status_code, 201)
        sale = Sale.objects.get(pk=response.data['id'])
        self.assertEqual(sale.session, session)

    def test_sale_creation_leaves_session_null_when_no_session_open(self):
        """No open session for the cashier's register/day -> session stays NULL
        (server-side session enforcement is out of scope on this line)."""
        response = self.client.post('/api/sales/', {'cashier': self.manager.id}, format='json')

        self.assertEqual(response.status_code, 201)
        sale = Sale.objects.get(pk=response.data['id'])
        self.assertIsNone(sale.session)

    def test_close_does_not_absorb_sale_from_sibling_register(self):
        """Closing register A must not pick up a sale attributed to register B,
        even though B's sale falls inside A's time window."""
        register_b = Register.objects.create(name='Register B')
        session_a = self.open_session(starting_cash=Decimal('100.00'))
        session_b = self.open_session(register=register_b, starting_cash=Decimal('0.00'))
        # A $10 sale actually made on register B.
        self.completed_sale(Decimal('10.00'), session=session_b)

        close_a = self.close(session_a, {'hundreds': 1})  # 100 counted, no cash sales
        self.assertEqual(close_a.status_code, 200)

        session_a.refresh_from_db()
        self.assertEqual(session_a.sales_total, Decimal('0.00'))
        self.assertEqual(session_a.expected_cash, Decimal('100.00'))
        # The sale is owned by B's session only.
        self.assertEqual(
            Sale.objects.filter(session=session_b, status=Sale.STATUS_COMPLETED).count(), 1,
        )

        # Closing B reports its own $10; summary and close() agree.
        close_b = self.close(session_b, {'tens': 1})
        self.assertEqual(close_b.status_code, 200)
        session_b.refresh_from_db()
        self.assertEqual(session_b.sales_total, Decimal('10.00'))
        self.assertEqual(session_b.expected_cash, Decimal('10.00'))
        self.assertEqual(
            Decimal(compute_session_summary(session_b.id)['summary']['revenue']),
            session_b.sales_total,
        )
        # And A's summary/Z-report agree with A's close() (zero revenue).
        self.assertEqual(
            Decimal(compute_session_summary(session_a.id)['summary']['revenue']),
            session_a.sales_total,
        )

    def test_close_of_yesterday_open_session_does_not_absorb_todays_sale(self):
        """Midnight handoff on one register: yesterday's session is still open
        and today's session is open on the same register. A sale made today is
        attributed to today's session, so closing yesterday's must report zero.
        """
        yesterday = self.today - timedelta(days=1)
        old_session = self.open_session(
            starting_cash=Decimal('100.00'), business_day=yesterday,
        )
        today_session = self.open_session(starting_cash=Decimal('0.00'))
        # $10 cash sale made today, attributed to today's open session.
        self.completed_sale(Decimal('10.00'), session=today_session)

        close_old = self.close(old_session, {'hundreds': 1})
        self.assertEqual(close_old.status_code, 200)
        old_session.refresh_from_db()
        self.assertEqual(old_session.sales_total, Decimal('0.00'))
        self.assertEqual(old_session.expected_cash, Decimal('100.00'))

        # The sale still lives on today's session.
        self.assertEqual(
            Sale.objects.filter(session=today_session, status=Sale.STATUS_COMPLETED).count(), 1,
        )
        # Today's summary counts exactly the $10 and agrees with what a close
        # would compute.
        self.assertEqual(
            Decimal(compute_session_summary(today_session.id)['summary']['revenue']),
            Decimal('10.00'),
        )


class ZReportSnapshotTests(TestCase):
    """The Z-report snapshot filed at close is immutable and a re-download.

    Regression for: re-downloading a closed shift's Z-report must return the
    exact figures filed at close — even after later refunds or product edits
    change the live data — and a legacy closed shift with no snapshot must be
    handled explicitly (404) rather than silently recomputed from live data.
    """

    def setUp(self):
        self.manager = User.objects.create_user(username='manager', password='pw', role='manager')
        self.client = APIClient()
        self.client.force_authenticate(self.manager)
        self.register = Register.objects.order_by('id').first() or Register.objects.create(name='Test Register')
        self.today = date.today()
        supplier = Supplier.objects.create(name='Acme')
        self.product = Product.objects.create(
            name='Widget', price=Decimal('10.00'), cost=Decimal('4.00'),
            supplier=supplier,
        )

    def make_session(self, starting_cash=Decimal('0.00')):
        return Session.objects.create(
            cashier=self.manager, starting_cash=starting_cash,
            register=self.register, business_day=self.today,
        )

    def make_completed_sale(self, total, session):
        sale = Sale.objects.create(
            cashier=self.manager, status=Sale.STATUS_COMPLETED,
            subtotal=total, total=total, session=session,
        )
        Payment.objects.create(sale=sale, method='cash', amount=total)
        SaleLine.objects.create(
            sale=sale, product=self.product, quantity=1, unit_price=self.product.price,
        )
        return sale

    def close(self, session, counts=None):
        return self.client.post(
            f'/api/sessions/{session.id}/close/', {'counts': counts or {}}, format='json',
        )

    def test_close_files_a_snapshot_recording_the_filed_figures(self):
        session = self.make_session()
        self.make_completed_sale(Decimal('10.00'), session)  # revenue 10, cogs 4, profit 6

        response = self.close(session)
        self.assertEqual(response.status_code, 200)

        snapshot = ZReportSnapshot.objects.get(session=session)
        self.assertEqual(
            Decimal(snapshot.summary_data['summary']['revenue']), Decimal('10.00'),
        )
        self.assertEqual(
            Decimal(snapshot.summary_data['summary']['profit_loss']), Decimal('6.00'),
        )
        self.assertIn('store_name', snapshot.summary_data)

    def test_redownload_returns_identical_pdf_even_after_data_changes(self):
        session = self.make_session()
        self.make_completed_sale(Decimal('10.00'), session)
        self.close(session)

        first = self.client.get(f'/api/sessions/{session.id}/zreport/')
        self.assertEqual(first.status_code, 200)

        # The live data changes after close: the sole sale is entirely removed,
        # so any live recomputation would now report zero revenue.
        Sale.objects.filter(session=session).delete()
        self.assertEqual(
            Decimal(compute_session_summary(session.id)['summary']['revenue']),
            Decimal('0.00'),
        )

        second = self.client.get(f'/api/sessions/{session.id}/zreport/')
        self.assertEqual(second.status_code, 200)
        # The snapshot is unscathed: the re-download is byte-identical to the
        # report that was filed at close.
        self.assertEqual(second.content, first.content)

    def test_redownload_matches_a_render_of_the_stored_snapshot(self):
        session = self.make_session()
        self.make_completed_sale(Decimal('10.00'), session)
        self.close(session)
        snapshot = ZReportSnapshot.objects.get(session=session)

        response = self.client.get(f'/api/sessions/{session.id}/zreport/')
        self.assertEqual(response.status_code, 200)

        from sales.zreport import build_zreport_pdf
        self.assertEqual(response.content, build_zreport_pdf(snapshot.summary_data))

    def test_legacy_closed_shift_without_snapshot_returns_404(self):
        session = self.make_session()
        # A shift closed before Z-report snapshots existed: closed status but
        # no ZReportSnapshot row (the row is only written by the API close).
        session.status = Session.STATUS_CLOSED
        session.closed_at = timezone.now()
        session.save()

        response = self.client.get(f'/api/sessions/{session.id}/zreport/')

        self.assertEqual(response.status_code, 404)
        self.assertIn('snapshot', response.data['detail'].lower())
        self.assertFalse(ZReportSnapshot.objects.filter(session=session).exists())

    def test_open_shift_still_serves_a_live_preview_pdf(self):
        session = self.make_session()
        self.make_completed_sale(Decimal('10.00'), session)

        response = self.client.get(f'/api/sessions/{session.id}/zreport/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/pdf')
        self.assertTrue(response.content.startswith(b'%PDF'))

