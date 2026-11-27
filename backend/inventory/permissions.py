from rest_framework import permissions
from rest_framework.authentication import SessionAuthentication


class RepackSessionAuthentication(SessionAuthentication):
    """Session auth whose ``WWW-Authenticate`` challenge is non-empty.

    DRF's ``APIView.handle_exception`` coerces a ``NotAuthenticated``
    (401) into 403 when ``get_authenticate_header()`` returns ``None`` —
    which is exactly what the stock ``SessionAuthentication`` does. Because
    the site runs only Session auth, an anonymous repack request would
    otherwise surface as 403. Giving these views an authenticator whose
    ``authenticate_header()`` returns a real challenge lets the spec's
    401 (R1.9/R2.3, AC-25/AC-36) survive the handler, with no change to the
    global ``REST_FRAMEWORK`` auth classes (AC-48).
    """

    def authenticate_header(self, request):
        return 'Session'


class CanPerformRepack(permissions.IsAuthenticated):
    """Allow any authenticated, active cashier, manager, or admin (or superuser)
    to create a repack.

    R2.2: authorization is a role check only — no PIN, no second-person approval,
    no manager credential. A cashier on the floor must be able to record a
    conversion in the same shift, so the permission explicitly includes cashiers.

    This permission is deliberately backed by ``IsAuthenticated`` so anonymous
    callers are rejected before any work. Note that the site configures only
    ``SessionAuthentication``, whose ``authenticate_header()`` returns ``None``;
    DRF's ``handle_exception`` then coerces a ``NotAuthenticated`` (401) into
    403. The repack views therefore combine a per-view ``RepackSessionAuthentication``
    (which supplies a non-empty WWW-Authenticate challenge) with a
    ``permission_denied`` override, so anonymous callers get the spec-mandated
    401 (R1.9/R2.3, AC-25/AC-36) while an authenticated-but-inactive (or
    wrong-role) user still receives 403 and nothing is written (AC-37). The
    401-vs-403 contract is enforced by the view overrides, not this permission
    class.
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
