"""Custom password validators enforcing the forced-password-change policy.

Defined in the app (not just the design doc) and enabled in settings via
``AUTH_PASSWORD_VALIDATORS``. See ``docs/designs/force-password-change.md`` §5.
"""

from django.core.exceptions import ValidationError

#: Length floor for the "character mix" rule (kept in one place).
MIN_LENGTH = 8

#: Values that may never be chosen as a password (the seeded default admin
#: credential is the canonical member). Normalized to a lowercase tuple.
RESERVED_VALUES = ('admin',)


def is_reserved_password(password):
    """Return True if *password* matches any reserved/default value.

    Shared by the validator, ``login_view`` and the user serializers so the
    reserved-value definition lives in exactly one place; comparison follows
    Django's own forgiving password semantics (trim, case-insensitive).
    """
    if not password:
        return False
    return password.strip().lower() in RESERVED_VALUES

#: Exact response message mandated by the API contract (§4.2).
MIX_MESSAGE = (
    'New password must be at least 8 characters long and contain both '
    'letters and digits.'
)

#: Exact response message mandated by the API contract (§4.2).
RESERVED_MESSAGE = (
    "New password must not be the default or reserved value 'admin'."
)


class ReservedPasswordValidator:
    """Reject the seeded default / reserved password values (e.g. ``'admin'``).

    Kept as its own validator so the reserved-value error takes precedence
    over the generic strength check when they both apply (e.g. the normal
    first change where both the current and new password are ``'admin'``).
    """

    def validate(self, password, user=None):
        if is_reserved_password(password):
            raise ValidationError(
                RESERVED_MESSAGE,
                code='password_reserved',
                params={'value': password},
            )

    def get_help_text(self):
        return "Your password must not be the default or reserved value 'admin'."


class CharacterMixValidator:
    """Require a minimum length plus at least one letter and one digit."""

    def validate(self, password, user=None):
        if (
            len(password) < MIN_LENGTH
            or not any(c.isalpha() for c in password)
            or not any(c.isdigit() for c in password)
        ):
            raise ValidationError(MIX_MESSAGE, code='password_mix')

    def get_help_text(self):
        return (
            'Your password must be at least 8 characters long and contain '
            'both letters and digits.'
        )
