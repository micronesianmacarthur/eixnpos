"""Test the ``0004_admin_role_to_staff`` data migration.

Verifies that running the migration maps every existing ``role='admin'`` user
to ``is_staff=True`` (without granting Django superuser) while leaving
non-admin roles untouched. Uses the migration executor with a
``TransactionTestCase`` (not atomic), mirroring Django's documented migration
testing recipe, so an unapply/re-apply of ``0004`` actually runs against the
test database.
"""

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class AdminRoleToStaffMigrationTests(TransactionTestCase):

    migrate_from = [('accounts', '0003_backfill_must_change_password')]
    migrate_to = [('accounts', '0004_admin_role_to_staff')]

    def setUp(self):
        super().setUp()
        # Force the schema/data back to the pre-0004 state so we can seed rows
        # the way they existed before the migration, then migrate forward.
        self.executor = MigrationExecutor(connection)
        self.executor.migrate(self.migrate_from)
        old_apps = self.executor.loader.project_state(self.migrate_from).apps
        User = old_apps.get_model('accounts', 'User')
        self.admin_before = User.objects.create(
            username='migrate_admin', role='admin', is_staff=False, is_superuser=False
        )
        self.cashier_before = User.objects.create(
            username='migrate_cashier', role='cashier', is_staff=False, is_superuser=False
        )

    def tearDown(self):
        # Leave the test DB back on the latest migration for the next test.
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_to)
        super().tearDown()

    def test_admin_role_users_become_staff_not_superuser(self):
        # Apply 0004.
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_to)
        new_apps = executor.loader.project_state(self.migrate_to).apps
        User = new_apps.get_model('accounts', 'User')

        admin = User.objects.get(username='migrate_admin')
        cashier = User.objects.get(username='migrate_cashier')

        self.assertTrue(admin.is_staff)
        self.assertFalse(admin.is_superuser)
        self.assertFalse(cashier.is_staff)
        self.assertFalse(cashier.is_superuser)

    def test_migration_is_idempotent_on_existing_staff_admin(self):
        # A role='admin' user already staff stays staff (no error, no reset).
        old_apps = self.executor.loader.project_state(self.migrate_from).apps
        User = old_apps.get_model('accounts', 'User')
        User.objects.filter(username='migrate_admin').update(is_staff=True)

        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_to)
        new_apps = executor.loader.project_state(self.migrate_to).apps
        UserNew = new_apps.get_model('accounts', 'User')
        admin = UserNew.objects.get(username='migrate_admin')
        self.assertTrue(admin.is_staff)
        self.assertFalse(admin.is_superuser)
