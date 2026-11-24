"""Test the ``0003_backfill_ledger_from_pay_on_account`` data migration.

Verifies that running the migration rebuilds each customer's ledger from its
legacy ``PayOnAccount`` rows (chronologically, with running balances and
provenance marking) and that ``Customer.balance`` reconciles to the ledger sum
for every customer — including those whose balance is pure invoice-sale credit
with no ``PayOnAccount`` rows, and those in overpayment (negative balance).

Uses the migration executor with a ``TransactionTestCase`` (non-atomic),
mirroring the recipe in ``accounts.test_admin_staff_migration`` so an
unapply/re-apply of ``0003`` actually executes against the test database.
"""

from decimal import Decimal
import importlib

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from django.utils import timezone

# Module name begins with a digit, so normal `from ... import 0003_...`
# is a syntax error; import it by string instead.
m0003 = importlib.import_module(
    'customers.migrations.0003_backfill_ledger_from_pay_on_account')


def _sign(direction):
    return Decimal('1') if direction == 'credit' else Decimal('-1')


def _ledger_sum(entries):
    return sum(e.amount * _sign(e.direction) for e in entries)


class BackfillLedgerMigrationTests(TransactionTestCase):

    migrate_from = [('customers', '0002_ledgerentry')]
    migrate_to = [('customers', '0003_backfill_ledger_from_pay_on_account')]

    def setUp(self):
        super().setUp()
        # Force the schema/data back to pre-0003 so we can seed production
        # shaped data, then migrate forward via the executor.
        self.executor = MigrationExecutor(connection)
        self.executor.migrate(self.migrate_from)
        self.old_apps = self.executor.loader.project_state(self.migrate_from).apps
        self.Customer = self.old_apps.get_model('customers', 'Customer')
        self.POA = self.old_apps.get_model('customers', 'PayOnAccount')
        self.Ledger = self.old_apps.get_model('customers', 'LedgerEntry')

    def tearDown(self):
        # Leave the test DB back on the latest migration for the next test.
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_to)
        super().tearDown()

    # ------------------------------------------------------------- helpers

    def _make_customer(self, name, balance, credit_limit='1000.00'):
        return self.Customer.objects.create(
            name=name, balance=Decimal(balance),
            credit_limit=Decimal(credit_limit),
        )

    def _make_poa(self, customer, amount, when):
        poa = self.POA.objects.create(
            customer=customer, amount=Decimal(amount),
        )
        self.POA.objects.filter(pk=poa.pk).update(created_at=when)
        return poa

    def _seed_production_shaped_data(self):
        t0 = timezone.now()
        one_day = timezone.timedelta(days=1)
        data = {}  # customer name -> (pre_balance, [amount1, amount2, ...])

        alpha = self._make_customer('alpha', '100.00')
        self._make_poa(alpha, '40.25', t0 - one_day)
        data['alpha'] = (Decimal('100.00'), [Decimal('40.25')])

        overpaid = self._make_customer('overpaid', '-25.00')
        self._make_poa(overpaid, '50.00', t0 - one_day)
        data['overpaid'] = (Decimal('-25.00'), [Decimal('50.00')])

        payonly = self._make_customer('payonly', '-30.00')
        self._make_poa(payonly, '30.00', t0 - one_day)
        data['payonly'] = (Decimal('-30.00'), [Decimal('30.00')])

        # Chronological multi-payment customer: two debits at distinct times.
        chrono = self._make_customer('chrono', '75.00')
        self._make_poa(chrono, '25.00', t0 - 2 * one_day)
        self._make_poa(chrono, '15.00', t0 - one_day)
        data['chrono'] = (Decimal('75.00'), [Decimal('25.00'), Decimal('15.00')])

        # Pure invoice-sale credit, no PayOnAccount rows at all.
        salesty = self._make_customer('salesty', '500.00')
        data['salesty'] = (Decimal('500.00'), [])

        # Zero balance, no activity: should produce no ledger rows.
        zero = self._make_customer('zero', '0.00')
        data['zero'] = (Decimal('0.00'), [])

        return data

    # ------------------------------------------------------------- tests

    def test_migration_reconciles_and_marks_every_customer(self):
        self._seed_production_shaped_data()

        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_to)
        new_apps = executor.loader.project_state(self.migrate_to).apps
        Ledger = new_apps.get_model('customers', 'LedgerEntry')
        Customer = new_apps.get_model('customers', 'Customer')

        customers = {c.name: c for c in Customer.objects.all()}
        self.assertEqual(set(customers), set(self._seed_data_names()))

        for name, customer in customers.items():
            entries = list(
                Ledger.objects.filter(customer_id=customer.pk)
                              .order_by('created_at', 'id'))
            if name == 'zero':
                # No PayOnAccount rows and zero balance: an empty ledger is the
                # correct, reconciled end state (ledger sum 0 == balance 0).
                self.assertEqual(entries, [], name)
                continue
            # Every row is explicitly marked — never a null method.
            self.assertTrue(entries, name)
            for e in entries:
                self.assertIsNotNone(e.method, f'{name}: null method')
                self.assertEqual(e.method, 'legacy', f'{name}: method')
                self.assertIsNone(e.operator, f'{name}: operator')
                self.assertIsNone(e.sale, f'{name}: sale')
            # Ledger sum reconciles to the cached balance.
            self.assertEqual(
                _ledger_sum(entries), customer.balance,
                f'{name}: ledger sum != Customer.balance')

    def _seed_data_names(self):
        return {
            'alpha', 'overpaid', 'payonly', 'chrono', 'salesty', 'zero',
        }

    def test_entry_shape_per_customer(self):
        self._seed_production_shaped_data()

        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_to)
        new_apps = executor.loader.project_state(self.migrate_to).apps
        Ledger = new_apps.get_model('customers', 'LedgerEntry')
        Customer = new_apps.get_model('customers', 'Customer')

        by_name = {c.name: c for c in Customer.objects.all()}

        # alpha: opening credit 140.25, then debit 40.25 -> running 100.00.
        entries = list(
            Ledger.objects.filter(customer_id=by_name['alpha'].pk)
                          .order_by('created_at', 'id'))
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0].direction, 'credit')
        self.assertEqual(entries[0].amount, Decimal('140.25'))
        self.assertEqual(entries[0].entry_type, 'adjustment')
        self.assertTrue(entries[0].reference.startswith('legacy-import:opening:'))
        self.assertEqual(entries[0].running_balance, Decimal('140.25'))
        self.assertEqual(entries[1].direction, 'debit')
        self.assertEqual(entries[1].amount, Decimal('40.25'))
        self.assertFalse(entries[1].reference.startswith('legacy-import:opening:'))
        self.assertEqual(entries[1].running_balance, Decimal('100.00'))
        self.assertEqual(entries[1].reference, 'payonaccount:' + str(
            self.POA.objects.get(customer_id=by_name['alpha'].pk, amount=Decimal('40.25')).pk
        ))

        # overpaid: opening credit 25, debit 50 -> running -25 (overpayment allowed).
        entries = list(
            Ledger.objects.filter(customer_id=by_name['overpaid'].pk)
                          .order_by('created_at', 'id'))
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0].amount, Decimal('25.00'))
        self.assertEqual(entries[1].running_balance, Decimal('-25.00'))

        # payonly: opening 0 (no residual) -> single debit, running -30.
        entries = list(
            Ledger.objects.filter(customer_id=by_name['payonly'].pk)
                          .order_by('created_at', 'id'))
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].amount, Decimal('30.00'))
        self.assertEqual(entries[0].running_balance, Decimal('-30.00'))

        # chrono: opening credit 115, then debits 25 and 15 -> 75.
        entries = list(
            Ledger.objects.filter(customer_id=by_name['chrono'].pk)
                          .order_by('created_at', 'id'))
        self.assertEqual(len(entries), 3)
        self.assertEqual([e.running_balance for e in entries],
                         [Decimal('115.00'), Decimal('90.00'), Decimal('75.00')])

        # salesty: pure credit -> single opening entry at 500.
        entries = list(
            Ledger.objects.filter(customer_id=by_name['salesty'].pk)
                          .order_by('created_at', 'id'))
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].amount, Decimal('500.00'))
        self.assertEqual(entries[0].running_balance, Decimal('500.00'))

        # zero: no activity -> no ledger rows.
        entries = list(
            Ledger.objects.filter(customer_id=by_name['zero'].pk))
        self.assertEqual(len(entries), 0)

    def test_payment_created_at_preserves_chronology(self):
        self._seed_production_shaped_data()
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_to)
        new_apps = executor.loader.project_state(self.migrate_to).apps
        Ledger = new_apps.get_model('customers', 'LedgerEntry')
        Customer = new_apps.get_model('customers', 'Customer')

        chrono = Customer.objects.get(name='chrono')
        poa_rows = list(self.POA.objects.filter(
            customer_id=chrono.pk).order_by('created_at', 'id'))

        # The two payment entries must carry the real payment timestamps, in
        # the same chronological order as the source PayOnAccount rows.
        payments = list(Ledger.objects.filter(
            customer_id=chrono.pk, entry_type='payment').order_by('created_at', 'id'))
        self.assertEqual(len(payments), 2)
        for payment, poa in zip(payments, poa_rows):
            self.assertEqual(payment.created_at, poa.created_at)
            self.assertEqual(payment.reference, f'payonaccount:{poa.pk}')

    def test_reverse_removes_synthetic_entries_and_leaves_balance(self):
        self._seed_production_shaped_data()
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_to)

        new_apps = executor.loader.project_state(self.migrate_to).apps
        Customer = new_apps.get_model('customers', 'Customer')
        Ledger = new_apps.get_model('customers', 'LedgerEntry')

        balances_before = {c.name: c.balance for c in Customer.objects.all()}
        # Sanity: forward actually created synthetic rows.
        self.assertGreater(Ledger.objects.filter(
            reference__startswith='payonaccount:').count(), 0)

        # Invoke the migration's reverse code directly: it must remove exactly
        # the synthetic rows this migration created while leaving
        # Customer.balance untouched (forward never changed it).
        schema_editor = connection.schema_editor().__enter__()
        try:
            m0003.unbackfill(new_apps, schema_editor)
        finally:
            schema_editor.__exit__(None, None, None)

        self.assertEqual(Ledger.objects.filter(
            reference__startswith='payonaccount:').count(), 0)
        self.assertEqual(Ledger.objects.filter(
            reference__startswith='legacy-import:opening:').count(), 0)
        # Balance unchanged by forward (and therefore by reverse).
        for name, balance in balances_before.items():
            self.assertEqual(Customer.objects.get(name=name).balance, balance)

    def test_forward_is_idempotent_via_reference_guard(self):
        self._seed_production_shaped_data()
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_to)

        new_apps = executor.loader.project_state(self.migrate_to).apps
        Customer = new_apps.get_model('customers', 'Customer')
        Ledger = new_apps.get_model('customers', 'LedgerEntry')
        count_before = Ledger.objects.count()

        # Re-run forward directly on already-migrated data. The reference
        # guard must make it a no-op (no duplicate rows, no reconcile failure).
        schema_editor = connection.schema_editor().__enter__()
        try:
            m0003.backfill(new_apps, schema_editor)
        finally:
            schema_editor.__exit__(None, None, None)

        self.assertEqual(Ledger.objects.count(), count_before)
        # Still reconciled for every customer.
        for customer in Customer.objects.all():
            entries = Ledger.objects.filter(customer_id=customer.pk)
            self.assertEqual(_ledger_sum(entries), customer.balance)
