"""Resync ``is_staff`` from ``role`` in both directions.

``0004_admin_role_to_staff`` mapped every ``role='admin'`` row to
``is_staff=True`` but never cleared a stale staff flag when someone was later
demoted, and it (like the seed paths at the time) only fixed the migration /
seed paths — an admin created through the user API still had ``is_staff=False``
and could not reach ``/admin/``.

This migration makes ``is_staff`` a pure function of ``role`` across every
existing row, in both directions:

* every ``role='admin'`` row becomes ``is_staff=True`` (covers API-created
  admins that 0004/seed never fixed), and
* every non-admin, non-superuser row that is erroneously ``is_staff=True`` is
  cleared to ``False`` (revokes stale demotions).

Superusers are always staff in Django and keep ``is_staff`` regardless of their
``role``, so they are never touched. Going forward the single source of truth
is ``accounts/roles.apply_role_to_staff`` (invoked by every role write path),
so this starts the data layer in sync and runtime writes keep it that way.

The pass is idempotent (a re-apply over already-consistent rows is a no-op).
The reverse is deliberately a no-op: rolling back a plain column resync to the
pre-0005 state is not meaningful — ``0004`` only ever set ``admin -> staff``
and cannot reconstruct which non-admin rows were manually staffed — so the data
is left as-is if the migration is un-applied.
"""

from django.db import migrations


def admin_to_staff(apps, schema_editor):
    User = apps.get_model('accounts', 'User')
    User.objects.filter(role='admin').update(is_staff=True)


def demoted_non_staff(apps, schema_editor):
    User = apps.get_model('accounts', 'User')
    User.objects.filter(is_staff=True, is_superuser=False).exclude(
        role='admin'
    ).update(is_staff=False)


def resync(apps, schema_editor):
    admin_to_staff(apps, schema_editor)
    demoted_non_staff(apps, schema_editor)


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0004_admin_role_to_staff'),
    ]

    operations = [
        migrations.RunPython(
            resync,
            migrations.RunPython.noop,
        ),
    ]
