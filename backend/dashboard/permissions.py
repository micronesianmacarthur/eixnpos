"""Access control for the dashboard API.

The dashboard exposes sales/inventory aggregates, so every ``/api/dashboard/*``
read endpoint requires an authenticated user (contract §7.2:
``IsDashboardReader``). Any authenticated role — cashier, manager, admin — may
read dashboard aggregates; write/destructive actions remain gated by their own
viewsets, and the backup-status endpoint stays manager-tier (it leaks an
absolute ``last_backup_path`` and its companion trigger is manager-only).

Anonymous users are rejected with 401 (DRF's default for an unauthenticated
request that fails permission).
"""
from rest_framework.permissions import BasePermission


class IsDashboardReader(BasePermission):
    """Allow any authenticated user to read dashboard aggregates.

    Anonymous callers are rejected (401/403 per DRF's SessionAuthentication).
    Write/destructive actions remain gated by their own viewsets.
    """

    message = 'Authentication is required to access the dashboard.'

    def has_permission(self, request, view):
        user = request.user
        return bool(user) and user.is_authenticated
