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
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['sku']

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

    @property
    def quantity(self):
        if self.non_stock:
            return 0
        from inventory.models import StockMovement
        total = 0
        for m in StockMovement.objects.filter(product=self):
            total += m.quantity
        return total

    def is_low_stock(self):
        if self.non_stock:
            return False
        return self.quantity <= self.min_stock
