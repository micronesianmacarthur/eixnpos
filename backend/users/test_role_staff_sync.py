"""Acceptance tests: ``role`` <-> ``is_staff`` stay in sync across CRUD.

Verifies the B1/B2 invariants at the API layer (see t_eb6bfd49):

* an API-created ``role='admin'`` user gains ``is_staff`` and reaches ``/admin/``
  (Blocker B2 — the normal creation path),
* demoting an admin to a non-admin role revokes ``is_staff`` and every
  admin/manager surface (Blocker B1),
* promoting a cashier to admin grants the backend,
* a superuser keeps ``is_staff``/``is_superuser`` through a role change, and
* a manager never reaches ``/admin/`` or the admin dashboard.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.roles import has_admin_role, has_manager_role
from accounts.utils import is_manager

User = get_user_model()


class RoleStaffSyncApiTests(TestCase):
    def setUp(self):
        self.boss = User.objects.create_user(
            username='boss', password='BossPass1', role='admin', is_staff=True
        )
        self.client = APIClient()
        self.client.force_authenticate(self.boss)

    def _admin_index_status(self, user):
        """Status code of ``/admin/`` for *user*'s own session."""
        client = APIClient()
        client.force_login(user)
        return client.get('/admin/').status_code

    def test_api_created_admin_gains_is_staff_and_reaches_admin_site(self):
        response = self.client.post(
            '/api/users/',
            {'username': 'newadmin', 'password': 'AdminPass1', 'role': 'admin'},
            format='json',
        )
        self.assertEqual(response.status_code, 201)

        created = User.objects.get(username='newadmin')
        self.assertEqual(created.role, 'admin')
        self.assertTrue(created.is_staff)
        self.assertFalse(created.is_superuser)
        self.assertEqual(self._admin_index_status(created), 200)

    def test_api_created_cashier_stays_non_staff(self):
        response = self.client.post(
            '/api/users/',
            {'username': 'newcash', 'password': 'CashPass1', 'role': 'cashier'},
            format='json',
        )
        self.assertEqual(response.status_code, 201)
        created = User.objects.get(username='newcash')
        self.assertFalse(created.is_staff)
        self.assertEqual(self._admin_index_status(created), 302)

    def test_demoting_admin_to_cashier_revokes_every_admin_surface(self):
        admin = User.objects.create_user(
            username='adm1', password='Adm1Passss', role='admin', is_staff=True
        )

        response = self.client.patch(
            f'/api/users/{admin.id}/', {'role': 'cashier'}, format='json'
        )
        self.assertEqual(response.status_code, 200)

        admin.refresh_from_db()
        self.assertEqual(admin.role, 'cashier')
        self.assertFalse(admin.is_staff)
        self.assertFalse(is_manager(admin))
        self.assertFalse(has_manager_role(admin))
        self.assertFalse(has_admin_role(admin))

        # The demoted user's own session is bounced from the stock admin site.
        self.assertEqual(self._admin_index_status(admin), 302)

        # And their API credentials no longer clear manager/admin gates.
        demoted = APIClient()
        demoted.force_authenticate(admin)
        self.assertEqual(demoted.get('/api/users/').status_code, 403)
        self.assertEqual(
            demoted.get('/api/auth/admin-dashboard/').status_code, 403
        )

    def test_promoting_cashier_to_admin_grants_backend(self):
        cashier = User.objects.create_user(
            username='cash1', password='CashPass1', role='cashier'
        )
        self.assertFalse(cashier.is_staff)

        response = self.client.patch(
            f'/api/users/{cashier.id}/', {'role': 'admin'}, format='json'
        )
        self.assertEqual(response.status_code, 200)

        cashier.refresh_from_db()
        self.assertEqual(cashier.role, 'admin')
        self.assertTrue(cashier.is_staff)
        self.assertTrue(is_manager(cashier))
        self.assertEqual(self._admin_index_status(cashier), 200)

    def test_superuser_keeps_staff_and_superuser_through_role_change(self):
        superuser = User.objects.create_user(
            username='su1', password='SuPass123', role='manager',
            is_superuser=True, is_staff=True  # a real superuser row
        )
        self.assertTrue(superuser.is_staff)
        self.assertTrue(superuser.is_superuser)

        response = self.client.patch(
            f'/api/users/{superuser.id}/', {'role': 'cashier'}, format='json'
        )
        self.assertEqual(response.status_code, 200)

        superuser.refresh_from_db()
        self.assertEqual(superuser.role, 'cashier')
        self.assertTrue(superuser.is_staff)  # never cleared for a superuser
        self.assertTrue(superuser.is_superuser)
        # Superuser bypass keeps backend + admin surfaces reachable.
        self.assertTrue(is_manager(superuser))
        self.assertEqual(self._admin_index_status(superuser), 200)
        session = APIClient()
        session.force_authenticate(superuser)
        self.assertEqual(
            session.get('/api/auth/admin-dashboard/').status_code, 200
        )

    def test_manager_never_reaches_admin_site_or_dashboard(self):
        manager = User.objects.create_user(
            username='mgr1', password='MgrPass1', role='manager'
        )
        self.assertFalse(manager.is_staff)
        self.assertTrue(is_manager(manager))  # manager tier, not admin tier
        self.assertEqual(self._admin_index_status(manager), 302)

        session = APIClient()
        session.force_authenticate(manager)
        self.assertEqual(
            session.get('/api/auth/admin-dashboard/').status_code, 403
        )
