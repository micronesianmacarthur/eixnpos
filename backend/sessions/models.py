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


def resolve_open_session(cashier, at=None):
    """The open Session a sale by ``cashier`` should be attributed to.

    Resolves the cashier's *default* register (their first accessible register,
    ordered by pk — the same rule the session ``create`` and ``current`` views
    use when no register is given) and the business-local date of ``at``
    (defaults to now), then returns the open session for that register/day.

    Returns ``None`` when there is no open session on the cashier's default
    register/day. We deliberately do NOT fall back to some other accessible
    register that happens to have an open session: the sale HTTP path carries
    no register, so silently attaching to a sibling-register session would
    misattribute revenue. In that case the sale stays ``session=NULL`` and the
    legacy close/summary time-window rule reports it, matching the absence of
    server-side session enforcement on this line.
    """
    from django.utils import timezone as dj_timezone
    at = at or dj_timezone.now()
    day = local_date_of(at)

    default = None
    for register in Register.objects.order_by('id'):
        if register.cashiers.exists() and not register.cashiers.filter(pk=cashier.pk).exists():
            continue
        default = register
        break
    if default is None:
        return None

    return Session.objects.filter(
        register=default, business_day=day, status=Session.STATUS_OPEN,
    ).order_by('-opened_at').first()


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
    # Both fields are non-null: legacy sessions were backfilled in migration
    # ``0004``, and every API-created session resolves a register server-side
    # (a caller with no accessible register gets a 400), so no row may be
    # unscoped.
    register = models.ForeignKey(
        Register, on_delete=models.PROTECT, related_name='sessions',
    )
    business_day = models.DateField()

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
