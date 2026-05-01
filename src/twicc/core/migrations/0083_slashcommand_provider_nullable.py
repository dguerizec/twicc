"""Add nullable provider field to SlashCommand and backfill existing rows.

The field is added as nullable here so the existing rows can be
backfilled with ``'claude_code'`` (the only provider that has been
populating slash commands so far). A follow-up migration tightens it
to NOT NULL and swaps the lookup index for a provider-aware variant.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0082_modelprice_provider_not_null"),
    ]

    operations = [
        migrations.AddField(
            model_name="slashcommand",
            name="provider",
            field=models.CharField(max_length=50, null=True),
        ),
        migrations.RunSQL(
            sql="UPDATE core_slashcommand SET provider = 'claude_code' WHERE provider IS NULL",
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
