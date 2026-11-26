""""Backfill Product.quantity from the StockMovement ledger.

After 0005 materialised ``Product.quantity`` (default 0, ``quantity >= 0``
CHECK), this data migration fills the column from the ledger rows that
already exist at migrate time, so a pre-existing database keeps its real
on-hand balance instead of zeroing every product.

Products whose ledger sums negative are clamped to 0: the ``quantity >= 0``
constraint can only be satisfied for a non-negative on-hand, and a negative
net ledger at migration time is corrupt data that the constraint intentionally
rejects going forward.
"""
from django.db import migrations
from django.db.models import Sum


def backfill_product_quantity(apps, schema_editor):
    Product = apps.get_model('products', 'Product')
    StockMovement = apps.get_model('inventory', 'StockMovement')

    for product in Product.objects.all():
        total = (
            StockMovement.objects.filter(product_id=product.pk)
            .aggregate(s=Sum('quantity'))['s']
        ) or 0
        total = max(0, total)
        Product.objects.filter(pk=product.pk).update(quantity=total)


class Migration(migrations.Migration):

    dependencies = [
        ('products', '0005_alter_product_options_product_quantity_and_more'),
        ('inventory', '0005_alter_stockmovement_adjusted_by'),
    ]

    operations = [
        migrations.RunPython(backfill_product_quantity, migrations.RunPython.noop),
    ]
