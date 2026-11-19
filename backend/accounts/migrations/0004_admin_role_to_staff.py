"""Map role='admin' users to Django staff status.

Blocker B2 fix (see t_ddaf975f): the app models admin-tier access with the
custom ``User.role='admin'`` field, but the two surfaces that actually gate
Django backend administration — the stock admin site (``/admin/``) and DRF's
``IsAdminUser`` — check the inherited ``is_staff`` flag. Nothing populated
``is_staff`` from ``role``, so every admin-role user (including the seeded
``admin`` and ``mac`` in the deployed db.sqlite3) had ``is_staff=0`` and was
redirected away from ``/admin/`` and rejected by admin-tier endpoints.

This data migration closes that gap at the data layer: every user with
``role='admin'`` is marked ``is_staff=True`` so the stock admin panel and DRF
admin permissions recognise the role. It sets ``is_staff`` only — deliberately
NOT ``is_superuser``, since superuser grants unrestricted access to every app
module in the admin site, which is broader than the admin role implies. The
pass is idempotent (a re-apply over already-staff admins is a no-op), and the
reverse is a no-op because ``is_staff`` is a plain column edit with no other
repercussion to unwind.
"""

from django.db import migrations


def admin_role_to_staff(apps, schema_editor):
    User = apps.get_model('accounts', 'User')
    User.objects.filter(role='admin').update(is_staff=True)


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0003_backfill_must_change_password'),
    ]

    operations = [
        migrations.RunPython(
            admin_role_to_staff,
            migrations.RunPython.noop,
        ),
    ]
