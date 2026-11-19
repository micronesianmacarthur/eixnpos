from rest_framework import permissions


class IsManagerOrAdmin(permissions.BasePermission):
    """
    Permission class that allows access only to users with role 'manager' or 'admin',
    or Django staff/superuser status.
    """

    message = "Only managers and admins can access this resource."

    def has_permission(self, request, view):
        user = request.user
        if not user or not getattr(user, 'is_authenticated', False):
            return False
        if getattr(user, 'is_staff', False) or getattr(user, 'is_superuser', False):
            return True
        return getattr(user, 'role', '') in ('manager', 'admin')


class IsAdminRole(permissions.BasePermission):
    """
    Permission class that allows access only to users with role 'admin',
    or Django staff/superuser status.

    Mirrors ``IsManagerOrAdmin`` but for the admin tier alone, so admin-only
    endpoints are gated on the single ``role='admin'`` field rather than on
    Django's ``is_staff`` flag — keeping the role as the one enforcement point
    for the app's authz model.
    """

    message = "Only admins can access this resource."

    def has_permission(self, request, view):
        user = request.user
        if not user or not getattr(user, 'is_authenticated', False):
            return False
        if getattr(user, 'is_staff', False) or getattr(user, 'is_superuser', False):
            return True
        return getattr(user, 'role', '') == 'admin'