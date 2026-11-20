"""Regression tests: functional POS models are registered with role-aware admin.

The reported failure (t_277bb838) was a role='admin' staff user reaching
``/admin/`` but seeing "You don't have permission to view or edit anything".
Root cause (audit t_022a4cb5): every business app's ``admin.py`` was empty or
missing, so only ``accounts.User`` and ``auth.Group`` were registered and no
functional model was visible or editable.

These tests pin the fix: the business models are registered with a
role-aware ``ModelAdmin`` (``accounts.admin_base.RoleAwareModelAdmin``), so

* a ``role='admin'`` staff user sees the functional models in the admin index
  and can open their changelists,
* ``role='manager'`` / ``role='cashier'`` users (non-staff) remain denied,
* superusers retain full access.
"""

from django.contrib import admin
from django.test import TestCase
from django.test import Client

from django.contrib.auth import get_user_model

from accounts.roles import apply_role_to_staff

User = get_user_model()

#: Models that must be registered for the fix to hold, one per app.
REQUIRED_REGISTRATIONS = {
    'business.businessinfo',
    'products.category',
    'products.product',
    'customers.customer',
    'suppliers.supplier',
    'inventory.stockmovement',
    'sales.sale',
    'pos_sessions.register',
    'pos_sessions.session',
}


def _mkuser(username, role, superuser=False, staff=None):
    u = User(username=username, role=role, is_superuser=superuser)
    apply_role_to_staff(u)  # derives is_staff from role
    if staff is not None:
        u.is_staff = staff
    u.is_active = True
    u.set_password('pw-x')
    u.save()
    return u


class AdminModelRegistrationTests(TestCase):
    def setUp(self):
        # The operator can see every model once registered.
        self.admin = _mkuser('adm', 'admin')
        self.manager = _mkuser('mgr', 'manager')
        self.cashier = _mkuser('cash', 'cashier')
        self.superuser = _mkuser('root', 'cashier', superuser=True)

    def _admin_landing(self, user):
        """Hit /admin/ without following; returns the raw response."""
        c = Client()
        c.force_login(user)
        return c.get('/admin/')

    def _index_status(self, user):
        c = Client()
        c.force_login(user)
        return c.get('/admin/', follow=True)

    def test_functional_models_are_registered(self):
        registered = {f"{m._meta.app_label}.{m._meta.model_name}" for m in admin.site._registry}
        self.assertTrue(
            REQUIRED_REGISTRATIONS <= registered,
            f"missing registrations: {sorted(REQUIRED_REGISTRATIONS - registered)}",
        )

    def test_admin_can_view_admin_index_without_permission_message(self):
        resp = self._index_status(self.admin)
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode()
        # The no-permission banner must NOT appear for an admin.
        self.assertNotIn("You don&#x27;t have permission to view or edit anything", body)
        self.assertNotIn("You don't have permission to view or edit anything", body)

    def test_admin_can_open_functional_changelist(self):
        for url in ['/admin/products/product/', '/admin/products/category/',
                    '/admin/customers/customer/', '/admin/suppliers/supplier/',
                    '/admin/inventory/stockmovement/', '/admin/sales/sale/',
                    '/admin/pos_sessions/register/', '/admin/business/businessinfo/']:
            c = Client()
            c.force_login(self.admin)
            resp = c.get(url)
            self.assertEqual(resp.status_code, 200, f"{url} denied for admin: {resp.status_code}")

    def test_nonadmin_users_are_denied(self):
        for user in (self.manager, self.cashier):
            # Non-staff users are redirected away from /admin/ (302 -> login),
            # never shown the admin index.
            resp = self._admin_landing(user)
            self.assertEqual(resp.status_code, 302, f"{user.role} reached /admin/")

    def test_superuser_retains_admin_access(self):
        resp = self._index_status(self.superuser)
        self.assertEqual(resp.status_code, 200)
