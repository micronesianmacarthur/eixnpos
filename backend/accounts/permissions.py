from rest_framework import permissions

from accounts.roles import has_admin_role, has_manager_role


class IsManagerOrAdmin(permissions.BasePermission):
    """Allow users with manager-tier authority ('manager' or 'admin' role).

    Superusers bypass regardless of role. ``is_staff`` alone grants nothing
    here — manager authority comes from the role, so a demoted administrator
    (still carrying a stale staff flag) is correctly locked out. Use
    ``apply_role_to_staff`` on every role write so stale flags do not linger.
    """

    message = "Only managers and admins can access this resource."

    def has_permission(self, request, view):
        return has_manager_role(request.user)


class IsAdminRole(permissions.BasePermission):
    """Allow users with admin-tier authority (role='admin').

    Superusers bypass regardless of role. The single enforcement point is the
    ``role`` field via ``has_admin_role``; ``is_staff`` by itself neither
    grants nor revokes admin authority, keeping role as the one source of
    truth for the app's authz model.
    """

    message = "Only admins can access this resource."

    def has_permission(self, request, view):
        return has_admin_role(request.user)
