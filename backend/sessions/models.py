from django.contrib.auth import get_user_model
from django.db import models
from django.db.models import Q, UniqueConstraint
from django.utils import timezone
from zoneinfo import ZoneInfo

User = get_user_model()


# ---------------------------------------------------------------------------
# Business-day helpers
#
# A session is scoped to a *business day*: the store's local wall-clock
# calendar date (in the business's configured IANA timezone) on which the
# shift was opened. The day is captured once, at creation, and never rewritten.
# ---------------------------------------------------------------------------

def business_tzinfo():
    """The IANA timezone a register's business day is counted in."""
    try:
        from business.models import BusinessInfo
        tz_name = (getattr(BusinessInfo.get(), 'timezone', '') or '').strip() or 'UTC'
    except Exception:
        tz_name = 'UTC'
    try:
        return ZoneInfo(tz_name)
    except Exception:
        return ZoneInfo('UTC')


def local_date_of(dt):
    """Map a datetime to the business-local calendar date.

    Tolerates naive datetimes (assumed to be in the business timezone) and
    aware ones (converted into the business timezone).
    """
    if dt is None:
        dt = timezone.now()
    if dt.tzinfo is None or dt.utcoffset() is None:
        dt = dt.replace(tzinfo=business_tzinfo())
    return timezone.localtime(dt, business_tzinfo()).date()


class Register(models.Model):
    """A physical POS station / cash drawer.

    ``cashiers`` lists who may use the register; an **empty** M2M (the v1
    default) grants access to every authenticated cashier.
    """
    name = models.CharField(max_length=100)
    identifier = models.CharField(
        max_length=40, unique=True, blank=True, null=True, default=None,
        help_text='Optional terminal/posix slug for the hardware station; unique when set.',
    )
    business = models.ForeignKey(
        'business.BusinessInfo',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='registers',
    )
    cashiers = models.ManyToManyField(
        User, blank=True, related_name='register_access'
    )

    def __str__(self):
        return self.name


class Session(models.Model):
    STATUS_OPEN = 'open'
    STATUS_CLOSED = 'closed'
    STATUS_CHOICES = [(STATUS_OPEN,'Open'),(STATUS_CLOSED,'Closed')]
    cashier = models.ForeignKey(User, on_delete=models.PROTECT, related_name='sessions')
    starting_cash = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_OPEN)
    opened_at = models.DateTimeField(auto_now_add=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    cash_counts = models.JSONField(default=dict, blank=True)
    cash_removed = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    expected_cash = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    counted_cash = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    variance = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    sales_total = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    notes = models.TextField(blank=True, default='')
    # Scope: the open shift is shared by the *register/day*, not by a single
    # cashier (``cashier`` is kept as audit-only — who opened the shift).
    register = models.ForeignKey(
        Register, on_delete=models.PROTECT, related_name='sessions',
        null=True, blank=True,
    )
    business_day = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ['-opened_at']
        constraints = [
            # One open shift per (register, business day) — atomic at the DB
            # layer so two concurrent opens on the same register/day cannot
            # both succeed. There is no global single-open-session rule.
            UniqueConstraint(
                fields=['register', 'business_day'],
                condition=Q(status='open'),
                name='only_one_open_session_per_register_per_day',
            ),
        ]

    def __str__(self):
        return f'Session #{self.id}'
