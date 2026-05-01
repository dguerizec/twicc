"""Add nullable provider field to ModelPrice and backfill existing rows.

The field is added as nullable here so existing rows can be backfilled
with ``'claude_code'`` (the only provider that has been syncing prices
so far). A follow-up migration tightens it to NOT NULL and swaps the
existing unique_together / index for provider-aware variants.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0080_processrun_sessioncron_provider_not_null"),
    ]

    operations = [
        migrations.AddField(
            model_name="modelprice",
            name="provider",
            field=models.CharField(max_length=50, null=True),
        ),
        migrations.RunSQL(
            sql="UPDATE core_modelprice SET provider = 'claude_code' WHERE provider IS NULL",
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
