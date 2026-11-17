# Add Sale.session — the POS session (register/day shift) a sale belongs to.
#
# Forward-port of the design in wip/pos-v1-backup: ``Sale.session`` is set
# server-side at sale creation to the open session for the cashier's register/
# business day, and close/summary/Z-report totals attribute a sale through this
# link (falling back to the legacy time-window rule only for rows where it is
# NULL). It is nullable here because pre-existing sales and any sale made
# while no session is open carry no link.

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('sales', '0004_sale_needs_review_sale_note'),
        ('pos_sessions', '0004_backfill_register_and_business_day'),
    ]

    operations = [
        migrations.AddField(
            model_name='sale',
            name='session',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='sales', to='pos_sessions.session'),
        ),
    ]
