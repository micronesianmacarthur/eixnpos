from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.models import AnonymousUser
from django.test import TestCase
from rest_framework.test import APIClient

from business.models import BusinessInfo

from .utils import is_manager

User = get_user_model()


class IsManagerTests(TestCase):
    def test_anonymous_user_is_not_manager(self):
        self.assertFalse(is_manager(AnonymousUser()))

    def test_none_is_not_manager(self):
        self.assertFalse(is_manager(None))

    def test_cashier_is_not_manager(self):
        cashier = User.objects.create_user(username='cashier', password='pw', role='cashier')
        self.assertFalse(is_manager(cashier))

    def test_manager_role_is_manager(self):
        manager = User.objects.create_user(username='manager', password='pw', role='manager')
        self.assertTrue(is_manager(manager))

    def test_admin_role_is_manager(self):
        admin = User.objects.get_or_create(
            username='admin', defaults={'role': 'admin'})[0]
        self.assertTrue(is_manager(admin))

    def test_staff_is_manager(self):
        staff = User.objects.create_user(username='staff', password='pw', role='cashier', is_staff=True)
        self.assertTrue(is_manager(staff))

    def test_superuser_is_manager(self):
        superuser = User.objects.create_user(username='root', password='pw', role='cashier', is_superuser=True)
        self.assertTrue(is_manager(superuser))


class AuthApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            username='cashier',
            password='correct-password',
            role='cashier',
            must_change_password=True,
        )

    def test_login_returns_user_and_password_change_flag(self):
        response = self.client.post(
            '/api/auth/login/',
            {'username': 'cashier', 'password': 'correct-password'},
            format='json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['user']['username'], 'cashier')
        self.assertTrue(response.data['password_change_required'])
        self.assertFalse(response.data['business_info_complete'])

    def test_login_reports_complete_business_info(self):
        business = BusinessInfo.get()
        business.company_name = 'Corner Shop'
        business.phone = '555-0100'
        business.email = 'shop@example.com'
        business.save()

        response = self.client.post(
            '/api/auth/login/',
            {'username': 'cashier', 'password': 'correct-password'},
            format='json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data['business_info_complete'])

    def test_login_rejects_invalid_credentials(self):
        response = self.client.post(
            '/api/auth/login/',
            {'username': 'cashier', 'password': 'wrong'},
            format='json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data['detail'], 'Invalid credentials')

    def test_me_requires_authentication(self):
        response = self.client.get('/api/auth/me/')

        self.assertIn(response.status_code, (401, 403))

    def test_me_returns_logged_in_user(self):
        self.client.post(
            '/api/auth/login/',
            {'username': 'cashier', 'password': 'correct-password'},
            format='json',
        )

        response = self.client.get('/api/auth/me/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['user']['id'], self.user.id)

    def test_logout_ends_session(self):
        self.client.post(
            '/api/auth/login/',
            {'username': 'cashier', 'password': 'correct-password'},
            format='json',
        )

        logout_response = self.client.post('/api/auth/logout/', format='json')
        me_response = self.client.get('/api/auth/me/')

        self.assertEqual(logout_response.status_code, 200)
        self.assertIn(me_response.status_code, (401, 403))

    def test_change_password_success_clears_flags_and_ends_session(self):
        self.client.post(
            '/api/auth/login/',
            {'username': 'cashier', 'password': 'correct-password'},
            format='json',
        )

        response = self.client.post(
            '/api/auth/change-password/',
            {
                'current_password': 'correct-password',
                'new_password': 'Str0ng!Passw0rd',
            },
            format='json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.data['detail'], 'Password changed. Please log in again.'
        )
        self.user.refresh_from_db()
        self.assertTrue(self.user.password_changed)
        self.assertFalse(self.user.must_change_password)
        # New password is stored and usable.
        self.assertIsNotNone(
            authenticate(username='cashier', password='Str0ng!Passw0rd')
        )
        # Old session is destroyed: the client must re-login.
        me_response = self.client.get('/api/auth/me/')
        self.assertIn(me_response.status_code, (401, 403))

    def test_change_password_requires_current_and_new_password(self):
        self.client.force_authenticate(self.user)

        missing = self.client.post('/api/auth/change-password/', {}, format='json')
        no_current = self.client.post(
            '/api/auth/change-password/',
            {'new_password': 'Str0ng!Passw0rd'},
            format='json',
        )

        self.assertEqual(missing.status_code, 400)
        self.assertEqual(no_current.status_code, 400)

    def test_change_password_wrong_current_password_returns_403(self):
        self.client.force_authenticate(self.user)

        response = self.client.post(
            '/api/auth/change-password/',
            {
                'current_password': 'wrong',
                'new_password': 'Str0ng!Passw0rd',
            },
            format='json',
        )

        self.assertEqual(response.status_code, 403)
        self.assertTrue(response.data['password_change_required'])
        self.assertEqual(response.data['detail'], 'Current password is incorrect.')

    def test_change_password_rejects_reserved_default_value(self):
        self.client.force_authenticate(self.user)

        response = self.client.post(
            '/api/auth/change-password/',
            {
                'current_password': 'correct-password',
                'new_password': 'admin',
            },
            format='json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.data['detail'],
            "New password must not be the default or reserved value 'admin'.",
        )

    def test_change_password_rejects_policy_violations(self):
        self.client.force_authenticate(self.user)

        # Too short / no digit.
        response = self.client.post(
            '/api/auth/change-password/',
            {'current_password': 'correct-password', 'new_password': 'password'},
            format='json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.data['detail'],
            'New password must be at least 8 characters long and contain both '
            'letters and digits.',
        )

        # Digits only.
        digits = self.client.post(
            '/api/auth/change-password/',
            {'current_password': 'correct-password', 'new_password': '123456789'},
            format='json',
        )
        self.assertEqual(digits.status_code, 400)

        # Whitespace-only is rejected by the length rule.
        blank = self.client.post(
            '/api/auth/change-password/',
            {'current_password': 'correct-password', 'new_password': '   '},
            format='json',
        )
        self.assertEqual(blank.status_code, 400)

    def test_change_password_rejects_unchanged_password(self):
        user = User.objects.create_user(
            username='diff',
            password='Passw0rd123',
            role='cashier',
        )
        self.client.force_authenticate(user)

        response = self.client.post(
            '/api/auth/change-password/',
            {'current_password': 'Passw0rd123', 'new_password': 'Passw0rd123'},
            format='json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.data['detail'],
            'New password must be different from the current password.',
        )

    def test_change_password_requires_authentication(self):
        response = self.client.post(
            '/api/auth/change-password/',
            {
                'current_password': 'correct-password',
                'new_password': 'Str0ng!Passw0rd',
            },
            format='json',
        )

        self.assertIn(response.status_code, (401, 403))

    def test_protected_route_blocked_while_flag_set(self):
        # Real session login sets the flag user in the session, so the guard
        # middleware sees them and blocks a protected route.
        self.client.post(
            '/api/auth/login/',
            {'username': 'cashier', 'password': 'correct-password'},
            format='json',
        )

        response = self.client.get('/api/auth/business-info/')

        self.assertEqual(response.status_code, 403)
        data = response.json()
        self.assertTrue(data['password_change_required'])
        self.assertEqual(
            data['detail'],
            'Password change required before accessing this resource.',
        )

    def test_auth_endpoints_available_while_flag_set(self):
        # The flow's own endpoints (me/change-password) stay reachable while
        # the flag is set so the pending state can be surfaced and cleared.
        self.client.post(
            '/api/auth/login/',
            {'username': 'cashier', 'password': 'correct-password'},
            format='json',
        )

        me_response = self.client.get('/api/auth/me/')
        self.assertEqual(me_response.status_code, 200)
        self.assertTrue(me_response.data['user']['must_change_password'])

        change_response = self.client.post(
            '/api/auth/change-password/',
            {'current_password': 'wrong', 'new_password': 'Str0ng!Passw0rd'},
            format='json',
        )
        self.assertEqual(change_response.status_code, 403)

    def test_normal_users_are_unaffected(self):
        user = User.objects.create_user(
            username='normal',
            password='Passw0rd123',
            role='cashier',
            must_change_password=False,
        )
        self.client.post(
            '/api/auth/login/',
            {'username': 'normal', 'password': 'Passw0rd123'},
            format='json',
        )

        response = self.client.get('/api/auth/business-info/')

        self.assertEqual(response.status_code, 200)
        self.assertIn('company_name', response.data)
        self.user.refresh_from_db()
        # The concurrent flag-bearing user added nothing unexpected.
        self.assertTrue(self.user.must_change_password)

    def test_csrf_endpoint_is_public(self):
        response = self.client.get('/api/auth/csrf/')

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data['csrfToken'])
