"""Global guard that enforces the forced-password-change flow.

Implemented as Django middleware (rather than a DRF permission) because every
authenticated viewset in the project sets an explicit ``permission_classes``
list that would override any DRF default — a middleware is the only way to
cover *all* protected endpoints uniformly with a single change.

Threading the check through Django's own request user (session-backed, the
same credential ``SessionAuthentication`` relies on) keeps it consistent with
the rest of the auth stack. Only the endpoints a user needs to complete the
flow are exempt (see ``docs/designs/force-password-change.md`` §4.3).
"""

from django.http import JsonResponse

#: Paths reachable while ``must_change_password`` is set. These are the only
#: views the design exempts from the guard — login (public), change-password,
#: me (so the pending state can be surfaced), plus csrf/logout which must stay
#: usable for session bookkeeping.
EXEMPT_PATHS = {
    '/api/auth/login/',
    '/api/auth/logout/',
    '/api/auth/change-password/',
    '/api/auth/me/',
    '/api/auth/csrf/',
}

BLOCKED_PAYLOAD = {
    'password_change_required': True,
    'detail': 'Password change required before accessing this resource.',
}


class PasswordChangeRequiredMiddleware:
    """Reject any protected request while the authenticated user's
    ``must_change_password`` flag is still set."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, 'user', None)
        if (
            request.path not in EXEMPT_PATHS
            and user is not None
            and getattr(user, 'is_authenticated', False)
            and user.must_change_password
        ):
            # Low-level audit: every guard rejection is recorded with the user
            # and client IP (never session keys). Done here at the middleware
            # level so every protected path variant is covered uniformly.
            from accounts import audit
            audit.guard_blocked(request, getattr(user, 'username', None))
            return JsonResponse(BLOCKED_PAYLOAD, status=403)
        return self.get_response(request)
