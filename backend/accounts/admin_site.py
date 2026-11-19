"""Hardened Django admin site for the admin login surface.

The stock ``/admin/login/`` is a separate credential surface from the DRF
``login_view``. Now that every ``role='admin'`` user is also ``is_staff``, that
stock login became a live, unthrottled oracle:

* it was not covered by ``accounts.throttling`` (unlimited password guesses), and
* it accepted the reserved seeded credential ``admin/admin``, because
  ``accounts.validators.is_reserved_password`` was only enforced in
  ``login_view``.

This module swaps in a custom ``AdminSite`` plus authentication form so the
admin login applies the same two policies as the API login:

* a failed-attempt budget (new ``admin_login`` throttle namespace, limits in
  ``config/settings.py``) that returns HTTP 429 once exhausted and clears on a
  successful login, and
* rejection of any reserved/default credential, surfacing the forced-password-
  change path instead of authenticating.

It is wired in by ``config/settings.py`` replacing ``django.contrib.admin`` in
``INSTALLED_APPS`` with ``accounts.admin_config.EixnPOSAdminConfig`` (see
Django's ``AdminConfig.default_site`` mechanism), so the existing
``@admin.register`` model registrations continue to target the same (custom)
site through ``admin.site``.
"""

from django.contrib import admin
from django.contrib.admin.forms import AdminAuthenticationForm
from django.contrib.auth import authenticate
from django.core.exceptions import ValidationError
from django.http import HttpResponse
from django.utils.translation import gettext_lazy as _

from . import audit, throttling
from .validators import is_reserved_password

#: Throttle namespace for the admin login, mirroring the API ``login`` one.
#: Limits/windows are defined in ``config/settings.py``.
THROTTLE_NAMESPACE = 'admin_login'

#: Message returned once the failed-attempt budget is exhausted.
LOCKED_MESSAGE = 'Too many failed login attempts. Try again later.'

#: Message shown when a reserved/default credential is submitted.
RESERVED_LOGIN_MESSAGE = (
    'Sign-in with the default/reserved credential is not allowed. '
    'Change the password first.'
)


class EixnPOSAdminAuthenticationForm(AdminAuthenticationForm):
    """Admin login form that throttles failures and rejects reserved passwords.

    Mirrors the policy enforced in ``accounts.login_view`` while staying on the
    stock Django ``LoginView`` flow (``/admin/login/``). A reserved password is
    only rejected for an account that actually holds it — a guess of ``admin``
    against a strong-password account is a plain wrong-password failure, so a
    stranger cannot use the reserved check to lock someone else out.
    """

    error_messages = {
        **AdminAuthenticationForm.error_messages,
        'must_change_password': _(RESERVED_LOGIN_MESSAGE),
        'throttled': _(LOCKED_MESSAGE),
    }

    def clean(self):
        username = self.cleaned_data.get('username')
        password = self.cleaned_data.get('password')
        ip = throttling.client_ip(self.request)
        identity = username or ip

        # Lockout gate (defence in depth; the AdminSite.login pre-check also
        # returns 429 before the form runs).
        if throttling.is_blocked(THROTTLE_NAMESPACE, identity, ip):
            raise ValidationError(self.error_messages['throttled'], code='throttled')

        user = authenticate(self.request, username=username, password=password)
        if user is None:
            # Wrong password / unknown account: record the guess.
            throttling.register_failure(THROTTLE_NAMESPACE, identity, ip)
            audit.login_failure(self.request, username)
            raise self.get_invalid_login_error()

        # A reserved credential on an account that really holds it is not a
        # valid session — force the password-change path instead. It is also a
        # failure against the budget (it is the seeded-credential oracle).
        if is_reserved_password(password):
            throttling.register_failure(THROTTLE_NAMESPACE, identity, ip)
            audit.login_failure(self.request, username)
            raise ValidationError(
                self.error_messages['must_change_password'],
                code='must_change_password',
            )

        if not user.is_active:
            throttling.register_failure(THROTTLE_NAMESPACE, identity, ip)
            raise ValidationError(self.error_messages['inactive'], code='inactive')

        if not user.is_staff:
            # Matches AdminAuthenticationForm: non-staff must not sign in.
            throttling.register_failure(THROTTLE_NAMESPACE, identity, ip)
            audit.login_failure(self.request, username)
            raise self.get_invalid_login_error()

        # Successful staff login clears the budget.
        self.user_cache = user
        throttling.reset(THROTTLE_NAMESPACE, identity, ip)
        audit.login_success(self.request, username)
        return self.cleaned_data


class EixnPOSAdminSite(admin.AdminSite):
    """Admin site whose login enforces throttling and reserved-password rules."""

    login_form = EixnPOSAdminAuthenticationForm

    def login(self, request, extra_context=None):
        """Return HTTP 429 once the failed-attempt budget is exhausted.

        The form above also guards, but returning the 4-class status at the view
        layer keeps the lockout unambiguous for clients (and matches the API
        login's ``429``). Everything else — CSRF, sessions, the logged-in GET
        redirect — stays on the stock ``LoginView`` via ``super().login()``.
        """
        if request.method == 'POST':
            username = request.POST.get('username', '') or ''
            ip = throttling.client_ip(request)
            identity = username or ip
            if throttling.is_blocked(THROTTLE_NAMESPACE, identity, ip):
                audit.login_failure(request, username, blocked=True)
                return HttpResponse(LOCKED_MESSAGE, status=429)
        return super().login(request, extra_context)
