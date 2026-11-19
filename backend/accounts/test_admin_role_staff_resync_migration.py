"""Test the ``0005_admin_role_staff_resync`` data migration.

Verifies is_staff is resynced from role in both directions: admin roles gain
staff (B2 / API-created admins), stale staff on non-admin non-superuser rows is
revoked (B1 / demotion), already-consistent rows are untouched, and superusers
stay staff regardless of role. Uses the migration executor with a
``TransactionTestCase`` (not atomic), mirroring the 0004 migration tests.
"""

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class AdminRoleStaffResyncMigrationTests(TransactionTestCase):

    migrate_from = [('accounts', '0004_admin_role_to_staff')]
    migrate_to = [('accounts', '0005_admin_role_staff_resync')]

    def setUp(self):
        super().setUp()
        self.executor = MigrationExecutor(connection)
        self.executor.migrate(self.migrate_from)
        old_apps = self.executor.loader.project_state(self.migrate_from).apps
        User = old_apps.get_model('accounts', 'User')
        self.admin_no_staff = User.objects.create(
            username='rs_admin', role='admin', is_staff=False, is_superuser=False
        )
        self.cashier_stale = User.objects.create(
            username='rs_cashier', role='cashier', is_staff=True, is_superuser=False
        )
        self.admin_staff = User.objects.create(
            username='rs_admin_s', role='admin', is_staff=True, is_superuser=False
        )
        self.superuser = User.objects.create(
            username='rs_super', role='cashier', is_staff=True, is_superuser=True
        )

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_to)
        super().tearDown()

    def test_resync_both_directions_and_keep_superusers_staff(self):
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_to)
        new_apps = executor.loader.project_state(self.migrate_to).apps
        User = new_apps.get_model('accounts', 'User')

        admin = User.objects.get(username='rs_admin')
        cashier = User.objects.get(username='rs_cashier')
        admin_staff = User.objects.get(username='rs_admin_s')
        superuser = User.objects.get(username='rs_super')

        # admin role -> is_staff (B2: API-created admins now reach /admin/).
        self.assertTrue(admin.is_staff)
        self.assertFalse(admin.is_superuser)

        # stale staff on a non-admin, non-superuser -> revoked (B1: demotion).
        self.assertFalse(cashier.is_staff)

        # Already-consistent rows are untouched.
        self.assertTrue(admin_staff.is_staff)
        self.assertFalse(admin_staff.is_superuser)

        # Superusers stay staff regardless of role.
        self.assertTrue(superuser.is_staff)
        self.assertTrue(superuser.is_superuser)
