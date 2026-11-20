"""End-to-end regression tests for admin permissions (t_7c96ef7e).

The parent task (t_277bb838) fixed the "You don't have permission to view
or edit anything" admin failure and pinned registration/state in
``test_admin_model_registration``. This module covers the *behavioral*
contract through real credential logins — no ``force_login`` — across all
three surfaces the incident involved:

* an authorized ``role='admin'`` user can log into ``/admin/login/`` with a
  password and both view AND edit at least one registered backend resource;
* an authenticated user without the required permission (``manager`` /
  ``cashier``) is denied on every admin surface, including when they hold a
  valid frontend session;
* the same credentials on the frontend API (``/api/auth/login/``) and the
  backend admin surface report the same identity/role state — the frontend
  cannot diverge from what the backend will enforce.
"""

from django.contrib.auth import get_user_model
from django.test import Client, TestCase

from accounts.roles import apply_role_to_staff

User = get_user_model()

ADMIN_URL = '/admin/'
LOGIN_URL = '/admin/login/'
API_LOGIN_URL = '/api/auth/login/'
PASSWORD = 'sup3r-secret-pw'  # not a reserved value; not stored raw anywhere


def _mkuser(username, role, superuser=False):
    u = User(username=username, role=role, is_superuser=superuser)
    apply_role_to_staff(u)
    u.is_active = True
    u.set_password(PASSWORD)
    u.save()
    return u


def _create_user_via_admin_site(username, role):
    """Create a staff-shaped user the way an admin would through the admin."""
    u = User(username=username, role=role)
    apply_role_to_staff(u)
    u.is_active = True
    u.set_password(PASSWORD)
    u.save()
    return u


class AdminCredentialLoginAndViewEditTests(TestCase):
    """Requirement 1: authorized admin logs in and views/edits a resource."""

    def setUp(self):
        self.admin = _mkuser('adm', 'admin')
        self.client = Client()

    def _login(self):
        resp = self.client.post(LOGIN_URL, {
            'username': 'adm', 'password': PASSWORD,
        })
        self.assertEqual(resp.status_code, 302, 'admin credential login failed')
        return resp

    def test_admin_login_then_view_changelist(self):
        self._login()
        resp = self.client.get('/admin/products/product/')
        self.assertEqual(resp.status_code, 200)

    def test_admin_login_then_edit_product(self):
        from products.models import Product, Category
        cat = Category.objects.create(name='Coffee')
        Product.objects.create(name='Espresso', category=cat, price='3.50')

        self._login()
        # View the change form for a concrete object.
        resp = self.client.get('/admin/products/product/1/change/')
        self.assertEqual(
            resp.status_code, 200,
            'admin cannot view change form for a registered resource',
        )
        # Edit it through the real admin form flow: resubmit the rendered
        # form fields with a changed name so required fields stay valid.
        form_html = resp.content.decode()
        data = self._extract_form_fields(form_html)
        data['name'] = 'Espresso Doppio'
        resp = self.client.post('/admin/products/product/1/change/', data)
        self.assertEqual(resp.status_code, 302)  # saved, redirected to changelist
        self.assertEqual(Product.objects.get(pk=1).name, 'Espresso Doppio')

    @staticmethod
    def _extract_form_fields(html):
        """Return the admin form's inputs/selects as a POST payload."""
        import re
        from html import unescape
        data = {}
        for tag in re.findall(r'<(?:input|select|textarea)\b[^>]*>', html):
            name = re.search(r'name="([^"]+)"', tag)
            if not name:
                continue
            name = name.group(1)
            if name.startswith('_'):
                continue
            if name in data:
                continue
            if 'type="checkbox"' in tag and 'checked' not in tag:
                continue
            if 'type="submit"' in tag:
                continue
            m = re.search(r'value="([^"]*)"', tag)
            data[name] = unescape(m.group(1)) if m else ''
        return data

    def test_admin_login_response_lands_on_index_not_login(self):
        # The admin login form posts with ?next=/admin/ (as the browser does).
        resp = self.client.post(LOGIN_URL + '?next=/admin/', {
            'username': 'adm', 'password': PASSWORD,
        })
        self.assertEqual(resp['Location'], ADMIN_URL)
        final = self.client.get(resp['Location'])
        self.assertEqual(final.status_code, 200)
        self.assertNotContains(final, 'login-form')

    def test_wrong_password_is_rejected(self):
        resp = self.client.post(LOGIN_URL, {
            'username': 'adm', 'password': 'wrong-password',
        })
        self.assertEqual(resp.status_code, 200)  # re-renders login form
        self.assertContains(resp, 'error', status_code=200)


class AuthenticatedUserWithoutPermissionDeniedTests(TestCase):
    """Requirement 2: authenticated but unauthorized users are denied."""

    def setUp(self):
        self.manager = _mkuser('mgr', 'manager')
        self.cashier = _mkuser('cash', 'cashier')
        self.admin = _mkuser('adm', 'admin')

    def test_manager_admin_login_rejected(self):
        resp = self.client.post(LOGIN_URL, {
            'username': 'mgr', 'password': PASSWORD,
        })
        # Non-staff credentials cannot even establish an admin session.
        self.assertEqual(resp.status_code, 200)
        resp2 = self.client.get(ADMIN_URL)
        self.assertEqual(resp2.status_code, 302)

    def test_cashier_admin_login_rejected(self):
        resp = self.client.post(LOGIN_URL, {
            'username': 'cash', 'password': PASSWORD,
        })
        self.assertEqual(resp.status_code, 200)
        resp2 = self.client.get(ADMIN_URL)
        self.assertEqual(resp2.status_code, 302)

    def test_frontend_authenticated_cashier_denied_on_admin(self):
        # Establish a real session through the frontend API, then hit admin.
        api_resp = self.client.post(API_LOGIN_URL, {
            'username': 'cash', 'password': PASSWORD,
        }, content_type='application/json')
        self.assertEqual(api_resp.status_code, 200, 'frontend login failed')
        resp = self.client.get(ADMIN_URL)
        self.assertEqual(resp.status_code, 302)

    def test_frontend_authenticated_manager_denied_on_admin(self):
        api_resp = self.client.post(API_LOGIN_URL, {
            'username': 'mgr', 'password': PASSWORD,
        }, content_type='application/json')
        self.assertEqual(api_resp.status_code, 200, 'frontend login failed')
        resp = self.client.get(ADMIN_URL)
        self.assertEqual(resp.status_code, 302)


class SharedCredentialsConsistencyTests(TestCase):
    """Requirement 3: identity/role state is consistent across surfaces.

    The frontend and the backend admin share the same session-based
    credential store. Whatever the frontend reports about a user's role
    must match what the backend admin will actually enforce, otherwise a
    user sees a frontend that says "admin" while the backend denies them
    (the exact incident this task tracks) — or worse, the reverse.
    """

    def setUp(self):
        self.admin = _mkuser('adm', 'admin')
        self.cashier = _mkuser('cash', 'cashier')
        self.client = Client()

    def _api_login(self, username):
        resp = self.client.post(API_LOGIN_URL, {
            'username': username, 'password': PASSWORD,
        }, content_type='application/json')
        self.assertEqual(resp.status_code, 200)
        return resp.json()

    def test_admin_role_reported_by_frontend_matches_backend(self):
        payload = self._api_login('adm')
        user = User.objects.get(username='adm')
        self.assertEqual(payload['user']['role'], user.role)
        self.assertEqual(payload['user']['role'], 'admin')
        self.assertTrue(user.is_staff, 'admin role must map to is_staff')
        # The same credentials actually reach the backend admin.
        resp = self.client.post(LOGIN_URL, {
            'username': 'adm', 'password': PASSWORD,
        })
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(self.client.get(ADMIN_URL).status_code, 200)

    def test_cashier_role_reported_by_frontend_matches_backend(self):
        payload = self._api_login('cash')
        user = User.objects.get(username='cash')
        self.assertEqual(payload['user']['role'], user.role)
        self.assertEqual(payload['user']['role'], 'cashier')
        self.assertFalse(user.is_staff, 'cashier must not be staff')
        # Same credentials cannot establish a backend session.
        resp = self.client.post(LOGIN_URL, {
            'username': 'cash', 'password': PASSWORD,
        })
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self.client.get(ADMIN_URL).status_code, 302)

    def test_promotion_propagates_between_frontend_and_backend(self):
        # Promote the cashier through the backend write path.
        self.cashier.role = 'admin'
        apply_role_to_staff(self.cashier)
        self.cashier.save()
        self.assertTrue(User.objects.get(username='cash').is_staff)

        # Frontend now reports admin, and the backend honors it.
        payload = self._api_login('cash')
        self.assertEqual(payload['user']['role'], 'admin')
        resp = self.client.post(LOGIN_URL, {
            'username': 'cash', 'password': PASSWORD,
        })
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(self.client.get(ADMIN_URL).status_code, 200)

    def test_demotion_revokes_backend_even_if_frontend_session_lingers(self):
        # Login on the frontend as admin, then demote via the backend.
        self._api_login('adm')
        self.admin.role = 'cashier'
        apply_role_to_staff(self.admin)
        self.admin.save()

        user = User.objects.get(username='adm')
        self.assertFalse(user.is_staff)
        resp = self.client.post(LOGIN_URL, {
            'username': 'adm', 'password': PASSWORD,
        })
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self.client.get(ADMIN_URL).status_code, 302)
