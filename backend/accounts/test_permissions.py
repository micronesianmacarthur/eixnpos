from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

User = get_user_model()


class UserViewSetPermissionTests(TestCase):
    """Test that UserViewSet permissions correctly restrict access to manager/admin roles."""

    def setUp(self):
        self.client = APIClient()
        self.cashier = User.objects.create_user(username='test_cashier', password='pw', role='cashier')
        self.manager = User.objects.create_user(username='test_manager', password='pw', role='manager')
        self.admin = User.objects.create_user(username='test_admin', password='pw', role='admin')

    def tearDown(self):
        self.cashier.delete()
        self.manager.delete()
        self.admin.delete()

    def test_cashier_cannot_list_users(self):
        """Cashier users should receive 403 Forbidden when accessing users list."""
        self.client.force_authenticate(self.cashier)
        response = self.client.get('/api/auth/users/')
        self.assertEqual(response.status_code, 403)

    def test_cashier_cannot_retrieve_user(self):
        """Cashier users should receive 403 Forbidden when retrieving a user."""
        self.client.force_authenticate(self.cashier)
        response = self.client.get(f'/api/auth/users/{self.cashier.id}/')
        self.assertEqual(response.status_code, 403)

    def test_cashier_cannot_create_user(self):
        """Cashier users should receive 403 Forbidden when creating a user."""
        self.client.force_authenticate(self.cashier)
        response = self.client.post('/api/auth/users/', {'username': 'newuser', 'role': 'cashier'}, format='json')
        self.assertEqual(response.status_code, 403)

    def test_cashier_cannot_update_user(self):
        """Cashier users should receive 403 Forbidden when updating a user."""
        self.client.force_authenticate(self.cashier)
        response = self.client.put(f'/api/auth/users/{self.cashier.id}/', {'username': 'updated', 'role': 'cashier'}, format='json')
        self.assertEqual(response.status_code, 403)

    def test_cashier_cannot_partial_update_user(self):
        """Cashier users should receive 403 Forbidden when partially updating a user."""
        self.client.force_authenticate(self.cashier)
        response = self.client.patch(f'/api/auth/users/{self.cashier.id}/', {'username': 'patched'}, format='json')
        self.assertEqual(response.status_code, 403)

    def test_cashier_cannot_delete_user(self):
        """Cashier users should receive 403 Forbidden when deleting a user."""
        self.client.force_authenticate(self.cashier)
        response = self.client.delete(f'/api/auth/users/{self.cashier.id}/')
        self.assertEqual(response.status_code, 403)

    def test_manager_can_list_users(self):
        """Manager users should receive 200 OK when accessing users list."""
        self.client.force_authenticate(self.manager)
        response = self.client.get('/api/auth/users/')
        self.assertEqual(response.status_code, 200)

    def test_manager_can_retrieve_user(self):
        """Manager users should receive 200 OK when retrieving a user."""
        self.client.force_authenticate(self.manager)
        response = self.client.get(f'/api/auth/users/{self.cashier.id}/')
        self.assertEqual(response.status_code, 200)

    def test_manager_can_create_user(self):
        """Manager users should receive 201 Created when creating a user."""
        self.client.force_authenticate(self.manager)
        response = self.client.post('/api/auth/users/', {'username': 'newuser', 'role': 'cashier'}, format='json')
        self.assertEqual(response.status_code, 201)
        # Cleanup
        User.objects.filter(username='newuser').delete()

    def test_admin_can_list_users(self):
        """Admin users should receive 200 OK when accessing users list."""
        self.client.force_authenticate(self.admin)
        response = self.client.get('/api/auth/users/')
        self.assertEqual(response.status_code, 200)

    def test_admin_can_retrieve_user(self):
        """Admin users should receive 200 OK when retrieving a user."""
        self.client.force_authenticate(self.admin)
        response = self.client.get(f'/api/auth/users/{self.cashier.id}/')
        self.assertEqual(response.status_code, 200)

    def test_unauthenticated_user_gets_401_or_403(self):
        """Unauthenticated users should receive 401 or 403 when accessing users endpoints."""
        response = self.client.get('/api/auth/users/')
        self.assertIn(response.status_code, (401, 403))

    def test_staff_user_can_access(self):
        """Django staff users should have access regardless of role."""
        staff = User.objects.create_user(username='staff', password='pw', role='cashier', is_staff=True)
        try:
            self.client.force_authenticate(staff)
            response = self.client.get('/api/auth/users/')
            self.assertEqual(response.status_code, 200)
        finally:
            staff.delete()

    def test_superuser_can_access(self):
        """Django superusers should have access regardless of role."""
        superuser = User.objects.create_user(username='super', password='pw', role='cashier', is_superuser=True)
        try:
            self.client.force_authenticate(superuser)
            response = self.client.get('/api/auth/users/')
            self.assertEqual(response.status_code, 200)
        finally:
            superuser.delete()