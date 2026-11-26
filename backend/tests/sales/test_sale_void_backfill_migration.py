"""Test the ``0014_sale_refund_reason_and_more`` data-migration backfill.

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

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from django.utils import timezone

User = get_user_model()


class SaleVoidBackfillMigrationTests(TransactionTestCase):

    migrate_from = [('sales', '0013_sale_void_audit_and_number_sequence')]
    migrate_to = [('sales', '0014_sale_refund_reason_and_more')]

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

    def test_reverse_preserves_genuine_voided_at_audit_timestamps(self):
        """Reversing 0007 must clear only the backfill-stamped rows, never a
        genuine post-0006 voided_at (real audit timestamp)."""
        MigrationExecutor(connection).migrate(self.migrate_to)  # forward: stamps self.legacy
        new_apps = MigrationExecutor(connection).loader.project_state(self.migrate_to).apps
        Sale = new_apps.get_model('sales', 'Sale')

        # A genuine voided row created after 0006 shipped carries a real audit
        # timestamp (voided_at != updated_at).
        genuine_ts = timezone.now() - timedelta(minutes=5)
        genuine = Sale.objects.create(
            cashier_id=self.cashier.id, status='voided', number=91002,
            voided_at=genuine_ts,
        )

        MigrationExecutor(connection).migrate(self.migrate_from)  # reverse: back to 0006
        old_apps = MigrationExecutor(connection).loader.project_state(self.migrate_from).apps
        Sale = old_apps.get_model('sales', 'Sale')

        # The backfilled legacy row's voided_at is cleared (pre-0007 state).
        self.assertIsNone(Sale.objects.get(pk=self.legacy.pk).voided_at)
        # The genuine timestamp survives the reverse.
        self.assertEqual(
            Sale.objects.get(pk=genuine.pk).voided_at, genuine_ts,
        )
