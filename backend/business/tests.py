from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from .models import BusinessInfo

User = get_user_model()


class BusinessInfoModelTests(TestCase):
    def test_get_returns_a_singleton(self):
        first = BusinessInfo.get()
        second = BusinessInfo.get()

        self.assertEqual(first.pk, second.pk)
        self.assertEqual(BusinessInfo.objects.count(), 1)

    def test_is_complete_requires_name_phone_and_email(self):
        business = BusinessInfo.get()
        self.assertFalse(business.is_complete())

        business.company_name = 'Corner Shop'
        business.phone = '555-0100'
        self.assertFalse(business.is_complete())

        business.email = 'shop@example.com'
        self.assertTrue(business.is_complete())


class BusinessApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='owner', password='pw', role='admin')
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_get_returns_business_info(self):
        business = BusinessInfo.get()
        business.company_name = 'Corner Shop'
        business.save()

        response = self.client.get('/api/business/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['company_name'], 'Corner Shop')

    def test_patch_updates_only_provided_fields(self):
        business = BusinessInfo.get()
        business.company_name = 'Corner Shop'
        business.phone = '555-0100'
        business.save()

        response = self.client.patch(
            '/api/business/',
            {'email': 'shop@example.com'},
            format='json',
        )

        self.assertEqual(response.status_code, 200)
        business.refresh_from_db()
        self.assertEqual(business.company_name, 'Corner Shop')
        self.assertEqual(business.phone, '555-0100')
        self.assertEqual(business.email, 'shop@example.com')

    def test_business_api_requires_authentication(self):
        anonymous = APIClient()

        response = anonymous.get('/api/business/')

        self.assertIn(response.status_code, (401, 403))


class BusinessApiAuthorizationTests(TestCase):
    def setUp(self):
        self.cashier = User.objects.create_user(username='cashier', password='pw', role='cashier')
        self.client = APIClient()
        self.client.force_authenticate(self.cashier)

    def test_cashier_can_get_business_info(self):
        response = self.client.get('/api/business/')

        self.assertEqual(response.status_code, 200)

    def test_cashier_cannot_patch_business_info(self):
        response = self.client.patch(
            '/api/business/',
            {'company_name': 'Hacked'},
            format='json',
        )

        self.assertEqual(response.status_code, 403)

    def test_cashier_cannot_post_business_info(self):
        response = self.client.post(
            '/api/business/',
            {'company_name': 'Hacked'},
            format='json',
        )

        self.assertEqual(response.status_code, 403)

    def test_cashier_cannot_change_backup_path(self):
        response = self.client.patch(
            '/api/business/',
            {'backup_path': '/tmp/evil'},
            format='json',
        )

        self.assertEqual(response.status_code, 403)
