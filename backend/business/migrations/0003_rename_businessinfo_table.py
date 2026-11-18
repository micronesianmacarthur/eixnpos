"""Align migration state with the renamed BusinessInfo table.

The production/dev database had its BusinessInfo table renamed out-of-band
from Django's default ``business_businessinfo`` to ``business_info``. This
migration records that rename in migration state and makes the database change
idempotent, so it works both on databases that were already renamed and on
fresh/older databases that still have the default table name.
"""

from django.db import migrations

OLD_TABLE = 'business_businessinfo'
NEW_TABLE = 'business_info'


def rename_to_business_info(apps, schema_editor):
    tables = set(schema_editor.connection.introspection.table_names())
    if OLD_TABLE in tables and NEW_TABLE not in tables:
        schema_editor.execute(
            'ALTER TABLE {} RENAME TO {}'.format(
                schema_editor.quote_name(OLD_TABLE),
                schema_editor.quote_name(NEW_TABLE),
            )
        )
    # NEW_TABLE already present: the rename was done manually, nothing to do.


def rename_to_default(apps, schema_editor):
    tables = set(schema_editor.connection.introspection.table_names())
    if NEW_TABLE in tables and OLD_TABLE not in tables:
        schema_editor.execute(
            'ALTER TABLE {} RENAME TO {}'.format(
                schema_editor.quote_name(NEW_TABLE),
                schema_editor.quote_name(OLD_TABLE),
            )
        )


class Migration(migrations.Migration):

    dependencies = [
        ('business', '0002_businessinfo_backup_path'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AlterModelTable(
                    name='businessinfo',
                    table='business_info',
                ),
            ],
            database_operations=[
                migrations.RunPython(
                    rename_to_business_info,
                    reverse_code=rename_to_default,
                ),
            ],
        ),
    ]
