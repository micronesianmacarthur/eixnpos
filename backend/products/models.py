from django.db import models
from suppliers.models import Supplier


class Category(models.Model):
    name = models.CharField(max_length=100, unique=True)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']
        verbose_name_plural = 'categories'

    def __str__(self):
        return self.name


class Product(models.Model):
    name = models.CharField(max_length=200)
    category = models.ForeignKey('Category', null=True, blank=True, on_delete=models.SET_NULL, related_name='products')
    sku = models.CharField(max_length=64, unique=True, blank=True, null=True)
    description = models.TextField(blank=True)
    price = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    cost = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    markup_margin = models.DecimalField(max_digits=5, decimal_places=2, default=10)
    min_stock = models.IntegerField(default=0)
    non_stock = models.BooleanField(default=False)
    supplier = models.ForeignKey(Supplier, null=True, blank=True, on_delete=models.SET_NULL, related_name='products')
    active = models.BooleanField(default=True)
    # --- Pack mapping (Option A) -------------------------------------------
    # A repack converts stock between two separate flat Products. The bulk/package
    # SKU (the *parent*) stores the mapping: ``unit_product`` is the individual-unit
    # SKU it converts to, and ``units_per_pack`` is the whole-unit ratio. Both fields
    # are either set together or both null (enforced by a CheckConstraint below, plus
    # serializer validation). The FK points at the parent's *own* unit product, so
    # ``child.pack_products`` (the ``related_name``) is the set of *pack parents* that
    # reference the child — the naming is counter-intuitive but follows the FK.
    unit_product = models.ForeignKey(
        'self', null=True, blank=True, on_delete=models.PROTECT,
        related_name='pack_products',
        help_text='The individual-unit SKU this pack converts to.',
    )
    units_per_pack = models.PositiveIntegerField(
        null=True, blank=True,
        help_text='Whole child units produced by one parent unit.',
    )

    # Materialized on-hand column. This is the single source of truth that the
    # stock writers lock and update with ``F('quantity') + delta`` inside the
    # same transaction as the ledger insert. The ``quantity >= 0`` CHECK makes
    # a negative balance a database error even if an application guard is
    # bypassed or races a concurrent writer. Backfilled from the ledger in a
    # data migration; ``StockMovement`` keeps it in sync (see
    # ``inventory.models.StockMovement.save/delete``).
    quantity = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['sku']
        constraints = [
            models.CheckConstraint(
                condition=models.Q(quantity__gte=0),
                name='product_quantity_non_negative',
            ),
            # Pack mapping: both fields null (not a pack) OR both set with a valid
            # whole-unit ratio in 2..1,000,000 (a ratio of 1 is a no-op and is
            # rejected). Half-configured pairs are rejected at the DB level too.
            models.CheckConstraint(
                condition=(
                    models.Q(unit_product__isnull=True, units_per_pack__isnull=True)
                    | models.Q(unit_product__isnull=False,
                               units_per_pack__gte=2, units_per_pack__lte=1_000_000)
                ),
                name='product_pack_mapping_valid',
            ),
        ]

    def _generate_sku(self):
        name = self.category.name if self.category is not None else ''
        first = ''
        for char in name:
            if char.isalpha():
                first = char.lower()
                break
        if not first:
            first = 'p'
        consonants = []
        for char in name.lower():
            if char.isalpha() and char not in 'aeiou' and char != first:
                consonants.append(char)
                if len(consonants) == 2:
                    break
        while len(consonants) < 2:
            consonants.append('x')
        return f'{first}{"".join(consonants)}-{self.id:04d}'

    def save(self, *args, **kwargs):
        created = self._state.adding
        super().save(*args, **kwargs)
        if created and not self.sku and self.category_id is not None:
            self.sku = self._generate_sku()
            super().save(update_fields=['sku'])

    def __str__(self):
        return self.name

    def is_low_stock(self):
        if self.non_stock:
            return False
        return self.quantity <= self.min_stock
