from rest_framework import permissions


class CanPerformRepack(permissions.IsAuthenticated):
    """Allow any authenticated, active cashier, manager, or admin (or superuser)
    to create a repack.

    R2.2: authorization is a role check only — no PIN, no second-person approval,
    no manager credential. A cashier on the floor must be able to record a
    conversion in the same shift, so the permission explicitly includes cashiers.

    Extending ``IsAuthenticated`` is important for the status contract: an
    anonymous request fails the base permission, and DRF's ``permission_denied``
    sees no successful authenticator, so it raises ``NotAuthenticated`` → HTTP 401
    (AC-25/AC-37). An authenticated-but-inactive user passes authentication but is
    rejected here with 403 and nothing written.
    """

    message = 'Only active cashiers, managers, and admins may record a repack.'
    ALLOWED_ROLES = {'cashier', 'manager', 'admin'}

    def has_permission(self, request, view):
        if not super().has_permission(request, view):
            return False
        user = request.user
        if not user.is_active:
            return False
        if user.is_superuser:
            return True
        return getattr(user, 'role', '') in self.ALLOWED_ROLES
