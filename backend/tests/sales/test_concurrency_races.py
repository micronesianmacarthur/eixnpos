"""Real-threaded concurrency races for the sales/sessions write paths.

WHY THESE TESTS EXIST
---------------------
Review comment 195 on t_04f0b292 found (with real two-thread probes on
PostgreSQL 18) that several sale/session write paths still race:

* B1 ``TransactionService.complete_sale`` has no sale-row lock: two
  concurrent completes with different payment methods BOTH succeed and
  double-post (payments=2, movements=2).
* B2 ``set_payment``'s open-status guard runs on an unlocked view-loaded
  instance: a payment can be appended to a sale that completes concurrently.
* B3 the missing ``SaleNumberSequence`` row is re-seeded to 0 (not MAX), so
  the next number collides with sale #1 under the unique constraint.
* B4 ``SessionViewSet.close`` checks STATUS_CLOSED outside the atomic and
  never locks: two concurrent closes both 500 on the ZReportSnapshot
  unique key instead of one 200 / one 400.
* SAFE (verified by the same review): view-layer double-complete,
  concurrent full refunds, void-vs-complete, and 10-thread number
  allocation — those tests are kept here as regression guards.

Unlike ``tests/sales/test_sales_concurrency_regression.py`` (deterministic
stale-instance interleaves, safe on any backend), every test below spawns
REAL threads aligned on a ``threading.Barrier`` and asserts final
committed database state. That requires a backend that enforces
``SELECT ... FOR UPDATE``.

HOW TO RUN AGAINST POSTGRESQL (CI command)
------------------------------------------
    cd backend
    python manage.py test tests.sales.test_concurrency_races \\
        --settings=tests.settings_pg --noinput -v 2

``tests/settings_pg.py`` points ``default`` at a local PostgreSQL
instance; the Django test runner creates and destroys a scratch
``test_eixn_pos`` database. On SQLite (the dev default) every class
skips cleanly — SQLite serializes writes and treats row locks as a
no-op, so these races are unreproducible there.

DETERMINISM
-----------
Threads are aligned with a barrier, always joined, and assertions are
made on final committed DB state only. Each thread uses its own
connection (Django connections are thread-local) and its own
``APIClient`` / ``TransactionService`` instance — nothing is shared but
the fixture rows created in ``setUp``.
"""

import threading
from datetime import date
from decimal import Decimal
from unittest import skipUnless

from django.contrib.auth import get_user_model
from django.db import IntegrityError, connection
from django.test import TransactionTestCase
from rest_framework.test import APIClient

from inventory.models import StockMovement
from products.models import Product
from sales.models import Payment, Sale, SaleLine, SaleNumberSequence
from sales.services import TransactionService
from sessions.models import Register, Session, ZReportSnapshot
from suppliers.models import Supplier

User = get_user_model()

ROW_LOCKS_SUPPORTED = connection.features.has_select_for_update

RACE_TEST_COUNT = 8  # one class below per required race


def run_race(actors):
    """Run each actor in its own thread, released together by a barrier.

    Returns the list of results in actor order. Any exception an actor
    raises is captured as its result (never re-raised here) so callers
    can assert on outcomes, not crash one worker.
    """
    barrier = threading.Barrier(len(actors))
    results = [None] * len(actors)

    def worker(i, actor):
        try:
            barrier.wait(timeout=10)
            results[i] = actor()
        except Exception as exc:  # noqa: BLE001 - captured on purpose
            results[i] = exc
        finally:
            connection.close()

    threads = [
        threading.Thread(target=worker, args=(i, actor), daemon=False)
        for i, actor in enumerate(actors)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    return results


@skipUnless(
    ROW_LOCKS_SUPPORTED,
    'Requires a backend that enforces SELECT ... FOR UPDATE (PostgreSQL). '
    'SQLite serializes writes and treats row locks as a no-op, so these '
    'races cannot be reproduced there. Run with: python manage.py test '
    'tests.sales.test_concurrency_races --settings=tests.settings_pg',
)
class ConcurrencyRaceBase(TransactionTestCase):
    """Fixture + thread plumbing for the real-thread race tests."""

    # Real transactions must commit so worker threads see fixture rows and
    # each other's writes.
    serialized_rollback = False

    def setUp(self):
        super().setUp()
        # TransactionTestCase flushes the DB after each test and Django does
        # not replay RunPython data migrations, so the sequence row seeded by
        # migration 0013 exists only for the first test. Recreate it from the
        # max sale number so the suite is order-independent (review S1).
        max_number = Sale.objects.order_by('-number').values_list(
            'number', flat=True
        ).first()
        SaleNumberSequence.objects.update_or_create(
            pk=1, defaults={'last_value': max_number or 0}
        )
        self.register = Register.objects.create(name='Race Register')
        self.cashier = User.objects.create_user(
            username='race_cashier', password='pw', role='cashier'
        )
        self.manager = User.objects.create_user(
            username='race_manager', password='pw', role='manager'
        )
        supplier = Supplier.objects.create(name='Race Supplier')
        # Give the product real on-hand stock: completing a stock line posts a
        # negative StockMovement and the product_quantity_non_negative CHECK
        # rejects it against quantity=0 (review B5 fixture bug).
        self.product = Product.objects.create(
            name='Race Product',
            price=Decimal('10.00'),
            cost=Decimal('2.00'),
            supplier=supplier,
            min_stock=5,
            quantity=100,
        )

    def _make_open_sale(self, quantity=2, unit_price=Decimal('10.00')):
        """An open sale with one stock line worth quantity*unit_price."""
        sale = Sale.objects.create(cashier=self.cashier)
        SaleLine.objects.create(
            sale=sale,
            product=self.product,
            quantity=quantity,
            unit_price=unit_price,
        )
        sale.recompute()
        sale.refresh_from_db()
        return sale

    def _make_completed_sale(self, quantity=2, unit_price=Decimal('10.00')):
        """Complete a sale exactly once through the (safe) view path."""
        sale = self._make_open_sale(quantity, unit_price)
        resp = self._api_call('post', f'/api/sales/{sale.pk}/complete/')
        assert resp.status_code == 200, (
            f'fixture complete failed: {resp.status_code} {getattr(resp, "data", "")}'
        )
        sale.refresh_from_db()
        return sale

    def _api_call(self, method, path, payload=None, user=None):
        """One isolated APIClient call (client + connection are per-thread)."""
        client = APIClient()
        client.force_authenticate(user or self.cashier)
        return getattr(client, method)(path, payload or {}, format='json')

    def _api_actor(self, method, path, payload=None, user=None):
        def actor():
            response = self._api_call(method, path, payload, user=user)
            return response.status_code

        return actor

    @staticmethod
    def _code(result):
        """Normalise a race result to an HTTP-ish int (unexpected exception -> 500)."""
        return result if isinstance(result, int) else 500

    def _complete_view_codes(self, sale):
        return run_race([
            self._api_actor('post', f'/api/sales/{sale.pk}/complete/')
            for _ in range(2)
        ])


class TestViewDoubleComplete(ConcurrencyRaceBase):
    """Review probe A (safe at 032b2ae): view double-complete must post once."""

    def test_view_double_complete_records_one_payment_and_one_movement(self):
        sale = self._make_open_sale()  # $20 sale, no payment recorded
        codes = [self._code(r) for r in self._complete_view_codes(sale)]
        sale.refresh_from_db()

        self.assertEqual(sale.status, Sale.STATUS_COMPLETED)
        # The loser must be rejected, not silently double-post.
        self.assertEqual(sorted(code == 200 for code in codes), [False, True])
        self.assertEqual(
            Payment.objects.filter(sale=sale).count(), 1,
            f'codes={codes}: a $20 sale must record exactly one payment',
        )
        self.assertEqual(
            StockMovement.objects.filter(
                movement_type='sale', reference=f'sale:{sale.number}'
            ).count(),
            1,
            f'codes={codes}: a $20 stock sale must post exactly one movement',
        )


class TestServiceDoubleComplete(ConcurrencyRaceBase):
    """Review probe E (B1): two completes with different methods double-post."""

    def _service_complete_actor(self, sale, method):
        def actor():
            ts = TransactionService()
            # Per-thread service instance bound to a FRESH read of the row,
            # mirroring two independent registers completing the same sale.
            ts._current_sale = Sale.objects.get(pk=sale.pk)
            ts.complete_sale(method, sale.total, tendered=sale.total)
            return 'ok'

        return actor

    def test_service_double_complete_with_different_methods_records_one_completion(self):
        sale = self._make_open_sale()
        results = run_race([
            self._service_complete_actor(sale, 'cash'),
            self._service_complete_actor(sale, 'card'),
        ])
        sale.refresh_from_db()

        outcomes = [r if isinstance(r, str) else repr(r) for r in results]
        self.assertEqual(
            outcomes.count('ok'), 1,
            f'exactly one completion may succeed, got {outcomes}',
        )
        self.assertEqual(sale.status, Sale.STATUS_COMPLETED)
        self.assertEqual(
            Payment.objects.filter(sale=sale).count(), 1,
            f'results={outcomes}: one completion, one payment row',
        )
        self.assertEqual(
            StockMovement.objects.filter(
                movement_type='sale', reference=f'sale:{sale.number}'
            ).count(),
            1,
            f'results={outcomes}: one completion, one stock movement',
        )


class TestConcurrentRefund(ConcurrencyRaceBase):
    """Concurrent full refunds must not restock twice."""

    def test_concurrent_full_refunds_restock_exactly_once(self):
        sale = self._make_completed_sale()  # 2 x $10, cash, stock movements -2
        codes = [
            self._code(r) for r in run_race([
                self._api_actor('post', f'/api/sales/{sale.pk}/refund/', {},
                                user=self.manager)
                for _ in range(2)
            ])
        ]
        sale.refresh_from_db()

        # Exactly one refund request wins; the loser is refused.
        self.assertEqual(sorted(code == 200 for code in codes), [False, True],
                         f'codes={codes}')
        restocked = sum(
            m.quantity
            for m in StockMovement.objects.filter(
                reference=f'refund:sale:{sale.number}'
            )
            if m.quantity > 0
        )
        self.assertEqual(
            restocked, 2,
            f'codes={codes}: a 2-unit sale must be restocked by exactly 2, '
            f'got {restocked} (double restock)',
        )
        from django.db.models import Sum
        from sales.models import Refund
        self.assertEqual(
            Refund.objects.filter(sale=sale).aggregate(
                q=Sum('quantity')
            )['q'], 2,
        )


class TestVoidVsComplete(ConcurrencyRaceBase):
    """A void and a complete racing on the same open sale must not both win."""

    def test_void_and_complete_cannot_both_succeed(self):
        sale = self._make_open_sale()
        codes = [
            self._code(r) for r in run_race([
                self._api_actor('post', f'/api/sales/{sale.pk}/complete/'),
                self._api_actor('post', f'/api/sales/{sale.pk}/void/',
                                {'reason': 'race'}, user=self.manager),
            ])
        ]
        sale.refresh_from_db()

        # Exactly one mutating request succeeds.
        self.assertEqual(sorted(code in (200, 201) for code in codes),
                         [False, True], f'codes={codes}')
        # And the sale ends in exactly one terminal state with consistent money.
        self.assertIn(sale.status, (Sale.STATUS_COMPLETED, Sale.STATUS_VOIDED))
        payments = Payment.objects.filter(sale=sale, voided=False).count()
        movements = StockMovement.objects.filter(
            movement_type='sale', reference=f'sale:{sale.number}'
        ).count()
        if sale.status == Sale.STATUS_COMPLETED:
            self.assertEqual(payments, 1, f'codes={codes}')
            self.assertEqual(movements, 1, f'codes={codes}')
        else:
            self.assertEqual(payments, 0, f'codes={codes}')
            self.assertEqual(movements, 0, f'codes={codes}')


class TestNumberAllocationRace(ConcurrencyRaceBase):
    """10 concurrent Sale creates must draw 10 distinct numbers."""

    def test_ten_thread_number_allocation_assigns_unique_numbers(self):
        def actor():
            sale = Sale.objects.create(cashier=self.cashier)
            return sale.number

        numbers = [r for r in run_race([actor for _ in range(10)])
                   if isinstance(r, int)]
        self.assertEqual(len(numbers), 10)
        self.assertEqual(
            len(set(numbers)), 10, f'duplicate numbers allocated: {numbers}'
        )
        # And the sequence high-water mark covers them all.
        self.assertGreaterEqual(
            SaleNumberSequence.objects.get(pk=1).last_value, max(numbers)
        )


class TestMissingSequenceRow(ConcurrencyRaceBase):
    """Review probe C (B3): a lost sequence row must self-heal to MAX, not 0."""

    def test_missing_sequence_row_recreated_from_max_not_zero(self):
        numbers = [Sale.objects.create(cashier=self.cashier).number
                   for _ in range(3)]
        top = max(numbers)
        SaleNumberSequence.objects.get(pk=1).delete()

        next_sale = Sale.objects.create(cashier=self.cashier)

        self.assertEqual(
            next_sale.number, top + 1,
            'a lost sequence row must reseed from MAX(number), not 0',
        )
        self.assertEqual(
            Sale.objects.values('number').distinct().count(), 4,
            'no number may be reused after a sequence-row loss',
        )


class TestSessionCloseRace(ConcurrencyRaceBase):
    """Review probe B (B4): concurrent closes -> one 200, one 400, one snapshot."""

    def test_concurrent_session_close_records_exactly_one_snapshot(self):
        session = Session.objects.create(
            cashier=self.cashier,
            register=self.register,
            business_day=date.today(),
            starting_cash=Decimal('0.00'),
        )
        codes = [
            self._code(r) for r in run_race([
                self._api_actor('post', f'/api/sessions/{session.pk}/close/',
                                {'counts': {}})
                for _ in range(2)
            ])
        ]
        session.refresh_from_db()

        self.assertEqual(
            ZReportSnapshot.objects.filter(session=session).count(), 1,
            f'codes={codes}: exactly one Z-report snapshot may be filed',
        )
        self.assertEqual(session.status, Session.STATUS_CLOSED)
        self.assertEqual(
            sorted(codes), [200, 400],
            f'one close must win (200) and the retry be refused (400), got {codes}',
        )


class TestSetPaymentRacingComplete(ConcurrencyRaceBase):
    """Review probe F (B2): set_payment must not pay an already-completed sale."""

    def test_set_payment_racing_complete_records_one_payment(self):
        sale = self._make_open_sale()  # $20, no payment yet
        codes = [
            self._code(r) for r in run_race([
                self._api_actor(
                    'post', f'/api/sales/{sale.pk}/set_payment/',
                    {'method': 'cash', 'amount': '20.00', 'tendered': '20.00'},
                ),
                self._api_actor('post', f'/api/sales/{sale.pk}/complete/'),
            ])
        ]
        sale.refresh_from_db()

        self.assertEqual(sale.status, Sale.STATUS_COMPLETED)
        self.assertEqual(
            Payment.objects.filter(sale=sale).count(), 1,
            f'codes={codes}: a $20 sale completed once carries exactly one '
            f'payment, got {Payment.objects.filter(sale=sale).count()}',
        )
