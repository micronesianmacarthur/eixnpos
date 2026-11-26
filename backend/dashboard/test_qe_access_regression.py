"""QE regression tests for the dashboard access rule (t_efa94bca).

The access rule under test (contract §7.2 as amended by t_da257f6d):

* Every ``/api/dashboard/*`` read endpoint is available to **any
  authenticated user** — cashier, manager, and admin alike.
* Anonymous/unauthenticated callers are rejected on every dashboard
  endpoint.
* The intentionally privileged dashboard actions stay restricted:
  ``GET /api/dashboard/backup/`` (leaks an absolute ``last_backup_path``)
  and the companion ``POST /api/backups/trigger/`` remain manager/admin
  only.

Each test name states the role and the expectation so a failure reads as
the access-control contract being broken, not as a data bug. These tests
exercise real HTTP routing/permission checks (``APIClient`` without
``force_login`` shortcuts bypassing DRF permissions).
"""
from rest_framework.test import APIClient

from dashboard.tests import DashboardBase


class DashboardAccessRegressionTests(DashboardBase):
    """Role x endpoint matrix for the dashboard access rule."""

    _READ_URLS = (
        '/api/dashboard/',
        '/api/dashboard/sales/today/',
        '/api/dashboard/sales/period/?start=2026-11-01&end=2026-11-24',
        '/api/dashboard/sales/top/',
        '/api/dashboard/sales/monthly/',
        '/api/dashboard/sessions/current/',
        '/api/dashboard/inventory/low-stock/',
    )
    _BACKUP_URL = '/api/dashboard/backup/'
    _BACKUP_TRIGGER_URL = '/api/backups/trigger/'

    def _assert_all_read_urls(self, status_code: int) -> None:
        for url in self._READ_URLS:
            response = self.client.get(url)
            self.assertEqual(
                response.status_code, status_code,
                msg=f'{url} -> {response.status_code}, expected {status_code}',
            )

    # -- anonymous access is rejected everywhere ------------------------ #

    def test_anonymous_user_is_rejected_on_every_dashboard_read_endpoint(self):
        # SessionAuthentication answers an anonymous request with 403
        # PermissionDenied (401 would require a WWW-Authenticate challenge).
        self._assert_all_read_urls(403)

    def test_anonymous_user_is_rejected_on_backup_status_endpoint(self):
        self.assertEqual(
            self.client.get(self._BACKUP_URL).status_code, 403)

    def test_anonymous_user_is_rejected_on_backup_trigger_action(self):
        self.assertEqual(
            self.client.post(self._BACKUP_TRIGGER_URL).status_code, 403)

    # -- cashier: full read access, restricted actions ------------------ #

    def test_cashier_can_access_every_dashboard_read_endpoint(self):
        self.client.force_authenticate(self.cashier)
        self._assert_all_read_urls(200)

    def test_cashier_is_forbidden_on_backup_status_endpoint(self):
        self.client.force_authenticate(self.cashier)
        self.assertEqual(
            self.client.get(self._BACKUP_URL).status_code, 403)

    def test_cashier_is_forbidden_on_backup_trigger_action(self):
        self.client.force_authenticate(self.cashier)
        self.assertEqual(
            self.client.post(self._BACKUP_TRIGGER_URL).status_code, 403)

    def test_cashier_post_to_dashboard_read_endpoint_is_not_allowed(self):
        # Read endpoints are read-only: a write verb must not slip through
        # just because the user is authenticated.
        self.client.force_authenticate(self.cashier)
        self.assertIn(
            self.client.post('/api/dashboard/').status_code,
            (403, 405),
        )

    # -- manager: full read access plus the manager-tier backup status -- #

    def test_manager_can_access_every_dashboard_read_endpoint(self):
        self.client.force_authenticate(self.manager)
        self._assert_all_read_urls(200)

    def test_manager_can_read_backup_status_endpoint(self):
        self.client.force_authenticate(self.manager)
        self.assertEqual(
            self.client.get(self._BACKUP_URL).status_code, 200)

    # -- admin: full read access plus the manager-tier backup status ---- #

    def test_admin_can_access_every_dashboard_read_endpoint(self):
        self.client.force_authenticate(self.admin)
        self._assert_all_read_urls(200)

    def test_admin_can_read_backup_status_endpoint(self):
        self.client.force_authenticate(self.admin)
        self.assertEqual(
            self.client.get(self._BACKUP_URL).status_code, 200)

    # -- no accidental widening ----------------------------------------- #

    def test_dashboard_access_grants_no_extra_privileges_to_cashier(self):
        # The dashboard unlock is read-only scope: it must not leak into
        # neighbouring privileged surfaces (user management).
        self.client.force_authenticate(self.cashier)
        self.assertEqual(
            self.client.get('/api/users/').status_code, 403)
