from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

User = get_user_model()


class UserApiTests(TestCase):
    def setUp(self):
        self.admin_user = User.objects.create_user(
            username='admin_test',
            password='adminpass',
            email='admin@example.com',
            first_name='Admin',
            last_name='User',
            role='admin'
        )
        self.manager_user = User.objects.create_user(
            username='manager_test',
            password='managerpass',
            email='manager@example.com',
            first_name='Manager',
            last_name='User',
            role='manager'
        )
        self.cashier_user = User.objects.create_user(
            username='cashier_test',
            password='cashierpass',
            email='cashier@example.com',
            first_name='Cashier',
            last_name='User',
            role='cashier'
        )
        self.client = APIClient()
        self.client.force_authenticate(self.admin_user)

    def test_list_users(self):
        response = self.client.get('/api/users/')
        self.assertEqual(response.status_code, 200)
        self.assertIn('results', response.data)  # Pagination
        self.assertGreaterEqual(len(response.data['results']), 3)

    def test_list_users_with_pagination(self):
        response = self.client.get('/api/users/?page=1&page_size=2')
        self.assertEqual(response.status_code, 200)
        self.assertIn('results', response.data)
        self.assertLessEqual(len(response.data['results']), 2)

    def test_list_users_filter_by_role(self):
        response = self.client.get('/api/users/?role=admin')
        self.assertEqual(response.status_code, 200)
        for user in response.data['results']:
            self.assertEqual(user['role'], 'admin')

    def test_retrieve_user(self):
        response = self.client.get(f'/api/users/{self.manager_user.id}/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['id'], self.manager_user.id)
        self.assertEqual(response.data['email'], 'manager@example.com')
        self.assertEqual(response.data['name'], 'Manager User')
        self.assertEqual(response.data['role'], 'manager')
        self.assertIn('createdAt', response.data)
        self.assertIn('updatedAt', response.data)
        self.assertIn('active', response.data)
        self.assertIs(response.data['active'], True)

    def test_create_user(self):
        response = self.client.post(
            '/api/users/',
            {
                'username': 'newuser',
                'email': 'new@example.com',
                'first_name': 'New',
                'last_name': 'User',
                'role': 'cashier',
                'password': 'newpass123'
            },
            format='json'
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['email'], 'new@example.com')
        self.assertEqual(response.data['name'], 'New User')
        self.assertEqual(response.data['role'], 'cashier')
        self.assertTrue(User.objects.filter(username='newuser').exists())
        # Verify password was hashed
        new_user = User.objects.get(username='newuser')
        self.assertTrue(new_user.check_password('newpass123'))

    def test_create_user_requires_password(self):
        response = self.client.post(
            '/api/users/',
            {
                'username': 'nopass',
                'email': 'nopass@example.com',
                'first_name': 'No',
                'last_name': 'Pass',
                'role': 'cashier'
            },
            format='json'
        )
        self.assertEqual(response.status_code, 400)

    def test_retrieve_exposes_raw_identity_fields(self):
        """The list/retrieve DTO must expose username and first/last name so the
        edit form can prefill from source fields instead of the combined name."""
        response = self.client.get(f'/api/users/{self.manager_user.id}/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['username'], 'manager_test')
        self.assertEqual(response.data['first_name'], 'Manager')
        self.assertEqual(response.data['last_name'], 'User')
        self.assertEqual(response.data['name'], 'Manager User')

    def test_edit_name_less_user_does_not_mutate_name_fields(self):
        """Editing a user with no first/last name must never write the username
        (or anything else) into first_name when only other fields change."""
        nameless = User.objects.create_user(
            username='alex',
            password='pass',
            email='alex@example.com',
            role='cashier',
            first_name='',
            last_name='',
        )
        response = self.client.patch(
            f'/api/users/{nameless.id}/',
            {'role': 'manager'},
            format='json',
        )
        self.assertEqual(response.status_code, 200)
        nameless.refresh_from_db()
        self.assertEqual(nameless.first_name, '')
        self.assertEqual(nameless.last_name, '')
        self.assertEqual(nameless.role, 'manager')

    def test_update_user(self):
        response = self.client.patch(
            f'/api/users/{self.cashier_user.id}/',
            {
                'first_name': 'Updated',
                'last_name': 'Cashier',
                'email': 'updated@example.com'
            },
            format='json'
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['name'], 'Updated Cashier')
        self.assertEqual(response.data['email'], 'updated@example.com')
        self.cashier_user.refresh_from_db()
        self.assertEqual(self.cashier_user.first_name, 'Updated')
        self.assertEqual(self.cashier_user.last_name, 'Cashier')

    def test_update_user_password(self):
        response = self.client.patch(
            f'/api/users/{self.cashier_user.id}/',
            {'password': 'newpassword123'},
            format='json'
        )
        self.assertEqual(response.status_code, 200)
        self.cashier_user.refresh_from_db()
        self.assertTrue(self.cashier_user.check_password('newpassword123'))

    def test_delete_user(self):
        user_to_delete = User.objects.create_user(
            username='todelete',
            password='pass',
            email='delete@example.com',
            role='cashier'
        )
        response = self.client.delete(f'/api/users/{user_to_delete.id}/')
        self.assertEqual(response.status_code, 204)
        self.assertFalse(User.objects.filter(id=user_to_delete.id).exists())

    def test_cannot_delete_self(self):
        response = self.client.delete(f'/api/users/{self.admin_user.id}/')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data['detail'], 'You cannot delete your own account.')
        self.assertTrue(User.objects.filter(id=self.admin_user.id).exists())

    def test_manager_can_access(self):
        manager_client = APIClient()
        manager_client.force_authenticate(self.manager_user)
        response = manager_client.get('/api/users/')
        self.assertEqual(response.status_code, 200)

    def test_cashier_cannot_access(self):
        cashier_client = APIClient()
        cashier_client.force_authenticate(self.cashier_user)
        response = cashier_client.get('/api/users/')
        self.assertIn(response.status_code, (401, 403))

    def test_anonymous_cannot_access(self):
        anonymous = APIClient()
        response = anonymous.get('/api/users/')
        self.assertIn(response.status_code, (401, 403))

    def test_user_dto_fields(self):
        """Verify User DTO contains required fields: id, email, name, role, createdAt, updatedAt"""
        response = self.client.get(f'/api/users/{self.manager_user.id}/')
        self.assertEqual(response.status_code, 200)
        required_fields = ['id', 'email', 'name', 'role', 'active', 'createdAt', 'updatedAt']
        for field in required_fields:
            self.assertIn(field, response.data, f"Missing required field: {field}")