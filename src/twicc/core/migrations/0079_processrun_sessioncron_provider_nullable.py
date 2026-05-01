"""Add nullable provider field to ProcessRun and SessionCron, and backfill rows.

The field is added as nullable here so existing rows can be backfilled
with ``'claude_code'`` (the only provider so far). A follow-up migration
makes the field non-nullable.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0078_periodicactivity_provider_not_null"),
    ]

    operations = [
        migrations.AddField(
            model_name="processrun",
            name="provider",
            field=models.CharField(max_length=50, null=True),
        ),
        migrations.AddField(
            model_name="sessioncron",
            name="provider",
            field=models.CharField(max_length=50, null=True),
        ),
        migrations.RunSQL(
            sql="UPDATE core_processrun SET provider = 'claude_code' WHERE provider IS NULL",
            reverse_sql=migrations.RunSQL.noop,
        ),
        migrations.RunSQL(
            sql="UPDATE core_sessioncron SET provider = 'claude_code' WHERE provider IS NULL",
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
