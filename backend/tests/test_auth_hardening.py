"""Auth-hardening tests (S1–S4 from the security review of t_925ef970).

Covers:
* S1 — brute-force throttling on login and change-password (HTTP 429).
* S2 — structured audit log records (no passwords / session keys).
* S4 — ``post_migrate`` admin seed does not resurrect a deleted admin.

S3 (db.sqlite3 untracked) is a git operation and is verified via
``git ls-files``, not these tests.
"""

from django.core.cache import cache
from django.test import RequestFactory, TestCase, override_settings
from rest_framework.test import APIClient

from django.contrib.auth import get_user_model

from accounts import throttling
from accounts.apps import seed_default_admin

User = get_user_model()


class LoginThrottleTests(TestCase):
    """S1: N+1 rapid failed logins must yield 429; a success under the limit
    still works, and success/expiry both release the budget."""

    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            username='throttled',
            password='Correct-Password-1',
            role='cashier',
            must_change_password=False,
        )
        cache.clear()

    def _try_login(self, password):
        return self.client.post(
            '/api/auth/login/',
            {'username': 'throttled', 'password': password},
            format='json',
        )

    def test_n_plus_one_failed_logins_yields_429(self):
        limit = 5
        # The first `limit` failures return 400 (credentials rejected)...
        for _ in range(limit):
            response = self._try_login('wrong')
            self.assertEqual(response.status_code, 400)
        # ...and the (limit+1)th attempt is throttled with 429.
        response = self._try_login('wrong')
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.data['detail'],
                         'Too many failed login attempts. Try again later.')

    def test_success_still_works_under_the_limit(self):
        # A couple of failures, then a successful login is not blocked.
        self._try_login('wrong')
        self._try_login('wrong2')
        response = self._try_login('Correct-Password-1')
        self.assertEqual(response.status_code, 200)

    def test_success_clears_failure_budget(self):
        # Fail twice, succeed (clears the counter), then fail again: the
        # failure budget restarts, so a second batch of failures is allowed.
        self._try_login('wrong')
        self._try_login('wrong2')
        self.assertEqual(self._try_login('Correct-Password-1').status_code, 200)
        for _ in range(5):
            self.assertEqual(self._try_login('wrong').status_code, 400)
        self.assertEqual(self._try_login('wrong').status_code, 429)

    def test_throttle_is_per_username_and_ip(self):
        # A different username hitting the same IP is not blocked by the
        # throttled account's budget.
        other = User.objects.create_user(
            username='otheruser',
            password='Other-Password-1',
            role='cashier',
            must_change_password=False,
        )
        cache.clear()
        self._try_login('wrong')
        self._try_login('wrong2')
        response = self.client.post(
            '/api/auth/login/',
            {'username': 'otheruser', 'password': 'Other-Password-1'},
            format='json',
        )
        self.assertEqual(response.status_code, 200)


class ChangePasswordThrottleTests(TestCase):
    """S1: wrong-current-password attempts are throttled per user+IP."""

    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            username='changer',
            password='Current-Password-1',
            role='cashier',
            must_change_password=False,
        )
        self.client.force_login(self.user)
        cache.clear()

    def test_n_plus_one_wrong_current_password_yields_429(self):
        limit = 5
        for _ in range(limit):
            response = self.client.post(
                '/api/auth/change-password/',
                {'current_password': 'wrong', 'new_password': 'Something-1'},
                format='json',
            )
            self.assertEqual(response.status_code, 403)
        response = self.client.post(
            '/api/auth/change-password/',
            {'current_password': 'wrong', 'new_password': 'Something-1'},
            format='json',
        )
        self.assertEqual(response.status_code, 429)

    def test_policy_violations_do_not_exhaust_budget(self):
        # A weak new password is a 400 but must NOT count toward the budget,
        # since it is not a credential-guessing signal.
        for _ in range(6):
            response = self.client.post(
                '/api/auth/change-password/',
                {'current_password': 'Current-Password-1', 'new_password': 'short'},
                format='json',
            )
            self.assertEqual(response.status_code, 400)
        # The budget is untouched, so a legitimate change still works.
        response = self.client.post(
            '/api/auth/change-password/',
            {'current_password': 'Current-Password-1', 'new_password': 'New-Pass-9x'},
            format='json',
        )
        self.assertEqual(response.status_code, 200)


class AuthAuditLogTests(TestCase):
    """S2: structured log records for login/password-change/guard events."""

    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            username='audited',
            password='Correct-Password-1',
            role='cashier',
            must_change_password=False,
        )
        cache.clear()

    def test_login_success_is_logged(self):
        with self.assertLogs('auth.audit', level='INFO') as captured:
            response = self.client.post(
                '/api/auth/login/',
                {'username': 'audited', 'password': 'Correct-Password-1'},
                format='json',
            )
            self.assertEqual(response.status_code, 200)
        joined = ' '.join(captured.output)
        self.assertIn('login_success', joined)
        self.assertIn('username=audited', joined)
        self.assertIn('outcome=success', joined)

    def test_login_failure_is_logged(self):
        with self.assertLogs('auth.audit', level='INFO') as captured:
            response = self.client.post(
                '/api/auth/login/',
                {'username': 'audited', 'password': 'wrong'},
                format='json',
            )
            self.assertEqual(response.status_code, 400)
        joined = ' '.join(captured.output)
        self.assertIn('login_failure', joined)
        self.assertIn('outcome=failure', joined)

    def test_forced_change_and_guard_are_logged(self):
        flagged = User.objects.create_user(
            username='pending',
            password='Pending-Password-1',
            role='cashier',
            must_change_password=True,
        )
        with self.assertLogs('auth.audit', level='INFO') as captured:
            response = self.client.post(
                '/api/auth/login/',
                {'username': 'pending', 'password': 'Pending-Password-1'},
                format='json',
            )
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.data['password_change_required'])
            blocked = self.client.get('/api/auth/business-info/')
            self.assertEqual(blocked.status_code, 403)
        joined = ' '.join(captured.output)
        self.assertIn('forced_change_required', joined)
        self.assertIn('guard_blocked', joined)
        self.assertIn('username=pending', joined)

    def test_password_change_success_and_failure_are_logged(self):
        self.client.force_login(self.user)
        with self.assertLogs('auth.audit', level='INFO') as captured:
            # Wrong current: failure reason class is recorded.
            failed = self.client.post(
                '/api/auth/change-password/',
                {'current_password': 'wrong', 'new_password': 'New-Pass-9x'},
                format='json',
            )
            self.assertEqual(failed.status_code, 403)
            # Correct change: success recorded.
            good = self.client.post(
                '/api/auth/change-password/',
                {'current_password': 'Correct-Password-1',
                 'new_password': 'New-Pass-9x'},
                format='json',
            )
            self.assertEqual(good.status_code, 200)
        joined = ' '.join(captured.output)
        self.assertIn('password_change_success', joined)
        self.assertIn('password_change_failure', joined)
        self.assertIn('reason_class=incorrect_current_password', joined)

    def test_logs_never_contain_password_values(self):
        self.client.force_login(self.user)
        with self.assertLogs('auth.audit', level='INFO') as captured:
            self.client.post(
                '/api/auth/change-password/',
                {'current_password': 'wrong', 'new_password': 'SuperSecret123'},
                format='json',
            )
            self.client.post(
                '/api/auth/login/',
                {'username': 'audited', 'password': 'SuperSecret123'},
                format='json',
            )
        joined = ' '.join(captured.output)
        self.assertNotIn('SuperSecret123', joined)
        self.assertNotIn('secret', joined.lower())


class AdminSeedGateTests(TestCase):
    """S4: the post_migrate admin seed must not resurrect a deleted admin."""

    def setUp(self):
        self.client = APIClient()

    @override_settings(DEBUG=True)
    def test_seed_does_not_resurrect_deleted_admin_when_other_users_exist(self):
        # Fresh test DB: post_migrate seeded 'admin'. Replace with a real user
        # and delete the admin, simulating an existing deployment.
        User.objects.all().delete()
        User.objects.create_user(
            username='realmanager',
            password='Real-Pass-1',
            role='manager',
            must_change_password=False,
        )
        seed_default_admin(sender=None)
        # No admin may be fabricated while a real user exists.
        self.assertFalse(User.objects.filter(username='admin').exists())

    @override_settings(DEBUG=False, SEED_ADMIN_ENV_FLAG=False)
    def test_seed_disabled_in_production_on_empty_db(self):
        # Production (DEBUG off, no env flag): even an empty DB must not be
        # seeded silently.
        User.objects.all().delete()
        seed_default_admin(sender=None)
        self.assertFalse(User.objects.filter(username='admin').exists())

    @override_settings(DEBUG=True)
    def test_seed_runs_on_genuinely_fresh_install(self):
        # Simulate a brand-new database with zero users under DEBUG: the seed
        # creates the initial admin with must_change_password=True.
        User.objects.all().delete()
        seed_default_admin(sender=None)
        admin = User.objects.filter(username='admin').first()
        self.assertIsNotNone(admin)
        self.assertTrue(admin.must_change_password)
        self.assertEqual(admin.role, 'admin')


class TrustedProxyClientIPTests(TestCase):
    """t_1dc4c113: ``X-Forwarded-For`` is honoured only from a trusted proxy.

    ``accounts.throttling.client_ip`` (and the audit IP derived from it) must
    ignore a client-supplied header from an untrusted peer, so a client cannot
    rotate the ``ip`` component of its throttle key or forge its audit IP.
    """

    def setUp(self):
        cache.clear()

    def _request(self, remote, xff=None):
        meta = {'REMOTE_ADDR': remote}
        if xff:
            meta['HTTP_X_FORWARDED_FOR'] = xff
        return RequestFactory().post('/api/auth/login/', data={}, **meta)

    def test_forwarded_for_ignored_when_no_trusted_proxy_configured(self):
        # Default config has no trusted proxies: the header is ignored and the
        # immediate peer's address is used.
        r = self._request('203.0.113.50', xff='10.9.9.9')
        self.assertEqual(throttling.client_ip(r), '203.0.113.50')

    def test_audit_ip_ignores_forwarded_for_from_untrusted_peer(self):
        from accounts import audit
        r = self._request('203.0.113.50', xff='10.9.9.9')
        self.assertEqual(audit._client_ip(r), '203.0.113.50')

    @override_settings(AUTH_TRUSTED_PROXY_IPS={'10.0.0.5'})
    def test_forwarded_for_honoured_when_peer_is_a_trusted_proxy(self):
        r = self._request('10.0.0.5', xff='203.0.113.9, 10.0.0.1')
        self.assertEqual(throttling.client_ip(r), '203.0.113.9')

    @override_settings(AUTH_TRUSTED_PROXY_IPS={'10.0.0.5'})
    def test_forwarded_for_from_untrusted_peer_is_not_honoured_even_with_proxies(self):
        # A trusted proxy list exists, but this request's peer is not on it:
        # its spoofed header is still ignored.
        r = self._request('198.51.100.7', xff='10.9.9.9')
        self.assertEqual(throttling.client_ip(r), '198.51.100.7')


class SpoofedXFFThrottleTests(TestCase):
    """t_1dc4c113: a spoofed ``X-Forwarded-For`` must not reset the budget.

    From an untrusted peer the header is ignored, so a fresh ``X-Forwarded-For``
    per attempt cannot open a new throttle key to dodge the 429.
    """

    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            username='xfftarget',
            password='Correct-Password-1',
            role='cashier',
            must_change_password=False,
        )
        cache.clear()

    def _try_login(self, password, **extra):
        return self.client.post(
            '/api/auth/login/',
            {'username': 'xfftarget', 'password': password},
            format='json',
            **extra,
        )

    def test_spoofed_header_cannot_escape_the_limit_with_no_trusted_proxy(self):
        # Exhaust the budget for the untrusted REMOTE_ADDR.
        for _ in range(5):
            self.assertEqual(self._try_login('wrong').status_code, 400)
        # Spoofing a fresh XFF from the same peer must still hit the same key.
        spoofed = self._try_login('wrong', HTTP_X_FORWARDED_FOR='10.9.9.9')
        self.assertEqual(spoofed.status_code, 429)

    @override_settings(AUTH_TRUSTED_PROXY_IPS={'10.0.0.5'})
    def test_spoofed_header_cannot_escape_with_unrelated_trusted_proxy(self):
        # Even when some *other* address is a trusted proxy, this peer's spoof
        # is ignored, so the budget is still reached.
        for _ in range(5):
            self.assertEqual(self._try_login('wrong').status_code, 400)
        spoofed = self._try_login('wrong', HTTP_X_FORWARDED_FOR='10.9.9.9')
        self.assertEqual(spoofed.status_code, 429)


class AuditFieldQuotingTests(TestCase):
    """t_1dc4c113: hostile field values must not forge audit lines.

    An unknown username is never model-validated, so a value with newlines or
    spaces must be quoted/escaped to keep the record on one logical line.
    """

    def setUp(self):
        self.client = APIClient()
        cache.clear()

    def test_hostile_username_with_newline_and_space_stays_single_line(self):
        hostile = 'evil\nforge me'
        with self.assertLogs('auth.audit', level='INFO') as captured:
            self.client.post(
                '/api/auth/login/',
                {'username': hostile, 'password': 'wrong'},
                format='json',
            )
        # Exactly one log record, with no embedded newline that would forge an
        # extra line, and the hostile value repr-quoted as a single token.
        self.assertEqual(len(captured.output), 1)
        line = captured.output[0]
        self.assertNotIn('\n', line)
        self.assertIn('username=%r' % hostile, line)

    def test_plain_values_keep_unquoted_key_value_format(self):
        # Unaffected values keep the existing greppable key=value shape.
        with self.assertLogs('auth.audit', level='INFO') as captured:
            self.client.post(
                '/api/auth/login/',
                {'username': 'plainname', 'password': 'wrong'},
                format='json',
            )
        joined = ' '.join(captured.output)
        self.assertIn('username=plainname', joined)
        self.assertNotIn("username='plainname'", joined)
