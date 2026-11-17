# Make Session.register and Session.business_day non-nullable.
#
# Migration 0004 backfilled every legacy session with a register and a
# business day, and every API-created session already resolves a register
# server-side (a caller with no accessible register gets a 400), so no row
# carries a NULL scope. Turning the fields non-null also removes the
# partial-unique-index NULLs-are-distinct loophole: two open sessions with no
# scope can no longer coexist, because both columns are required.

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('pos_sessions', '0004_backfill_register_and_business_day'),
    ]

    operations = [
        migrations.AlterField(
            model_name='session',
            name='business_day',
            field=models.DateField(),
        ),
        migrations.AlterField(
            model_name='session',
            name='register',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='sessions', to='pos_sessions.register'),
        ),
    ]
