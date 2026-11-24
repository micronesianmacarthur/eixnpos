# Give every existing Payment row a unique idempotency_key (UUID4) before
# asserting the unique constraint. Runs after 0006 adds the (nullable) column;
# new rows created by the refactored set_payment/complete_sale flows write
# real keys going forward.

import uuid

from django.db import migrations, models


def backfill_idempotency_keys(apps, schema_editor):
    Payment = apps.get_model('sales', 'Payment')
    for payment in Payment.objects.filter(idempotency_key__isnull=True).iterator(chunk_size=500):
        payment.idempotency_key = str(uuid.uuid4())
        payment.save(update_fields=['idempotency_key'])


class Migration(migrations.Migration):

    dependencies = [
        ('sales', '0006_payment_idempotency_fields'),
    ]

    operations = [
        migrations.RunPython(backfill_idempotency_keys, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='payment',
            name='idempotency_key',
            field=models.CharField(blank=True, max_length=64, null=True, unique=True),
        ),
    ]