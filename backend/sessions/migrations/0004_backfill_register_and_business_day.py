# Seed a default register and backfill legacy sessions with register/scoped day.
#
# Before register/day scoping, sessions had no register and no business day, and
# the global single-open-session rule applied. This data migration:
#   1. ensures at least one Register exists (seeding "Register 1") and
#   2. assigns every session without a register to it, capturing the business
#      day (store-local calendar date) from the session's open instant — the
#      same rule new sessions use, so history stays consistent.

import django.utils.timezone as tzmodule
from django.db import migrations
from zoneinfo import ZoneInfo


def seed_register_and_backfill(apps, schema_editor):
    Register = apps.get_model('pos_sessions', 'Register')
    Session = apps.get_model('pos_sessions', 'Session')
    BusinessInfo = apps.get_model('business', 'BusinessInfo')

    register = Register.objects.order_by('id').first()
    if register is None:
        register = Register.objects.create(name='Register 1')

    tz_name = 'UTC'
    try:
        tz_name = (BusinessInfo.objects.order_by('id').first().timezone
                   or 'UTC')
    except Exception:
        tz_name = 'UTC'
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = ZoneInfo('UTC')

    for session in Session.objects.filter(register__isnull=True):
        opened = session.opened_at or tzmodule.now()
        if getattr(opened, 'utcoffset', None) is None or opened.utcoffset() is None:
            opened = opened.replace(tzinfo=tz)
        session.register = register
        session.business_day = tzmodule.localtime(opened, tz).date()
        session.save(update_fields=['register', 'business_day'])


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('pos_sessions', '0003_register_remove_session_only_one_open_session_and_more'),
    ]

    operations = [
        migrations.RunPython(seed_register_and_backfill, noop),
    ]
