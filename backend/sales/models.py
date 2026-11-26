"""
Transaction locking invariants for the sales module.

Sale numbers
------------
``Sale.number`` is allocated from a single, monotonic ``SaleNumberSequence``
row (id=1, a high-water mark). That row is the SOLE authority for sale
numbers: no caller ever computes a number from the ``sales_sale`` table, so
deleting the largest-numbered sale can never free a number for reuse.

Critical invariant: **a transaction may never hold the sale-row lock and then
allocate (or advance) a sale number.** The sequence row has its own lock
(``select_for_update`` on id=1), and any code path that would take both locks
in the same transaction creates a deadlock cycle against ``Sale.save()``'s
number allocation. Number allocation therefore happens in its own
``transaction.atomic()`` that locks ONLY the sequence row, and never runs
inside a block that already holds a sale-row lock.

Missing-row self-heal: if the sequence row is absent (test DB flushes,
runtime deletion, manual loss) it is (re)created from ``MAX(number)`` under a
row lock with an ``IntegrityError`` retry inside the same atomic, so the next
allocation starts strictly above all existing numbers instead of colliding
with number 1.

Manual numbers: a sale created with an explicit ``number`` (admin/import/seed)
advances the high-water mark to at least that number, so a later auto-allocated
sale can never reuse a hand-assigned number.

Sale-row locking
----------------
Every mutating lifecycle transition (complete, finalize, void, refund) opens a
``transaction.atomic()``, re-fetches the persisted row with
``select_for_update``, and validates + mutates against that LOCKED instance —
never a possibly-stale in-memory copy. ``select_for_update`` is a no-op on
SQLite (writers serialize); on PostgreSQL it is a real row lock.

Lock order (when more than one row is locked): the sale row FIRST, then
payment/line/customer rows, then product rows in ascending id. The sequence
row is never held together with a sale row (see above).

External I/O (receipt/PDF generation, backups, gateway-style payment-network
calls) must stay OUTSIDE the atomic blocks. Lock scope is as small as
correctness allows.

New sale mutation checklist:
1. open transaction.atomic
2. re-fetch the persisted row with select_for_update
3. validate against the locked instance
4. never allocate a sale number while holding a sale-row lock
5. don't do I/O inside the transaction
"""
from django.db import models, transaction, IntegrityError
from django.contrib.auth import get_user_model
from django.utils import timezone
from decimal import Decimal
import uuid

User = get_user_model()

# Maximum bounded-retry attempts for allocating a unique sale number.
NUMBER_ALLOCATION_ATTEMPTS = 10


def _max_sale_number() -> int:
    """Max ``Sale.number`` currently persisted, or 0 for an empty table.

    The seed used when the ``SaleNumberSequence`` singleton row is missing and
    must be recreated: the new high-water mark must start strictly above every
    existing number so the next allocation never collides with number 1.
    """
    return Sale.objects.aggregate(max_number=models.Max('number'))['max_number'] or 0


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

        Missing-row self-heal: if the singleton row is absent (a test DB
        flush, runtime deletion, or manual loss), it is (re)created from the
        max existing ``Sale.number`` under the row lock — never from 0 — so
        the first allocation after a loss starts strictly above all existing
        numbers instead of colliding with number 1. A concurrent first INSERT
        of the singleton raises IntegrityError; we catch it and retry the
        locked read of the row the winner committed.
        """
        for _ in range(NUMBER_ALLOCATION_ATTEMPTS):
            try:
                with transaction.atomic():
                    seq, _created = (
                        SaleNumberSequence.objects.select_for_update().get_or_create(
                            pk=1,
                            defaults={'last_value': _max_sale_number()},
                        )
                    )
                    seq.last_value += 1
                    seq.save(update_fields=['last_value'])
                    return seq.last_value
            except IntegrityError:
                # Concurrent writer beat us to the increment (or to the first
                # INSERT after a row loss); re-read the committed row.
                continue
        raise SaleNumberError('Could not allocate a unique sale number after retry')

    def advance_to(self, n: int) -> None:
        """Raise the high-water mark to at least ``n``.

        Used when a sale is created with an explicit/manual ``number`` (seed
        data, admin or import), so a later auto-allocation can never hand out a
        number <= a hand-assigned one. ``select_for_update`` is a no-op on
        SQLite; a real row lock on PostgreSQL. Like :meth:`allocate_next`, this
        self-heals a missing singleton row from ``MAX(number)`` and retries on a
        concurrent first INSERT.
        """
        for _ in range(NUMBER_ALLOCATION_ATTEMPTS):
            try:
                with transaction.atomic():
                    seq, _created = (
                        SaleNumberSequence.objects.select_for_update().get_or_create(
                            pk=1,
                            defaults={'last_value': max(_max_sale_number(), n)},
                        )
                    )
                    if seq.last_value < n:
                        seq.last_value = n
                        seq.save(update_fields=['last_value'])
                    return
            except IntegrityError:
                continue
        raise SaleNumberError('Could not raise the sale-number high-water mark after retry')


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
        # Re-read the matched rows under a row lock immediately before the
        # delete (atomic conditional delete semantics), so a concurrent
        # completion cannot flip a just-delted row to immutable between the
        # guard check above and the actual DELETE. (``select_for_update`` is a
        # no-op on SQLite, which serializes writers anyway; on PostgreSQL it
        # takes a real row lock.)
        with transaction.atomic():
            locked = self.select_for_update()
            if locked.filter(~models.Q(status__in=EDITABLE_SALE_STATUSES)).exists():
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
        # A brand-new row created with an explicit/manual number must advance
        # the sequence high-water mark so a later auto-allocation never reuses
        # the hand-assigned number. (Auto-allocated numbers go through
        # allocate_next and leave the mark where it should be; only the
        # explicit-number path needs the extra bump.)
        advance_for_manual = bool(self.number) and self._state.adding
        if not self.number:
            self.number = self._allocate_number()
        self._guard_immutable_save()
        try:
            super().save(*args, **kwargs)
        finally:
            # A sanctioned transition consumes its one-shot flag on save.
            self._transition_pending = False
        # after the row is persisted, not before, so a failed INSERT never
        # advances the mark (the number stays unused and NO-REUSE holds).
        if advance_for_manual:
            SaleNumberSequence().advance_to(self.number)

    def _column_name(self, field_name):
        """The actual DB column for a field (FKs use ``<name>_id``)."""
        return self._meta.get_field(field_name).attname

    #: Fields a sanctioned ``transition_to`` may (re)write on an immutable
    #: sale. ``_transition_pending`` suppresses ``_guard_immutable_save`` only
    #: for these, so a pending transition can never smuggle an identity or a
    #: different transition's audit rewrite past the guard on the same save.
    TRANSITION_SCOPE_FIELDS = frozenset({
        'status',
        'completed_at',
        'voided_by', 'voided_at', 'void_reason',
        'refunded_by', 'refunded_at', 'refund_reason',
    })

    def _guard_immutable_save(self):
        """Refuse to mutate an already-immutable sale via plain ``save()``.

        Only enforced when the **persisted** row has left the open/pending
        state, so the database (not a possibly-stale in-memory status) is the
        source of truth. Unless a sanctioned ``transition_to`` is in flight
        (``_transition_pending``), any protected identity/audit field whose new
        value differs from the persisted row raises ``ImmutableSaleError``.
        Callers never need to remember to call ``check_inviolate_fields()``.

        ``_transition_pending`` is restricted to the single sanctioned save
        (and its own scope fields); it never exempts identity fields or audit
        fields belonging to a *different* transition.
        """
        if self._state.adding:
            return  # brand-new row; nothing persisted to protect.
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
        if getattr(self, '_transition_pending', False):
            # A status transition may only rewrite status + its own audit
            # fields; any other protected (identity / other-audit) change is
            # still refused even mid-transition.
            changed = [f for f in changed if f not in self.TRANSITION_SCOPE_FIELDS]
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

        The guard re-reads ``status`` from the database under a row lock and
        inside an atomic block (a point-in-time source of truth), rather than
        trusting the in-memory instance, so a stale object loaded while the
        sale was open cannot later erase it after the row was completed through
        another path — and a concurrent completion between the re-read and the
        DELETE cannot slip through (``select_for_update`` is a no-op on SQLite,
        which serializes writers; it is a real row lock on PostgreSQL).
        """
        with transaction.atomic():
            try:
                current = type(self).objects.select_for_update().get(pk=self.pk)
            except type(self).DoesNotExist:
                # Row is already gone; nothing to protect or delete.
                return (0, {})
            if current.status not in EDITABLE_SALE_STATUSES:
                raise ImmutableSaleError(
                    f'Cannot hard-delete sale #{current.number}: status is '
                    f'{current.status} (only open/pending sales may be deleted).'
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

        The whitelist is keyed on the **persisted** row's status (re-read from
        the DB in the caller's transaction, so ``select_for_update``/atomic
        framing around the caller applies), not a possibly-stale in-memory
        ``self.status``: a stale open instance whose row has since been
        completed cannot authorize an illegal completed->voided write. (An
        object whose own ``transition_to`` already ran but hasn't saved yet —
        ``_transition_pending`` — whitelists from its in-memory status, since
        the DB legitimately lags a sanctioned in-flight transition.)

        Allowed transitions:
            pending/open  -> completed | voided
            completed     -> refunded
            voided/refunded are terminal.
        """
        # The whitelist is keyed on the *authoritative* current status: the
        # persisted row's status (re-read in the caller's transaction, so
        # select_for_update/atomic framing around the caller applies) — not a
        # possibly-stale in-memory ``self.status``. A stale open instance whose
        # row has since been completed cannot authorize an illegal
        # completed->voided write. The one exception is an object that is
        # *mid*-transition (transition_to -> save is the unit of work): its
        # in-memory status was set by a sanctioned transition not yet persisted,
        # and the DB legitimately lags it, so we whitelist from in-memory state.
        if getattr(self, '_transition_pending', False):
            base_status = self.status
        else:
            base_status = self.status
            current = (
                type(self).objects.filter(pk=self.pk)
                .values_list('status', flat=True)
                .first()
            )
            if current is not None:
                base_status = current
        allowed = self._allowed_transitions().get(base_status, set())
        if new_status not in allowed:
            raise ImmutableSaleError(
                f'Illegal status transition {base_status} -> {new_status}.'
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
        from django.db import transaction as db_transaction
        with db_transaction.atomic():
            # Serialize cart recomputation on this sale: lock the sale row before
            # reading lines/writing totals so two concurrent cart edits cannot
            # lose each other's line_total / sale-total updates.
            locked = Sale.objects.select_for_update().get(pk=self.pk)
            subtotal = Decimal('0')
            discount_total = Decimal('0')
            for line in locked.lines.all():
                line.line_total = (line.unit_price * line.quantity) - line.discount_amount
                line.save(update_fields=['line_total'])
                # Update in-memory object to reflect saved value
                line.refresh_from_db(fields=['line_total'])
                subtotal += line.unit_price * line.quantity
                discount_total += line.discount_amount

            locked.subtotal = subtotal
            locked.discount_total = discount_total
            taxable_amount = subtotal - discount_total
            locked.tax_total = (taxable_amount * tax_rate).quantize(Decimal('0.01'))
            locked.total = taxable_amount + locked.tax_total
            locked.save(update_fields=['subtotal', 'discount_total', 'tax_total', 'total'])

        # The recomputation happened on the locked `locked` instance, not on
        # `self`; refresh `self` so callers reading `self.total` / subtotal /
        # tax_total after recompute() see the persisted values rather than the
        # stale pre-lock snapshot.
        if self.pk:
            self.refresh_from_db(
                fields=['subtotal', 'discount_total', 'tax_total', 'total']
            )

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
    # Snapshot of the product's cost at sale time, captured when the line is
    # created so later edits to Product.cost never restate historical COGS.
    # NULL for pre-migration rows (no reliable historical source).
    unit_cost = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    discount_type = models.CharField(max_length=16, choices=DISCOUNT_CHOICES, default=DISCOUNT_NONE)
    discount_value = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    discount_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    discount_authorizer = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='authorized_discounts')
    line_total = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    def save(self, *args, **kwargs):
        # Snapshot the product's cost at the moment the line is created so
        # later edits to Product.cost never restate historical COGS. Living in
        # the model's save() (not a call site) guarantees every creation path —
        # cart/transaction services, the admin form, imports, direct .create()
        # — captures it. Only on FIRST insert: an explicit unit_cost is
        # respected and updates never rewrite an existing snapshot.
        if self._state.adding and self.unit_cost is None:
            self.unit_cost = self.product.cost
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.product} x{self.quantity}'


class Payment(models.Model):
    METHOD_CASH = 'cash'
    METHOD_CARD = 'card'
    METHOD_MOBILE = 'mobile'
    METHOD_SPLIT = 'split'
    METHOD_INVOICE = 'invoice'
    METHOD_CHOICES = [
        (METHOD_CASH, 'Cash'),
        (METHOD_CARD, 'Card'),
        (METHOD_MOBILE, 'Mobile'),
        (METHOD_SPLIT, 'Split'),
        (METHOD_INVOICE, 'Invoice'),
    ]

    # Capacity/origin of each payment row: how the write happened, so cash-up
    # and auditors can gauge the trust level of each recorded payment.
    SOURCE_UI = 'ui'
    SOURCE_SERVICE = 'service'
    SOURCE_COMPLETE_FLOW = 'complete_flow'
    SOURCE_RECONCILIATION = 'reconciliation'
    SOURCE_CHOICES = [
        (SOURCE_UI, 'UI set_payment'),
        (SOURCE_SERVICE, 'TransactionService.complete_sale'),
        (SOURCE_COMPLETE_FLOW, 'complete endpoint fallback'),
        (SOURCE_RECONCILIATION, 'Reconciliation/import'),
    ]

    sale = models.ForeignKey(Sale, on_delete=models.CASCADE, related_name='payments')
    method = models.CharField(max_length=16, choices=METHOD_CHOICES)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    tendered = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    change_due = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    manager_approved = models.BooleanField(default=False)
    # True when this invoice Payment was admitted by the role-based policy
    # predicate (see sales.policies.can_invoice_on_account) rather than by an
    # explicit manager/admin approval. Distinct from ``manager_approved``:
    # a cashier invoice allowed by policy is policy_authorized=True while a
    # manager/admin-invoice (session or PIN) is manager_approved=True with
    # policy_authorized left False. Separating the two keeps the audit truthful
    # so ``manager_approved`` never silently marks a by-policy (unapproved)
    # invoice as an approved one.
    policy_authorized = models.BooleanField(default=False)
    # Idempotency: a stable key uniquely ties a Payment row to one logical
    # tender action so a retried/duplicated request cannot double-record. The
    # client supplies this key for a retryable tender; server-derived keys are
    # no longer generated (they were guessable and enable key-squatting). A
    # keyless write gets a random UUID via the default, so admin/shell/import
    # rows are never NULL and always carry a unique (non-replayable) key.
    # editable=False hides it from the admin add/edit form so provenance keys
    # cannot be hand-typed.
    idempotency_key = models.CharField(max_length=64, unique=True, default=uuid.uuid4, editable=False)
    source = models.CharField(max_length=24, choices=SOURCE_CHOICES, default=SOURCE_UI)
    transaction_id = models.CharField(max_length=64, null=True, blank=True)
    # Groups the per-method rows of one split payment into a single logical
    # tender (each split component is one top-level Payment row).
    correlation_id = models.CharField(max_length=64, null=True, blank=True)
    # Neutralisation fields: a payment that has been voided (or whose full
    # amount has been refunded) no longer counts as a captured sale payment.
    # A void does not delete the row — it keeps the audit history while the
    # flag stops the amount from contributing to balances/paid totals.
    voided = models.BooleanField(
        default=False, db_index=True,
        help_text='Payment has been voided (neutralized). It no longer counts '
                  'as a captured sale payment.',
    )
    voided_at = models.DateTimeField(
        null=True, blank=True,
        help_text='Timestamp the payment was voided.',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def remaining_refundable(self) -> Decimal:
        """Money still refundable against this captured payment.

        The amount that was captured minus the cumulative refunds already
        booked against it. Zero once fully refunded.
        """
        refunded = self.refunds.aggregate(
            total=models.Sum('amount')
        )['total'] or Decimal('0')
        return max(Decimal('0'), self.amount - refunded)

    def is_fully_refunded(self) -> bool:
        return self.remaining_refundable() <= Decimal('0')


class Refund(models.Model):
    """Authoritative record of a (partial) refund and its reversing entries.

    One row per refunded line item (or per line/payment segment when a single
    line's refund spans multiple captured payments). Writing a Refund row is
    the bookkeeping entry for: the tender returned, the AR/customer-balance
    credit (for invoice payments), and the inventory restock movement.
    """

    sale = models.ForeignKey(
        Sale, on_delete=models.CASCADE, related_name='refunds',
        help_text='The sale the refund applies to.',
    )
    line = models.ForeignKey(
        SaleLine, on_delete=models.CASCADE, related_name='refunds',
        help_text='The specific sale line being refunded.',
    )
    payment = models.ForeignKey(
        Payment, on_delete=models.CASCADE, related_name='refunds',
        help_text='The captured payment this refund reverses (or a portion of).',
    )
    quantity = models.IntegerField(
        default=0,
        help_text='Units of the line returned. <= line.quantity.',
    )
    amount = models.DecimalField(
        max_digits=12, decimal_places=2,
        help_text='Refunded money. > 0, <= remaining unrefunded capacity of '
                  'the line and of the payment.',
    )
    method = models.CharField(
        max_length=16,
        choices=[(m, n) for m, n in Payment.METHOD_CHOICES],
        help_text='Tender method this refund returns. For cash this is the '
                  'tendered cash being returned; for card/invoice it is the '
                  'method being reversed.',
    )
    reason = models.TextField(blank=True)
    manager_approved = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['sale', 'created_at']),
            models.Index(fields=['payment', 'created_at']),
        ]

    def __str__(self):
        return f'Refund {self.amount} on {self.sale}'

