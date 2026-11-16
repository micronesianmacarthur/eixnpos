"""Backfill: force a password change for any user still holding the default.

Blocker B1 fix (see t_d9b72443): enforcement of the forced-password-change flow
was keyed only on ``User.must_change_password``, never on the stored password
value. A database upgraded or repaired by a path that did not set the flag can
therefore keep a known credential (the seeded default ``admin``) on an account
whose ``must_change_password=False``. This data migration flags every user whose
password still matches the reserved/default value, so login forces the change.

The database is small enough that ``check_password`` per row is acceptable; it
unambiguously detects the live hash without needing the plaintext.
"""

from django.contrib.auth.hashers import check_password as check_reserved_hash
from django.db import migrations

#: Canonical reserved value the seeded admin account is created with. Kept in
#: sync with ``accounts.validators.RESERVED_VALUES``; hard-coded here so the
#: migration stays immutable against future edits to that module.
RESERVED_DEFAULTS = ('admin',)


def backfill_must_change_password(apps, schema_editor):
    User = apps.get_model('accounts', 'User')
    # Iterate the stored hashes (never plaintext): check_password(tgt, enc)
    # returns True when tgt hashes to the stored value. ``user.password`` is a
    # plain column exposed on the historical model, so this needs no model
    # methods and works reliably inside a data migration when the app model
    # has been abstracted away by the migration state.
    for user in User.objects.iterator():
        if any(
            check_reserved_hash(reserved, user.password)
            for reserved in RESERVED_DEFAULTS
        ):
            user.must_change_password = True
            user.save(update_fields=['must_change_password'])


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0002_alter_user_options_user_updated_at'),
    ]

    operations = [
        migrations.RunPython(
            backfill_must_change_password,
            migrations.RunPython.noop,
        ),
    ]
