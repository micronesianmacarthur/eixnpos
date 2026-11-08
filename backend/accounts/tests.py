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
        admin = User.objects.get(username='admin')
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
        self.assertTrue(response.data['must_change_password'])
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

    def test_change_password_updates_flags_and_keeps_user_logged_in(self):
        self.client.post(
            '/api/auth/login/',
            {'username': 'cashier', 'password': 'correct-password'},
            format='json',
        )

        response = self.client.post(
            '/api/auth/change-password/',
            {'new_password': 'new-password'},
            format='json',
        )

        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.password_changed)
        self.assertFalse(self.user.must_change_password)
        self.assertIsNotNone(authenticate(username='cashier', password='new-password'))
        self.assertEqual(self.client.get('/api/auth/me/').status_code, 200)

    def test_change_password_requires_new_password(self):
        self.client.force_authenticate(self.user)

        response = self.client.post('/api/auth/change-password/', {}, format='json')

        self.assertEqual(response.status_code, 400)

    def test_change_password_requires_authentication(self):
        response = self.client.post(
            '/api/auth/change-password/',
            {'new_password': 'new-password'},
            format='json',
        )

        self.assertIn(response.status_code, (401, 403))

    def test_csrf_endpoint_is_public(self):
        response = self.client.get('/api/auth/csrf/')

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data['csrfToken'])
