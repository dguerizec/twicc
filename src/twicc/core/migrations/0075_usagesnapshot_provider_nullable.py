"""Add nullable provider field to UsageSnapshot and backfill existing rows.

The field is added as nullable here so existing rows can be backfilled
with 'claude_code' (the only provider so far). A follow-up migration
makes the field non-nullable.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0074_drop_usage_per_model_and_other_quotas"),
    ]

    operations = [
        migrations.AddField(
            model_name="usagesnapshot",
            name="provider",
            field=models.CharField(max_length=50, null=True),
        ),
        migrations.RunSQL(
            sql="UPDATE core_usagesnapshot SET provider = 'claude_code' WHERE provider IS NULL",
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
