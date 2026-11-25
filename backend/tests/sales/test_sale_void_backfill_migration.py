"""Test the ``0007_sale_refund_reason_and_more`` data-migration backfill.

The 0007 migration adds the ``refund_reason`` column, backfills ``voided_at``
on legacy (pre-0006) voided sales that never carried a timestamp, and then adds
the reverse ``voided_status_requires_voided_at`` CHECK constraint. This verifies
the backfill makes the constraint applicable to production data: a row flipped
to ``voided`` before the constraint existed (NULL ``voided_at``) must receive a
``voided_at`` timestamp when 0007 runs, so applying the migration does not fail.

Uses ``MigrationExecutor`` with a ``TransactionTestCase`` (not atomic),
mirroring Django's documented migration-testing recipe and the accounts app's
existing migration tests.
"""

from django.contrib.auth import get_user_model
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

User = get_user_model()


class SaleVoidBackfillMigrationTests(TransactionTestCase):

    migrate_from = [('sales', '0006_sale_void_audit_and_number_sequence')]
    migrate_to = [('sales', '0007_sale_refund_reason_and_more')]

    def setUp(self):
        super().setUp()
        # A real user the legacy voided sale can reference as cashier.
        self.cashier = User.objects.create_user(
            username='legacy_cashier', password='pw', role='cashier'
        )
        # Force the sales schema back to the pre-0007 state.
        self.executor = MigrationExecutor(connection)
        self.executor.migrate(self.migrate_from)
        old_apps = self.executor.loader.project_state(self.migrate_from).apps
        Sale = old_apps.get_model('sales', 'Sale')
        # The pre-0007 table still allows a voided row with voided_at IS NULL
        # (only the forward voided_at->status constraint existed).
        self.legacy = Sale.objects.create(
            cashier_id=self.cashier.id, status='voided', number=91001,
        )
        self.assertIsNone(self.legacy.voided_at)

    def tearDown(self):
        # Leave the test DB back on the latest migration for the next test.
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_to)
        super().tearDown()

    def test_backfill_stamps_voided_at_on_legacy_voided_rows(self):
        """Applying 0007 gives every legacy voided row a voided_at and the
        schema change succeeds against that data."""
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_to)  # runs backfill + adds constraint
        new_apps = executor.loader.project_state(self.migrate_to).apps
        Sale = new_apps.get_model('sales', 'Sale')

        row = Sale.objects.get(pk=self.legacy.pk)
        self.assertIsNotNone(row.voided_at)
