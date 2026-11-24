# Add idempotency/source/trace fields to Payment.
#
# idempotency_key is added NULLABLE and NOT unique here; a separate migration
# (0007) backfills existing rows with a UUID before asserting uniqueness, so
# adding the unique constraint against a non-empty, actively-written table is
# safe.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('sales', '0005_sale_session'),
    ]

    operations = [
        migrations.AddField(
            model_name='payment',
            name='correlation_id',
            field=models.CharField(blank=True, max_length=64, null=True),
        ),
        migrations.AddField(
            model_name='payment',
            name='idempotency_key',
            field=models.CharField(blank=True, max_length=64, null=True),
        ),
        migrations.AddField(
            model_name='payment',
            name='source',
            field=models.CharField(choices=[('ui', 'UI set_payment'), ('service', 'TransactionService.complete_sale'), ('complete_flow', 'complete endpoint fallback'), ('reconciliation', 'Reconciliation/import')], default='ui', max_length=24),
        ),
        migrations.AddField(
            model_name='payment',
            name='transaction_id',
            field=models.CharField(blank=True, max_length=64, null=True),
        ),
    ]
