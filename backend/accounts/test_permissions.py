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

    def test_cashier_with_stale_staff_flag_is_still_denied(self):
        """A demoted administrator (cashier role, stale is_staff) is revoked.

        This is the demotion invariant: ``is_staff`` alone must not grant
        manager-tier authority — only the role does — so a demoted admin cannot
        keep using the users API even if a stale staff flag lingers.
        """
        staff = User.objects.create_user(username='staff', password='pw', role='cashier', is_staff=True)
        try:
            self.client.force_authenticate(staff)
            response = self.client.get('/api/auth/users/')
            self.assertEqual(response.status_code, 403)
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


class AdminDashboardPermissionTests(TestCase):
    """The admin dashboard is admin-tier: gated on ``role='admin'``.

    The endpoint deliberately uses the app's role check (``IsAdminRole``)
    rather than DRF's ``IsAdminUser`` (which is strictly ``is_staff``), so the
    admin tier lives in one place and works even where ``is_staff`` has not yet
    been backfilled by the ``0004_admin_role_to_staff`` data migration.
    """

    def setUp(self):
        self.client = APIClient()
        self.cashier = User.objects.create_user(username='dash_cashier', password='pw', role='cashier')
        self.manager = User.objects.create_user(username='dash_manager', password='pw', role='manager')
        self.admin = User.objects.create_user(username='dash_admin', password='pw', role='admin')

    def tearDown(self):
        for user in (self.admin, self.manager, self.cashier):
            user.delete()

    def test_admin_role_can_access_admin_dashboard(self):
        """An admin-role user (even without is_staff) reaches the dashboard."""
        self.client.force_authenticate(self.admin)
        response = self.client.get('/api/auth/admin-dashboard/')
        self.assertEqual(response.status_code, 200)

    def test_manager_cannot_access_admin_dashboard(self):
        """Managers are non-admin and stay blocked on the admin dashboard."""
        self.client.force_authenticate(self.manager)
        response = self.client.get('/api/auth/admin-dashboard/')
        self.assertEqual(response.status_code, 403)

    def test_cashier_cannot_access_admin_dashboard(self):
        """Cashiers are non-admin and stay blocked on the admin dashboard."""
        self.client.force_authenticate(self.cashier)
        response = self.client.get('/api/auth/admin-dashboard/')
        self.assertEqual(response.status_code, 403)

    def test_unauthenticated_cannot_access_admin_dashboard(self):
        """Anonymous requests are rejected on the admin dashboard."""
        response = self.client.get('/api/auth/admin-dashboard/')
        self.assertIn(response.status_code, (401, 403))


class DjangoAdminSitePermissionTests(TestCase):
    """The stock Django admin site only admits staff users.

    ``role='admin'`` now maps to ``is_staff=True`` (via the 0004 data migration
    and seed sync), so admins reach ``/admin/``; non-admin roles (no staff)
    remain redirected away.
    """

    def setUp(self):
        self.cashier = User.objects.create_user(username='adm_cashier', password='pw', role='cashier')
        self.admin = User.objects.create_user(
            username='adm_admin', password='pw', role='admin', is_staff=True
        )

    def tearDown(self):
        self.admin.delete()
        self.cashier.delete()

    def test_staff_admin_can_reach_admin_index(self):
        """A role='admin' (staff) user gets a 200 from the admin index."""
        self.client.force_login(self.admin)
        response = self.client.get('/admin/')
        self.assertEqual(response.status_code, 200)

    def test_non_staff_cashier_redirected_from_admin(self):
        """A non-staff cashier is bounced to the admin login page."""
        self.client.force_login(self.cashier)
        response = self.client.get('/admin/')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/admin/login/', response['Location'])