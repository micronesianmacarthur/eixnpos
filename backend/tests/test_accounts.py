from io import StringIO
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase

from accounts.utils import is_manager

User = get_user_model()


class IsManagerTests(SimpleTestCase):
    def test_none_and_anonymous_are_not_managers(self):
        self.assertFalse(is_manager(None))
        self.assertFalse(is_manager(AnonymousUser()))

    def test_unauthenticated_user_is_not_manager(self):
        user = SimpleNamespace(
            is_authenticated=False,
            is_staff=True,
            is_superuser=True,
            role='admin',
        )

        self.assertFalse(is_manager(user))

    def test_cashier_role_is_not_manager(self):
        self.assertFalse(is_manager(User(username='c', role='cashier')))

    def test_empty_role_is_not_manager(self):
        self.assertFalse(is_manager(User(username='c', role='')))

    def test_manager_and_admin_roles_are_managers(self):
        self.assertTrue(is_manager(User(username='m', role='manager')))
        self.assertTrue(is_manager(User(username='a', role='admin')))

    def test_staff_flag_promotes_a_cashier(self):
        user = User(username='s', role='cashier', is_staff=True)

        self.assertTrue(is_manager(user))

    def test_superuser_flag_promotes_a_cashier(self):
        user = User(username='r', role='cashier', is_superuser=True)

        self.assertTrue(is_manager(user))

    def test_duck_typed_object_without_role_is_not_manager(self):
        user = SimpleNamespace(is_authenticated=True, is_staff=False, is_superuser=False)

        self.assertFalse(is_manager(user))


class UserDefaultsTests(TestCase):
    def test_new_user_defaults_to_cashier_without_password_prompts(self):
        user = User.objects.create_user(username='cashier', password='pw')

        self.assertEqual(user.role, 'cashier')
        self.assertFalse(user.must_change_password)
        self.assertFalse(user.password_changed)


class SeedAdminCommandTests(TestCase):
    def setUp(self):
        # A post_migrate signal already seeds admin/admin for fresh databases.
        User.objects.filter(username='admin').delete()

    def run_command(self):
        stdout = StringIO()
        call_command('seed_admin', stdout=stdout)
        return stdout.getvalue()

    def test_creates_admin_user_with_full_permissions(self):
        output = self.run_command()

        admin = User.objects.get(username='admin')
        self.assertIn('Created admin user.', output)
        self.assertEqual(admin.role, 'admin')
        self.assertTrue(admin.is_staff)
        self.assertTrue(admin.is_superuser)
        self.assertTrue(admin.check_password('admin'))

    def test_is_idempotent(self):
        self.run_command()
        output = self.run_command()

        self.assertIn('Admin user already exists.', output)
        self.assertEqual(User.objects.filter(username='admin').count(), 1)

    def test_does_not_overwrite_an_existing_admin_username(self):
        existing = User.objects.create_user(username='admin', password='secret', role='cashier')

        self.run_command()

        existing.refresh_from_db()
        self.assertEqual(existing.role, 'cashier')
        self.assertTrue(existing.check_password('secret'))
