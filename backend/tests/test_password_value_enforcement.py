"""Tests for blocker B1: forced change enforced on the password value.

Security review (t_925ef970, comment 51) found that the forced-password-change
flow keyed only on ``User.must_change_password``. An account created by a path
that leaves the flag ``False`` but still holds a reserved/default credential
(e.g. ``PATCH /api/users/<id>/ {"password": "admin"}``, or a legacy/upgraded
database) logged in with ``password_change_required: false`` and full API
access. These tests pin the fix:

a. a non-flagged user whose password is the reserved value is forced at login
   and blocked from protected routes;
b. ``PATCH /api/users/<id>/`` rejects a weak or reserved password with 400;
c. a session flagged *after* login cannot reach protected endpoints until the
   password is changed.
"""

from django.contrib.auth import get_user_model

from rest_framework.test import APIClient, APITestCase

User = get_user_model()

#: A valid password that passes the project policy (>=8 chars, letters+digits).
STRONG_PASSWORD = 'Ch4ng3d!Pass'


class PasswordValueEnforcementTests(APITestCase):
    def setUp(self):
        self.client = APIClient()

    def _login(self, username, password):
        return self.client.post(
            '/api/auth/login/',
            {'username': username, 'password': password},
            format='json',
        )

    def test_reserved_password_forces_change_even_without_flag(self):
        """a) A user whose password is 'admin' but flag is False is forced at login."""
        user = User.objects.create_user(
            username='vulnuser',
            password='admin',
            role='admin',
            must_change_password=False,
        )
        self.assertFalse(user.must_change_password)

        login = self._login('vulnuser', 'admin')
        self.assertEqual(login.status_code, 200)
        self.assertTrue(login.data['password_change_required'])

        # The flag is persisted so the middleware guard engages immediately.
        user.refresh_from_db()
        self.assertTrue(user.must_change_password)

        # Protected routes are now blocked while the flag is set.
        blocked = self.client.get('/api/products/')
        self.assertEqual(blocked.status_code, 403)
        self.assertTrue(blocked.json()['password_change_required'])

    def test_patch_user_rejects_reserved_password(self):
        """b) PATCH /api/users/<id>/ with the reserved value is a 400."""
        admin = User.objects.create_user(
            username='opadmin',
            password=STRONG_PASSWORD,
            role='admin',
            must_change_password=False,
        )
        victim = User.objects.create_user(
            username='victim',
            password=STRONG_PASSWORD,
            role='cashier',
            must_change_password=False,
        )
        self.client.force_authenticate(admin)

        resp = self.client.patch(
            f'/api/users/{victim.id}/',
            {'password': 'admin'},
            format='json',
        )
        self.assertEqual(resp.status_code, 400)
        # The stored credential must be untouched.
        victim.refresh_from_db()
        self.assertFalse(victim.check_password('admin'))
        self.assertTrue(victim.check_password(STRONG_PASSWORD))
        self.assertFalse(victim.must_change_password)

    def test_patch_user_rejects_weak_password(self):
        """b) PATCH with a weak password (too short, no mix) is a 400."""
        admin = User.objects.create_user(
            username='opadmin',
            password=STRONG_PASSWORD,
            role='admin',
            must_change_password=False,
        )
        victim = User.objects.create_user(
            username='victim2',
            password=STRONG_PASSWORD,
            role='cashier',
            must_change_password=False,
        )
        self.client.force_authenticate(admin)

        for weak in ('ab', 'abcdefgh', '12345678'):
            resp = self.client.patch(
                f'/api/users/{victim.id}/',
                {'password': weak},
                format='json',
            )
            self.assertEqual(resp.status_code, 400)

    def test_create_user_rejects_reserved_password(self):
        """Creating a user with the reserved password is rejected."""
        admin = User.objects.create_user(
            username='opadmin',
            password=STRONG_PASSWORD,
            role='admin',
            must_change_password=False,
        )
        self.client.force_authenticate(admin)

        resp = self.client.post(
            '/api/users/',
            {
                'username': 'newbie',
                'email': 'newbie@example.com',
                'role': 'cashier',
                'password': 'admin',
            },
            format='json',
        )
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(User.objects.filter(username='newbie').exists())

    def test_flagged_after_login_session_blocked_until_change(self):
        """c) A session flagged after login is blocked until the password changes."""
        user = User.objects.create_user(
            username='flagger',
            password='admin',
            role='manager',
            must_change_password=False,
        )
        # Login with the reserved credential flags the account in-place.
        self._login('flagger', 'admin')
        user.refresh_from_db()
        self.assertTrue(user.must_change_password)

        # The very same (now-flagged) session must be blocked from protected routes.
        blocked = self.client.get('/api/products/')
        self.assertEqual(blocked.status_code, 403)
        self.assertTrue(blocked.json()['password_change_required'])

        # Only the change/me/login endpoints remain reachable.
        me = self.client.get('/api/auth/me/')
        self.assertEqual(me.status_code, 200)

        # Provide a wrong current password: still blocked, still forced.
        wrong = self.client.post(
            '/api/auth/change-password/',
            {'current_password': 'nope', 'new_password': STRONG_PASSWORD},
            format='json',
        )
        self.assertEqual(wrong.status_code, 403)
        self.assertTrue(wrong.data['password_change_required'])

        # Change the password: flag clears, old session is logged out.
        changed = self.client.post(
            '/api/auth/change-password/',
            {'current_password': 'admin', 'new_password': STRONG_PASSWORD},
            format='json',
        )
        self.assertEqual(changed.status_code, 200)
        user.refresh_from_db()
        self.assertFalse(user.must_change_password)
        self.assertTrue(user.check_password(STRONG_PASSWORD))

        # Re-login with the new password restores access to protected routes.
        relogin = self._login('flagger', STRONG_PASSWORD)
        self.assertEqual(relogin.status_code, 200)
        self.assertFalse(relogin.data['password_change_required'])
        ok = self.client.get('/api/products/')
        self.assertEqual(ok.status_code, 200)


class BackfillMigrationTests(APITestCase):
    """Exercise the backfill migration's function against live rows."""

    def _run_migration(self):
        from importlib import import_module

        from django.apps import apps as django_apps

        backfill = import_module(
            'accounts.migrations.0003_backfill_must_change_password'
        )
        # No schema_editor operations are used; pass a stub.
        backfill.backfill_must_change_password(django_apps, None)

    def test_backfill_flags_existing_default_credential(self):
        """A pre-existing user whose password is the default gets flagged."""
        # A normal user is not touched.
        okuser = User.objects.create_user(
            username='ok',
            password=STRONG_PASSWORD,
            role='cashier',
        )
        # A legacy account holding the default credential with flag False.
        legacy = User.objects.create_user(
            username='legacy',
            password='admin',
            role='admin',
            must_change_password=False,
        )
        self.assertFalse(legacy.must_change_password)

        self._run_migration()

        legacy.refresh_from_db()
        okuser.refresh_from_db()
        self.assertTrue(legacy.must_change_password)
        self.assertFalse(okuser.must_change_password)

    def test_backfill_is_idempotent(self):
        """Re-running (e.g. on a repaired DB) is safe for already-flagged users."""
        legacy = User.objects.create_user(
            username='legacy2',
            password='admin',
            role='admin',
            must_change_password=False,
        )
        self._run_migration()
        legacy.refresh_from_db()
        self.assertTrue(legacy.must_change_password)
        legacy.password_changed = True  # simulate the user having changed later
        legacy.save()
        # Re-running per-row check still agrees (flag already True, no error).
        self._run_migration()
        legacy.refresh_from_db()
        self.assertTrue(legacy.must_change_password)


class BackfillMigrationHook(APITestCase):
    """Sanity check that the reserved-password check used by the migration lives in validators."""

    def test_reserved_helper_matches_validator(self):
        from accounts.validators import is_reserved_password

        self.assertTrue(is_reserved_password('admin'))
        self.assertTrue(is_reserved_password('ADMIN'))
        self.assertTrue(is_reserved_password('  admin  '))
        self.assertFalse(is_reserved_password('NotTheDefault'))
        self.assertFalse(is_reserved_password(''))
        self.assertFalse(is_reserved_password(None))
