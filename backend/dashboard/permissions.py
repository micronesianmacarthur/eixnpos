"""Manager-tier access control for the dashboard API.

The dashboard exposes sales/inventory aggregates, so every ``/api/dashboard/*``
endpoint requires manager-tier authority. A single DRF permission class gates
all of them (per the dashboard metrics & data contract §7.2: ``IsDashboardReader``).

Manager tier == ``role in (manager, admin)``; superusers bypass. Delegates to the
shared ``has_manager_role`` predicate so there is one source of truth for the tier.
Anonymous users are rejected with 401 (DRF's default for an unauthenticated
request that fails permission); cashiers are rejected with 403.
"""
from rest_framework.permissions import BasePermission

from accounts.roles import has_manager_role


class IsDashboardReader(BasePermission):
    """Allow manager-tier (manager/admin) authority; superusers bypass."""

    message = 'Only managers and admins can access the dashboard.'

    def has_permission(self, request, view):
        return has_manager_role(request.user)
