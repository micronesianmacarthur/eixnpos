# Add ZReportSnapshot: an immutable Z-report figure snapshot captured per
# session at close time, so GET /zreport/ can re-render the filed PDF from
# stored inputs instead of recomputing from live (possibly later-edited) data.
#
# One snapshot per session (unique OneToOne on Session.id), keyed so exactly
# one filed report exists per closed shift.

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('pos_sessions', '0005_session_register_and_business_day_not_null'),
    ]

    operations = [
        migrations.CreateModel(
            name='ZReportSnapshot',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('summary_data', models.JSONField(help_text='JSON-serialized compute_session_summary output plus store_name, captured atomically at close.')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('session', models.OneToOneField(help_text='The session this report was filed for at close.', on_delete=django.db.models.deletion.CASCADE, related_name='zreport_snapshot', to='pos_sessions.session')),
            ],
        ),
    ]
