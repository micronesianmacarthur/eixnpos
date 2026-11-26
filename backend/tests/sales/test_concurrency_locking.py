"""
Concurrency regression tests for sale complete / void / refund and
sale-number allocation.

WHY THESE TESTS EXIST
---------------------
The sales write paths (``Sale.save`` number allocation,
``TransactionService.complete_sale`` / ``void_sale`` / ``refund_sale``) use
read-then-write patterns with no row locking. Two concurrent requests can
therefore double-post a sale, over-refund stock, or leave a sale in a
contradictory terminal state. The fix is ``select_for_update`` inside
``transaction.atomic`` on the affected rows.

WHY SQLITE CANNOT RUN THESE TESTS
----------------------------------
SQLite serializes all writes and silently ignores
``SELECT ... FOR UPDATE`` (``connection.features.has_select_for_update``
is False). Every race below would be invisible: the tests are SKIPped on
backends without row-lock support.

HOW TO RUN AGAINST POSTGRESQL
-----------------------------
    cd backend
    python manage.py test tests.sales.test_concurrency_locking \
        --settings=tests.settings_pg

``tests/settings_pg.py`` points ``default`` at a local PostgreSQL instance
(user ``eixn``); the Django test runner creates a scratch
``test_eixn_pos`` database and drops it afterwards.

DETERMINISM
-----------
Threads are aligned with a ``threading.Barrier`` and always joined;
assertions are made on the final committed database state, never on
timing. Each thread uses its own connection (Django connections are
thread-local) and closes it before exiting.

EXPECTED RESULTS
----------------
* Against the unlocked (pre-fix) code: double-complete and
  number-allocation tests FAIL (duplicate postings / IntegrityError).
* Against the row-locked implementation: all tests pass.
"""
import threading
from decimal import Decimal
from unittest import skipUnless

from django.contrib.auth import get_user_model
from django.db import connections, connection
from django.test import TransactionTestCase
from rest_framework.test import APIClient

from customers.models import Customer  # noqa: F401  (context: sale FKs)
from inventory.models import StockMovement
from products.models import Product
from sales.models import Payment, Sale, SaleLine, SaleNumberSequence
from sales.services import TransactionError, TransactionService

User = get_user_model()

ROW_LOCKS_SUPPORTED = connection.features.has_select_for_update


@skipUnless(
    ROW_LOCKS_SUPPORTED,
    'Requires a backend that enforces SELECT ... FOR UPDATE '
    '(PostgreSQL). SQLite serializes writes and treats row locks as '
    'a no-op, so these races cannot be reproduced there. Run with: '
    'python manage.py test tests.sales.test_concurrency_locking '
    '--settings=tests.settings_pg',
)
class SaleConcurrencyTestBase(TransactionTestCase):
    """Shared fixture/thread plumbing for sale concurrency tests."""

    # Real transactions must commit so worker threads see the fixture rows
    # and each other's writes; TransactionTestCase (unlike TestCase) does
    # not wrap the test body in an outer transaction.
    serialized_rollback = False

    def setUp(self):
        super().setUp()
        # TransactionTestCase flushes the DB after each test and does not
        # replay RunPython data migrations, so the counter row seeded by
        # sales/migrations/0013_sale_void_audit_and_number_sequence.py would exist only
        # for the first test. Recreate it deterministically from the max
        # sale number so the suite is order-independent.
        max_number = Sale.objects.order_by('-number').values_list(
            'number', flat=True
        ).first()
        SaleNumberSequence.objects.update_or_create(
            pk=1, defaults={'last_value': max_number or 0}
        )
        self.cashier = User.objects.create_user(
            username='race-cashier', password='pw', role='cashier'
        )
        supplier = Product._meta.get_field('supplier').related_model
        supplier_obj = supplier.objects.create(name='Race Supplier')
        self.product = Product.objects.create(
            name='Race Product',
            price=Decimal('5.00'),
            cost=Decimal('2.50'),
            supplier=supplier_obj,
            min_stock=100,
        )

    def _make_open_sale(self, quantity=2, unit_price=Decimal('5.00')):
        """Create a completed-eligible open sale worth quantity*price."""
        sale = Sale.objects.create(cashier=self.cashier, status=Sale.STATUS_PENDING)
        SaleLine.objects.create(
            sale=sale,
            product=self.product,
            quantity=quantity,
            unit_price=unit_price,
        )
        sale.recompute()  # sets subtotal/tax_total/total, tax rate 0
        sale.refresh_from_db()
        return sale

    def _make_completed_sale(self, quantity=2, unit_price=Decimal('5.00')):
        sale = self._make_open_sale(quantity, unit_price)
        service = TransactionService()
        service.resume_sale(sale.pk)
        service.complete_sale(
            'cash', sale.total, tendered=sale.total, generate_receipt=False
        )
        sale.refresh_from_db()
        return sale

    def _run_concurrently(self, targets, barrier=None):
        """Run one callable per thread behind a shared barrier.

        Returns a list of outcome dicts, one per thread:
            {'name': str, 'result': <return value or None>,
             'error': <exception instance or None>}
        Threads always join and always close their DB connections, so the
        test never leaks a connection and never depends on wall-clock
        timing for correctness (the barrier only widens the race window
        deterministically).
        """
        if barrier is None:
            barrier = threading.Barrier(len(targets))
        outcomes = [None] * len(targets)

        def runner(index, fn):
            outcome = {'name': f'thread-{index}', 'result': None, 'error': None}
            try:
                barrier.wait(timeout=10)
                outcome['result'] = fn()
            except Exception as exc:  # noqa: BLE001 - recorded for assertions
                outcome['error'] = exc
            finally:
                connections.close_all()
            outcomes[index] = outcome

        threads = [
            threading.Thread(target=runner, args=(i, fn), name=f'sale-race-{i}')
            for i, fn in enumerate(targets)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        self.assertFalse(
            any(t.is_alive() for t in threads), 'worker threads did not finish'
        )
        self.assertTrue(
            all(o is not None for o in outcomes), 'every thread must record an outcome'
        )
        return outcomes


class DoubleCompleteRaceTest(SaleConcurrencyTestBase):
    """Two cashiers completing the same open sale at the same time.

    Exactly one complete may succeed; the loser must get the domain error,
    and the sale must be posted exactly once (one payment, one reverse-free
    set of stock movements, single completed_at).
    """

    def test_exactly_one_double_complete_succeeds(self):
        sale = self._make_open_sale(quantity=2, unit_price=Decimal('5.00'))
        total = sale.total  # 10.00

        def complete():
            service = TransactionService()
            service.resume_sale(sale.pk)
            service.complete_sale(
                'cash', total, tendered=total, generate_receipt=False
            )
            return sale.pk

        outcomes = self._run_concurrently([complete, complete])

        succeeded = [o for o in outcomes if o['error'] is None]
        failed = [o for o in outcomes if o['error'] is not None]

        self.assertEqual(
            len(succeeded), 1,
            f'exactly one complete_sale must succeed, got outcomes: {outcomes}'
        )
        self.assertEqual(
            len(failed), 1, 'the losing complete_sale must raise, got: '
            f'{[type(o["error"]).__name__ for o in failed]}'
        )
        self.assertIsInstance(
            failed[0]['error'], TransactionError,
            f'expected TransactionError, got {failed[0]["error"]!r}'
        )

        sale.refresh_from_db()
        self.assertEqual(sale.status, Sale.STATUS_COMPLETED)

        # Posted exactly once: one payment, one stock movement per line.
        self.assertEqual(
            Payment.objects.filter(sale=sale).count(), 1,
            'double-complete posted the payment twice'
        )
        movements = StockMovement.objects.filter(reference=f'sale:{sale.number}')
        self.assertEqual(
            movements.count(), 1,
            f'stock movement posted more than once for sale {sale.number}'
        )
        self.assertEqual(sale.lines.count(), 1, 'sale lines duplicated')


class ConcurrentRefundRaceTest(SaleConcurrencyTestBase):
    """Two managers refunding the same completed sale at the same time.

    The refunded total must never exceed the sale total: only one refund
    may go through, and the reverse stock movements must be created once.
    """

    def test_concurrent_refunds_do_not_over_refund(self):
        quantity = 3
        sale = self._make_completed_sale(quantity=quantity, unit_price=Decimal('5.00'))
        self.assertEqual(sale.status, Sale.STATUS_COMPLETED)

        def refund():
            service = TransactionService()
            return service.refund_sale(sale.pk, reason='race', manager_approved=True)

        outcomes = self._run_concurrently([refund, refund])

        succeeded = [o for o in outcomes if o['error'] is None]
        failed = [o for o in outcomes if o['error'] is not None]

        self.assertEqual(
            len(succeeded), 1,
            f'exactly one refund may succeed, got outcomes: {outcomes}'
        )
        for o in failed:
            self.assertIsInstance(
                o['error'], TransactionError,
                f'expected TransactionError from losing refund, got {o["error"]!r}'
            )

        sale.refresh_from_db()
        self.assertEqual(sale.status, Sale.STATUS_REFUNDED)

        # Refunded stock must equal the original quantity exactly once:
        # the reverse movements must never double the refund.
        reverse = StockMovement.objects.filter(reference=f'refund:sale:{sale.number}')
        refunded_qty = sum(m.quantity for m in reverse)
        self.assertEqual(
            refunded_qty, quantity,
            f'over-refund: refunded {refunded_qty} units of {quantity} sold'
        )
        # One reverse StockMovement per sale line carrying quantity; the
        # fixture has a single line of `quantity` units, so the correct
        # movement count is sale.lines.count(), not quantity.
        self.assertEqual(
            reverse.count(), sale.lines.count(),
            'reverse stock movements created more than once'
        )
        self.assertEqual(sale.lines.count(), 1, 'sale lines duplicated')


class SaleNumberAllocationRaceTest(SaleConcurrencyTestBase):
    """N concurrent sales must allocate N distinct, gapless numbers.

    ``Sale.save`` allocates ``MAX(number) + 1`` outside any transaction or
    lock, so concurrent creates can read the same MAX and either post the
    same number (unique violation) or leave gaps after rollbacks. A correct
    implementation reserves the number atomically (counter row + row lock).
    """

    N_THREADS = 10

    def test_concurrent_starts_allocate_distinct_gapless_numbers(self):
        n = self.N_THREADS

        def start_sale():
            sale = Sale.objects.create(
                cashier=self.cashier, status=Sale.STATUS_PENDING
            )
            return sale.number

        outcomes = self._run_concurrently([start_sale] * n)

        errors = [o['error'] for o in outcomes if o['error'] is not None]
        self.assertEqual(
            errors, [],
            f'{len(errors)}/{n} concurrent number allocations failed '
            f'(read-then-write MAX(number)+1 race): {errors!r}'
        )

        numbers = sorted(o['result'] for o in outcomes)
        self.assertEqual(
            len(set(numbers)), n,
            f'duplicate sale numbers allocated: {numbers}'
        )
        self.assertEqual(
            numbers, list(range(numbers[0], numbers[0] + n)),
            f'sale numbers are not gapless: {numbers}'
        )

        # Ground truth in the DB: every created row has a unique number.
        db_numbers = list(Sale.objects.order_by('number').values_list('number', flat=True))
        self.assertEqual(len(db_numbers), n)
        self.assertEqual(len(set(db_numbers)), n, f'duplicate numbers stored: {db_numbers}')


class VoidCompleteRaceTest(SaleConcurrencyTestBase):
    """One cashier voids while another completes the same open sale.

    The sale must end in exactly one consistent terminal state: either
    completed (payment + stock movements posted) or voided (nothing
    posted) — never both, never half of each.
    """

    def test_void_vs_complete_yields_single_terminal_state(self):
        sale = self._make_open_sale(quantity=2, unit_price=Decimal('5.00'))
        total = sale.total

        def void_it():
            service = TransactionService()
            service.resume_sale(sale.pk)
            service.void_sale()
            return Sale.STATUS_VOIDED

        def complete_it():
            service = TransactionService()
            service.resume_sale(sale.pk)
            service.complete_sale(
                'cash', total, tendered=total, generate_receipt=False
            )
            return Sale.STATUS_COMPLETED

        outcomes = self._run_concurrently([void_it, complete_it])

        succeeded = [o for o in outcomes if o['error'] is None]
        failed = [o for o in outcomes if o['error'] is not None]

        self.assertEqual(
            len(succeeded), 1,
            f'exactly one of void/complete may win, got outcomes: {outcomes}'
        )
        self.assertEqual(
            len(failed), 1,
            'the losing operation must raise, got: '
            f'{[type(o["error"]).__name__ for o in failed]}'
        )
        for o in failed:
            self.assertIsInstance(
                o['error'], TransactionError,
                f'expected TransactionError, got {o["error"]!r}'
            )

        sale.refresh_from_db()
        self.assertIn(
            sale.status,
            (Sale.STATUS_COMPLETED, Sale.STATUS_VOIDED),
            f'no single terminal state: status is {sale.status!r}',
        )

        payments = Payment.objects.filter(sale=sale)
        movements = StockMovement.objects.filter(reference=f'sale:{sale.number}')

        if sale.status == Sale.STATUS_VOIDED:
            # A voided sale must have nothing posted against it.
            self.assertEqual(
                payments.count(), 0,
                'voided sale carries a payment: complete and void both took effect'
            )
            self.assertEqual(
                movements.count(), 0,
                'voided sale carries stock movements: complete and void both took effect'
            )
        else:  # COMPLETED
            self.assertEqual(
                payments.count(), 1,
                'completed sale must have exactly one payment'
            )
            self.assertEqual(movements.count(), 1)


class ApiDoubleCompleteRaceTest(SaleConcurrencyTestBase):
    """View-layer double-complete races: two concurrent API requests.

    The service-level tests above only exercise TransactionService. The
    complete view adds its own payment/movement writes outside the locked
    path, so the remaining double-posts are only visible through the API.
    """

    def _post_complete_concurrently(self, sale, payload):
        """Two threads POST /api/sales/{id}/complete/ behind a barrier.

        Each thread gets its own APIClient and closes its DB connection
        afterwards. Returns a list of outcome dicts with the HTTP status
        code, mirroring _run_concurrently's contract.
        """
        url = f'/api/sales/{sale.pk}/complete/'
        barrier = threading.Barrier(2)
        outcomes = [None] * 2

        def runner(index):
            outcome = {
                'name': f'thread-{index}',
                'status': None,
                'body': None,
                'error': None,
            }
            try:
                client = APIClient()
                client.force_authenticate(self.cashier)
                barrier.wait(timeout=10)
                response = client.post(url, payload, format='json')
                outcome['status'] = response.status_code
                outcome['body'] = getattr(response, 'data', None)
            except Exception as exc:  # noqa: BLE001 - recorded for assertions
                outcome['error'] = exc
            finally:
                connections.close_all()
            outcomes[index] = outcome

        threads = [
            threading.Thread(target=runner, args=(i,), name=f'sale-api-race-{i}')
            for i in range(2)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        self.assertFalse(
            any(t.is_alive() for t in threads), 'worker threads did not finish'
        )
        self.assertTrue(
            all(o is not None for o in outcomes), 'every thread must record an outcome'
        )
        for o in outcomes:
            self.assertIsNone(
                o['error'],
                f'{o["name"]} raised {o["error"]!r}',
            )
            # Pre-fix, the losing request may legitimately see 400 (its
            # payment check fails after the winner recorded a payment) or
            # 200; the race signal is the posted-row count asserted below,
            # not the status code.
            self.assertIn(
                o['status'], (200, 400),
                f'{o["name"]} got unexpected HTTP {o["status"]}: {o["body"]}',
            )
        return outcomes

    def test_api_double_complete_posts_one_payment(self):
        sale = self._make_open_sale(quantity=1, unit_price=Decimal('10.00'))
        self.assertEqual(sale.total, Decimal('10.00'))
        self.assertEqual(
            Payment.objects.filter(sale=sale).count(), 0,
            'fixture must start with no payment rows'
        )

        self._post_complete_concurrently(sale, {})

        sale.refresh_from_db()
        self.assertEqual(sale.status, Sale.STATUS_COMPLETED)
        self.assertEqual(
            Payment.objects.filter(sale=sale).count(), 1,
            'double-complete via the API posted more than one payment'
        )

    def test_api_double_complete_zero_value_posts_one_movement(self):
        sale = self._make_open_sale(quantity=1, unit_price=Decimal('0.00'))
        self.assertEqual(sale.total, Decimal('0.00'))
        self.assertEqual(
            StockMovement.objects.filter(reference=f'sale:{sale.number}').count(),
            0,
            'fixture must start with no stock movements',
        )

        self._post_complete_concurrently(sale, {'note': 'race'})

        sale.refresh_from_db()
        self.assertEqual(sale.status, Sale.STATUS_COMPLETED)
        self.assertEqual(
            StockMovement.objects.filter(reference=f'sale:{sale.number}').count(),
            1,
            'double-complete of a zero-value sale posted more than one '
            'stock movement',
        )
