"""Tests for the hardened Django admin login (S2).

Covers the security follow-up on the ``/admin/login/`` surface that ``0004``
made live for every ``role='admin'`` (now ``is_staff``) user:

* the reserved seeded credential (``password='admin'``) is rejected and surfaces
  the forced-password-change path instead of authenticating;
* failed attempts are throttled via ``accounts.throttling`` (``admin_login``
  namespace) and the view returns HTTP 429 once the budget is exhausted;
* a successful login clears the failure counter;
* a normal strong-password staff login still works;
* ``/admin/`` stays behind the ``must_change_password`` guard after login.

Note: the local-memory cache is not flushed between tests by ``TestCase``, so
``setUp`` clears it — otherwise a throttled identity from a prior test would
leak into the next one.
"""

from django.core.cache import cache
from django.test import TestCase

from . import throttling

#: Matches config/settings.py AUTH_ADMIN_LOGIN_FAIL_LIMIT.
LIMIT = 5


class HardenedAdminLoginTests(TestCase):
    def setUp(self):
        cache.clear()

    # -- reserved credential -------------------------------------------------

    def test_reserved_credential_rejected_at_admin_login(self):
        # A staff account whose password is still the seeded default
        # ('admin') must not be able to sign into /admin/login/.
        held = self._user('reserved_admin', 'admin')
        response = self.client.post(
            '/admin/login/', {'username': 'reserved_admin', 'password': 'admin'}
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'default/reserved credential')
        # Not authenticated: a follow-up to /admin/ still bounces to login.
        follow = self.client.get('/admin/', follow=False)
        self.assertEqual(follow.status_code, 302)
        self.assertIn('/admin/login/', follow['Location'])
        held.delete()

    def test_reserved_rejection_counts_as_failed_attempt(self):
        # A reserved credential is a failure against the budget, not a free pass.
        self._user('reserved_admin', 'admin')
        for _ in range(LIMIT):
            self.client.post(
                '/admin/login/', {'username': 'reserved_admin', 'password': 'admin'}
            )
        blocked = self.client.post(
            '/admin/login/', {'username': 'reserved_admin', 'password': 'admin'}
        )
        self.assertEqual(blocked.status_code, 429)

    # -- throttling ----------------------------------------------------------

    def test_failed_attempts_hit_the_configured_limit(self):
        self._user('throttled_admin', 'Str0ngPass1')
        # LIMIT wrong-password attempts render the form again (200)...
        for _ in range(LIMIT):
            response = self.client.post(
                '/admin/login/', {'username': 'throttled_admin', 'password': 'wrong'}
            )
            self.assertEqual(response.status_code, 200)
        # ...and the next one is locked out with 429.
        locked = self.client.post(
            '/admin/login/', {'username': 'throttled_admin', 'password': 'wrong'}
        )
        self.assertEqual(locked.status_code, 429)

    def test_successful_login_clears_the_counter(self):
        self._user('recover_admin', 'Str0ngPass1')
        # Two failures leave a partial budget.
        for _ in range(2):
            self.client.post(
                '/admin/login/', {'username': 'recover_admin', 'password': 'wrong'}
            )
        identity = 'recover_admin'
        ip = '127.0.0.1'
        self.assertTrue(throttling.is_blocked('admin_login', identity, ip) is False)

        # A correct login succeeds and resets the counter.
        ok = self.client.post(
            '/admin/login/',
            {'username': 'recover_admin', 'password': 'Str0ngPass1'},
        )
        self.assertEqual(ok.status_code, 302)
        self.assertFalse(throttling.is_blocked('admin_login', identity, ip))

        # After reset, a fresh wrong attempt is a normal 200, not a 429.
        again = self.client.post(
            '/admin/login/', {'username': 'recover_admin', 'password': 'wrong'}
        )
        self.assertEqual(again.status_code, 200)

    def test_limit_uses_unique_per_identity_keys(self):
        # A locked-out identity does not lock out a different username.
        self._user('bad_admin', 'Str0ngPass1')
        for _ in range(LIMIT):
            self.client.post(
                '/admin/login/', {'username': 'bad_admin', 'password': 'wrong'}
            )
        self.assertEqual(
            self.client.post(
                '/admin/login/', {'username': 'bad_admin', 'password': 'wrong'}
            ).status_code,
            429,
        )
        # A different username keeps its own untouched budget.
        other = self.client.post(
            '/admin/login/', {'username': 'other_user', 'password': 'wrong'}
        )
        self.assertEqual(other.status_code, 200)

    # -- normal strong-password login ----------------------------------------

    def test_strong_password_staff_admin_login_works(self):
        self._user('good_admin', 'Str0ngPass1')
        response = self.client.post(
            '/admin/login/',
            {'username': 'good_admin', 'password': 'Str0ngPass1'},
        )
        self.assertEqual(response.status_code, 302)
        index = self.client.get('/admin/')
        self.assertEqual(index.status_code, 200)

    # -- forced-change guard stays intact -------------------------------------

    def test_admin_login_does_not_bypass_forced_change_guard(self):
        # A strong-password user whose must_change_password flag is still set
        # can sign in, but /admin/ remains blocked until the password changes.
        pending = self._staff_user(
            'pending_admin', 'Str0ngPass1', must_change_password=True
        )
        response = self.client.post(
            '/admin/login/',
            {'username': 'pending_admin', 'password': 'Str0ngPass1'},
        )
        self.assertEqual(response.status_code, 302)
        blocked = self.client.get('/admin/')
        self.assertEqual(blocked.status_code, 403)
        pending.delete()

    # helpers -----------------------------------------------------------------

    def _user(self, username, password, **kwargs):
        return self._staff_user(username, password, must_change_password=False)

    def _staff_user(self, username, password, must_change_password):
        from django.contrib.auth import get_user_model
        User = get_user_model()
        return User.objects.create_user(
            username=username, password=password, role='admin',
            is_staff=True, must_change_password=must_change_password,
        )
