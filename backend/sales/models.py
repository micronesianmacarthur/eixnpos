from django.db import models, transaction, IntegrityError
from django.contrib.auth import get_user_model
from django.utils import timezone
from decimal import Decimal

User = get_user_model()

# Maximum bounded-retry attempts for allocating a unique sale number.
NUMBER_ALLOCATION_ATTEMPTS = 10


class SaleNumberError(Exception):
    """Raised when a unique sale number cannot be allocated after retry."""


class ImmutableSaleError(Exception):
    """Raised when a completed/corrected sale is mutated or hard-deleted.

    Completed, voided and refunded sales are immutable records: they cannot be
    hard-deleted nor have their identity fields (number, customer, cashier,
    session) changed through any ORM path. Status changes must go through the
    controlled ``transition_to`` whitelist rather than direct field writes.
    """


class SaleNumberSequence(models.Model):
    """Single-row monotonic high-water mark for ``Sale.number``.

    Keeps the "last allocated number" independent of the ``sales_sale`` table,
    so deleting the max-numbered sale never frees that number for reuse, and
    (together with the DB ``unique`` constraint on ``Sale.number`` and the
    bounded retry in ``Sale.save``) guarantees unique numbers even under
    concurrent creates.

    The row must have id=1; the data migration seeds it to the max existing
    sale number (0 for an empty table).
    """

    id = models.PositiveIntegerField(primary_key=True)  # forced to 1
    last_value = models.PositiveBigIntegerField(default=0)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(id=1),
                name='single_sale_number_sequence_row',
            ),
            models.CheckConstraint(
                condition=models.Q(last_value__gte=0),
                name='sale_number_sequence_non_negative',
            ),
        ]

    def allocate_next(self) -> int:
        """Atomically increment and return the next sale number.

        Locked read+write on the single sequence row so concurrent allocators
        observe a strictly increasing value. (``select_for_update`` is a no-op
        on SQLite, which serializes writes anyway; on PostgreSQL it takes a
        real row lock.)
        """
        for _ in range(NUMBER_ALLOCATION_ATTEMPTS):
            try:
                with transaction.atomic():
                    try:
                        seq = SaleNumberSequence.objects.select_for_update().get(pk=1)
                    except SaleNumberSequence.DoesNotExist:
                        seq = SaleNumberSequence.objects.create(pk=1, last_value=0)
                    seq.last_value += 1
                    seq.save(update_fields=['last_value'])
                    return seq.last_value
            except IntegrityError:
                # Concurrent writer beat us to the increment; retry.
                continue
        raise SaleNumberError('Could not allocate a unique sale number after retry')


#: Statuses that mark a sale as still editable / hard-deletable. Anything
#: outside this pair (completed, voided, refunded) is an immutable accounting
#: record that must not be erased or have its identity/audit fields rewritten.
EDITABLE_SALE_STATUSES = frozenset({'pending', 'open'})


class SaleQuerySet(models.QuerySet):
    """QuerySet that refuses to hard-delete non-open sales.

    ``Model.delete()`` only guards the single-instance path; a bulk
    ``QuerySet.delete()`` (e.g. ``Sale.objects.filter(...).delete()``, the
    Django admin "delete selected" action, or the collector fast-path) bypasses
    it. Overriding ``delete()`` here fails closed: if the queryset matches any
    sale that has left the open/pending state, the whole delete is refused with
    ``ImmutableSaleError`` instead of silently erasing rows (and orphaning
    StockMovement references, since those have no FK to ``Sale``).
    """

    def delete(self):
        immutable = self.filter(~models.Q(status__in=EDITABLE_SALE_STATUSES))
        if immutable.exists():
            raise ImmutableSaleError(
                'Cannot hard-delete sale(s) with immutable status '
                '(completed/voided/refunded) via a queryset; void or refund '
                'them instead.'
            )
        return super().delete()


class Sale(models.Model):
    STATUS_PENDING = 'pending'
    STATUS_OPEN = 'open'
    STATUS_COMPLETED = 'completed'
    STATUS_VOIDED = 'voided'
    STATUS_REFUNDED = 'refunded'
    STATUS_CHOICES = [
        (STATUS_PENDING, 'Pending'),
        (STATUS_OPEN, 'Open'),
        (STATUS_COMPLETED, 'Completed'),
        (STATUS_VOIDED, 'Voided'),
        (STATUS_REFUNDED, 'Refunded'),
    ]

    number = models.PositiveIntegerField(unique=True, editable=False)
    cashier = models.ForeignKey(User, on_delete=models.PROTECT, related_name='sales')
    # The POS session (register/day shift) this sale belongs to. Set
    # server-side at sale creation from the cashier's open session; NULL only
    # when there is no open session for the cashier's register/business day
    # (server-side session enforcement is absent on this line). Close/summary/
    # Z-report totals attribute a sale through this link, falling back to the
    # legacy time-window rule only for rows where it is NULL (pre-migration
    # history).
    session = models.ForeignKey(
        'pos_sessions.Session', related_name='sales',
        null=True, blank=True, on_delete=models.PROTECT,
    )
    customer = models.ForeignKey('customers.Customer', null=True, blank=True, on_delete=models.SET_NULL, related_name='sales')
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_OPEN)
    subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    discount_total = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    tax_total = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    refunded_at = models.DateTimeField(null=True, blank=True)
    # Audit trail for voiding. voided_by / voided_at / void_reason are written
    # by the service layer in the same atomic update that flips the sale to
    # voided; they are immutable once set, so a client can never fabricate an
    # audit record after the fact. refunded_by mirrors refunded_at for symmetry.
    voided_by = models.ForeignKey(
        User, related_name='voided_sales',
        null=True, blank=True, on_delete=models.PROTECT,
    )
    voided_at = models.DateTimeField(null=True, blank=True)
    void_reason = models.TextField(blank=True)
    refunded_by = models.ForeignKey(
        User, related_name='refunded_sales',
        null=True, blank=True, on_delete=models.PROTECT,
    )
    # Operator-supplied reason for a refund, persisted on the sale (not just in
    # the StockMovement.note / reversal rows). Written atomically with the
    # completed->refunded transition so the audit record never loses the why.
    refund_reason = models.TextField(blank=True)
    note = models.TextField(blank=True)
    needs_review = models.BooleanField(default=False)

    class Meta:
        ordering = ['-created_at']
        constraints = [
            models.CheckConstraint(
                condition=models.Q(voided_at__isnull=True) | models.Q(status='voided'),
                name='voided_at_implies_voided_status',
            ),
            models.CheckConstraint(
                condition=models.Q(refunded_at__isnull=True) | models.Q(status='refunded'),
                name='refunded_at_implies_refunded_status',
            ),
            # Reverse direction of the voided audit pair: once a sale is voided
            # it must carry an audit timestamp (never a bare status flip).
            models.CheckConstraint(
                condition=~models.Q(status='voided', voided_at__isnull=True),
                name='voided_status_requires_voided_at',
            ),
        ]

    # Fields that (once a sale leaves open/pending) may never change through any
    # write path: they form the sale's immutable identity.
    INVIOLATE_FIELDS = frozenset({
        'number', 'cashier', 'customer', 'session',
        'subtotal', 'discount_total', 'tax_total', 'total',
    })

    # Identity + audit fields protected once a sale is immutable. This is the
    # superset of INVIOLATE_FIELDS plus ``status`` and the void/refund/completion
    # audit trail, which must be written only by ``transition_to``.
    PROTECTED_FIELDS = frozenset({
        *INVIOLATE_FIELDS,
        'status',
        'voided_by', 'voided_at', 'void_reason',
        'refunded_by', 'refunded_at', 'refund_reason',
        'completed_at',
    })

    #: The default manager now uses :class:`SaleQuerySet`, so bulk deletes fail
    #: closed on immutable sales just like the single-instance path.
    objects = SaleQuerySet.as_manager()

    def save(self, *args, **kwargs):
        if not self.number:
            self.number = self._allocate_number()
        self._guard_immutable_save()
        try:
            super().save(*args, **kwargs)
        finally:
            # A sanctioned transition consumes its one-shot flag on save.
            self._transition_pending = False

    def _column_name(self, field_name):
        """The actual DB column for a field (FKs use ``<name>_id``)."""
        return self._meta.get_field(field_name).attname

    def _guard_immutable_save(self):
        """Refuse to mutate an already-immutable sale via plain ``save()``.

        Only enforced when the **persisted** row has left the open/pending
        state, so the database (not a possibly-stale in-memory status) is the
        source of truth. Unless a sanctioned ``transition_to`` is in flight
        (``_transition_pending``), any protected identity/audit field whose new
        value differs from the persisted row raises ``ImmutableSaleError``.
        Callers never need to remember to call ``check_inviolate_fields()``.
        """
        if self._state.adding:
            return  # brand-new row; nothing persisted to protect.
        if getattr(self, '_transition_pending', False):
            return  # sanctioned status transition; transition_to is the writer.
        persisted = (
            Sale.objects.filter(pk=self.pk)
            .values(*[self._column_name(f) for f in self.PROTECTED_FIELDS])
            .first()
        )
        if persisted is None:
            return  # row already gone; the delete guard owns this path.
        if persisted.get('status') in EDITABLE_SALE_STATUSES:
            return  # open/pending rows remain fully editable.
        changed = sorted(
            f for f in self.PROTECTED_FIELDS
            if getattr(self, self._column_name(f)) != persisted.get(self._column_name(f))
        )
        if changed:
            raise ImmutableSaleError(
                f'Cannot change field(s) on a completed sale: {", ".join(changed)}.'
            )

    def _allocate_number(self) -> int:
        """Allocate a unique, never-reused sale number.

        Uses the single-row SaleNumberSequence high-water mark; never frees a
        number on deletion. Number is immutable once assigned to a sale.
        """
        return SaleNumberSequence().allocate_next()

    def is_immutable(self) -> bool:
        """A sale is immutable once it leaves the open/pending state."""
        return not self.is_open()

    def delete(self, *args, **kwargs):
        """Refuse to hard-delete a completed, voided, or refunded sale.

        Completed/corrected sales are immutable accounting records. Only
        open/pending (draft) sales may be hard-deleted; a completed sale must
        be voided/refunded (which records an audit trail), never erased.

        The guard re-reads ``status`` from the database (a point-in-time
        source of truth) rather than trusting the in-memory instance, so a
        stale object loaded while the sale was open cannot later erase it after
        the row was completed through another path.
        """
        current = type(self).objects.filter(pk=self.pk).values_list('status', flat=True).first()
        if current is None:
            # Row is already gone; nothing to protect or delete.
            return (0, {})
        if current not in EDITABLE_SALE_STATUSES:
            raise ImmutableSaleError(
                f'Cannot hard-delete sale #{self.number}: status is '
                f'{current} (only open/pending sales may be deleted).'
            )
        return super().delete(*args, **kwargs)

    def check_inviolate_fields(self, payload):
        """Raise if *payload* would mutate an inviolate field on this sale.

        *payload* is a mapping of candidate field names. Only enforced once the
        sale is immutable (status outside open/pending).
        """
        if not self.is_immutable():
            return
        touched = sorted(self.INVIOLATE_FIELDS.intersection(payload.keys()))
        if touched:
            raise ImmutableSaleError(
                f'Cannot change field(s) on a completed sale: {", ".join(touched)}.'
            )

    def transition_to(
        self,
        new_status: str,
        *,
        by: 'User' = None,
        reason: str = '',
    ) -> 'Sale':
        """Transition the sale via the controlled status whitelist.

        This is the ONLY sanctioned way to change ``status`` once a sale is
        immutable, and it records the corresponding audit fields atomically in
        the caller's transaction.

        Allowed transitions:
            pending/open  -> completed | voided
            completed     -> refunded
            voided/refunded are terminal.
        """
        allowed = self._allowed_transitions().get(self.status, set())
        if new_status not in allowed:
            raise ImmutableSaleError(
                f'Illegal status transition {self.status} -> {new_status}.'
            )
        self.status = new_status
        # Mark this instance as mid-sanctioned-transition so the immutability
        # guard in ``save()`` permits the status + audit-field write that this
        # whitelist produces (the flag is consumed on the next ``save()``).
        self._transition_pending = True
        now = timezone.now()
        if new_status == self.STATUS_COMPLETED:
            self.completed_at = now
        elif new_status == self.STATUS_VOIDED:
            self.voided_at = now
            self.voided_by = by
            self.void_reason = reason
        elif new_status == self.STATUS_REFUNDED:
            self.refunded_at = now
            self.refunded_by = by
            self.refund_reason = reason
        return self

    @classmethod
    def _allowed_transitions(cls):
        return {
            cls.STATUS_PENDING: {cls.STATUS_COMPLETED, cls.STATUS_VOIDED},
            cls.STATUS_OPEN: {cls.STATUS_COMPLETED, cls.STATUS_VOIDED},
            cls.STATUS_COMPLETED: {cls.STATUS_REFUNDED},
            cls.STATUS_VOIDED: set(),
            cls.STATUS_REFUNDED: set(),
        }

    def recompute(self, tax_rate: Decimal = Decimal('0')):
        """Recalculate sale totals from line items.
        
        Args:
            tax_rate: Tax rate as a decimal (e.g., Decimal('0.10') for 10%)
        """
        subtotal = Decimal('0')
        discount_total = Decimal('0')
        for line in self.lines.all():
            line.line_total = (line.unit_price * line.quantity) - line.discount_amount
            line.save(update_fields=['line_total'])
            # Update in-memory object to reflect saved value
            line.refresh_from_db(fields=['line_total'])
            subtotal += line.unit_price * line.quantity
            discount_total += line.discount_amount
        
        self.subtotal = subtotal
        self.discount_total = discount_total
        taxable_amount = subtotal - discount_total
        self.tax_total = (taxable_amount * tax_rate).quantize(Decimal('0.01'))
        self.total = taxable_amount + self.tax_total
        self.save(update_fields=['subtotal', 'discount_total', 'tax_total', 'total'])

    def is_open(self) -> bool:
        """Check if sale is in a modifiable state."""
        return self.status in (self.STATUS_PENDING, self.STATUS_OPEN)

    def __str__(self):
        return f'Sale #{self.number}'


class SaleLine(models.Model):
    DISCOUNT_NONE = 'none'
    DISCOUNT_PERCENT = 'percent'
    DISCOUNT_FIXED = 'fixed'
    DISCOUNT_CHOICES = [
        (DISCOUNT_NONE, 'None'),
        (DISCOUNT_PERCENT, 'Percent'),
        (DISCOUNT_FIXED, 'Fixed'),
    ]

    sale = models.ForeignKey(Sale, on_delete=models.CASCADE, related_name='lines')
    product = models.ForeignKey('products.Product', on_delete=models.PROTECT)
    quantity = models.IntegerField(default=1)
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)
    discount_type = models.CharField(max_length=16, choices=DISCOUNT_CHOICES, default=DISCOUNT_NONE)
    discount_value = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    discount_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    discount_authorizer = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='authorized_discounts')
    line_total = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    def __str__(self):
        return f'{self.product} x{self.quantity}'


class Payment(models.Model):
    METHOD_CASH = 'cash'
    METHOD_CARD = 'card'
    METHOD_SPLIT = 'split'
    METHOD_INVOICE = 'invoice'
    METHOD_CHOICES = [
        (METHOD_CASH, 'Cash'),
        (METHOD_CARD, 'Card'),
        (METHOD_SPLIT, 'Split'),
        (METHOD_INVOICE, 'Invoice'),
    ]

    sale = models.ForeignKey(Sale, on_delete=models.CASCADE, related_name='payments')
    method = models.CharField(max_length=16, choices=METHOD_CHOICES)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    tendered = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    change_due = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    manager_approved = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
