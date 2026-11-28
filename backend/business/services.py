"""Application settings service.

Settings are persisted as key/value rows (``AppSetting``) and parsed here so the
storage format never leaks into callers. Each known setting gets a small typed
accessor plus the validation that makes its value safe to use.
"""

from decimal import Decimal, InvalidOperation

from .models import AppSetting

#: Key under which the default target margin percentage is stored.
DEFAULT_TARGET_MARGIN_PERCENT_KEY = 'default_target_margin_percent'

#: Used on a fresh install (and whenever a stored value is missing/corrupt).
DEFAULT_TARGET_MARGIN_PERCENT = Decimal('15')

#: Inclusive bounds for a target margin percentage.
MIN_TARGET_MARGIN_PERCENT = Decimal('0')
MAX_TARGET_MARGIN_PERCENT = Decimal('100')

#: Maximum fraction digits accepted for the margin percentage.
TARGET_MARGIN_PERCENT_DECIMAL_PLACES = 2

_MARGIN_ERROR = 'Enter a number between 0 and 100.'


def _canonical(value):
    """Render a Decimal without exponent notation or trailing zeros."""
    text = format(value, 'f')
    if '.' in text:
        text = text.rstrip('0').rstrip('.')
    return text or '0'


def normalize_target_margin_percent(raw):
    """Validate *raw* and return it as a Decimal in the 0-100 range.

    Accepts ints, finite floats and numeric strings. Rejects booleans, ``None``,
    non-numeric text, NaN/Infinity, out-of-range values and more than two
    fraction digits. Raises ``ValueError`` with a user-facing message.
    """
    if raw is None or isinstance(raw, bool):
        raise ValueError(_MARGIN_ERROR)

    # Reject blank/whitespace strings explicitly: Decimal('') would raise, but
    # the message should be about the value, not a parsing detail.
    if isinstance(raw, str) and not raw.strip():
        raise ValueError(_MARGIN_ERROR)

    try:
        value = Decimal(str(raw).strip())
    except (InvalidOperation, ValueError):
        raise ValueError(_MARGIN_ERROR)

    if not value.is_finite():
        raise ValueError(_MARGIN_ERROR)
    if value < MIN_TARGET_MARGIN_PERCENT or value > MAX_TARGET_MARGIN_PERCENT:
        raise ValueError(_MARGIN_ERROR)
    if value != value.quantize(Decimal('0.01')):
        raise ValueError(
            'Enter a percentage with at most {} decimal places.'.format(
                TARGET_MARGIN_PERCENT_DECIMAL_PLACES
            )
        )

    return value


def get_default_target_margin_percent():
    """Return the configured margin, falling back to 15 when unusable."""
    stored = (
        AppSetting.objects
        .filter(key=DEFAULT_TARGET_MARGIN_PERCENT_KEY)
        .values_list('value', flat=True)
        .first()
    )
    if stored is None:
        return DEFAULT_TARGET_MARGIN_PERCENT
    try:
        return normalize_target_margin_percent(stored)
    except ValueError:
        # A corrupt row (for example hand-edited in the DB) must not break
        # pricing; the documented fallback is the fresh-install default.
        return DEFAULT_TARGET_MARGIN_PERCENT


def set_default_target_margin_percent(raw):
    """Validate and persist *raw*, returning the stored Decimal value."""
    value = normalize_target_margin_percent(raw)
    AppSetting.objects.update_or_create(
        key=DEFAULT_TARGET_MARGIN_PERCENT_KEY,
        defaults={'value': _canonical(value)},
    )
    return value
