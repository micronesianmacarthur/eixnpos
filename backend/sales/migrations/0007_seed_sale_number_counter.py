# Seed the sale-number counter from existing sales so previously issued
# numbers are never re-used by the locked counter after the switch away from
# the racy MAX(number)+1 allocation in Sale.save().

from django.db import migrations


def seed_counter(apps, schema_editor):
    Sale = apps.get_model('sales', 'Sale')
    Counter = apps.get_model('sales', 'SaleNumberCounter')
    max_number = Sale.objects.order_by('-number').values_list('number', flat=True).first()
    Counter.objects.create(pk=1, current=max_number or 0)


def unseed_counter(apps, schema_editor):
    Counter = apps.get_model('sales', 'SaleNumberCounter')
    Counter.objects.filter(pk=1).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('sales', '0006_salenumbercounter'),
    ]

    operations = [
        migrations.RunPython(seed_counter, unseed_counter),
    ]
