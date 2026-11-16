"""End-to-end integration test for the forced-password-change flow.

Demonstrates the full backend flow for the seeded default admin account
(``admin`` / ``admin``) against the real HTTP layer, matching the acceptance
criteria in ``docs/designs/force-password-change.md`` §8.

The seeded admin is created by a ``post_migrate`` signal (see
``accounts/apps.py``) with ``must_change_password=True``, so the the admin
user already exists and carries the flag when these tests run.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

User = get_user_model()


class ForcedPasswordChangeFlowTests(TestCase):
    def setUp(self):
        # The S4-seeded admin is gated off in tests (DEBUG is forced False by
        # the test runner), so create the default account explicitly the same
        # way the post_migrate seed would: admin/admin, must_change_password.
        if not User.objects.filter(username='admin').exists():
            admin = User(username='admin', role='admin', must_change_password=True)
            admin.set_password('admin')
            admin.save()
        self.client = APIClient()

    def _login(self, username='admin', password='admin'):
        return self.client.post(
            '/api/auth/login/',
            {'username': username, 'password': password},
            format='json',
        )

    def test_default_admin_is_forced_to_change_password(self):
        """Full flow: block → validate → change → re-login → restore access."""

        # 1. Login with the default credential succeeds and reports the flag.
        login = self._login()
        self.assertEqual(login.status_code, 200)
        self.assertTrue(login.data['password_change_required'])

        # 2. Protected routes reject the request while the flag is set.
        blocked = self.client.get('/api/auth/business-info/')
        self.assertEqual(blocked.status_code, 403)
        self.assertTrue(blocked.json()['password_change_required'])

        # 3. Wrong current password is rejected with 403.
        wrong = self.client.post(
            '/api/auth/change-password/',
            {'current_password': 'nope', 'new_password': 'Str0ng!Passw0rd'},
            format='json',
        )
        self.assertEqual(wrong.status_code, 403)
        self.assertTrue(wrong.data['password_change_required'])

        # 4. Reusing the seeded default value is rejected with 400.
        reuse = self.client.post(
            '/api/auth/change-password/',
            {'current_password': 'admin', 'new_password': 'admin'},
            format='json',
        )
        self.assertEqual(reuse.status_code, 400)

        # 5. A valid change clears the flag and destroys the current session.
        changed = self.client.post(
            '/api/auth/change-password/',
            {'current_password': 'admin', 'new_password': 'Str0ng!Passw0rd'},
            format='json',
        )
        self.assertEqual(changed.status_code, 200)
        self.assertEqual(
            changed.data['detail'], 'Password changed. Please log in again.'
        )
        admin = User.objects.get(username='admin')
        self.assertFalse(admin.must_change_password)
        self.assertTrue(admin.password_changed)
        # The old session is unusable; the client must re-login.
        self.assertIn(self.client.get('/api/auth/me/').status_code, (401, 403))

        # 6. Re-login with the new password issues a fully live session.
        relogin = self._login(password='Str0ng!Passw0rd')
        self.assertEqual(relogin.status_code, 200)
        self.assertFalse(relogin.data['password_change_required'])

        # 7. Protected routes are now accessible.
        ok = self.client.get('/api/auth/business-info/')
        self.assertEqual(ok.status_code, 200)

    def test_normal_users_are_unaffected(self):
        """Users without the flag never enter the forced flow."""
        User.objects.create_user(
            username='normal',
            password='Passw0rd123',
            role='cashier',
            must_change_password=False,
        )

        login = self._login(username='normal', password='Passw0rd123')
        self.assertEqual(login.status_code, 200)
        self.assertFalse(login.data['password_change_required'])

        ok = self.client.get('/api/auth/business-info/')
        self.assertEqual(ok.status_code, 200)

        # A normal user can still change their own password without being
        # redirected, and the change needs the correct current password.
        wrong = self.client.post(
            '/api/auth/change-password/',
            {'current_password': 'wrong', 'new_password': 'Another!Passw0rd'},
            format='json',
        )
        self.assertEqual(wrong.status_code, 403)

    def test_default_credentials_stop_working_after_forced_change(self):
        """After the forced change, the seeded admin/admin login must fail."""
        self._login()
        changed = self.client.post(
            '/api/auth/change-password/',
            {'current_password': 'admin', 'new_password': 'Str0ng!Passw0rd'},
            format='json',
        )
        self.assertEqual(changed.status_code, 200)

        self.client.logout()
        stale = self._login(password='admin')
        self.assertEqual(stale.status_code, 400)

        fresh = self._login(password='Str0ng!Passw0rd')
        self.assertEqual(fresh.status_code, 200)
        self.assertFalse(fresh.data['password_change_required'])
