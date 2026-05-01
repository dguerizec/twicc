"""Add nullable provider field to DailyActivity and WeeklyActivity, and backfill rows.

The field is added as nullable here so existing rows can be backfilled
with ``'claude_code'`` (the only provider so far). A follow-up migration
makes the field non-nullable and switches the unique constraint to
``(project, date, provider)``.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0076_usagesnapshot_provider_not_null"),
    ]

    operations = [
        migrations.AddField(
            model_name="dailyactivity",
            name="provider",
            field=models.CharField(max_length=50, null=True),
        ),
        migrations.AddField(
            model_name="weeklyactivity",
            name="provider",
            field=models.CharField(max_length=50, null=True),
        ),
        migrations.RunSQL(
            sql="UPDATE core_dailyactivity SET provider = 'claude_code' WHERE provider IS NULL",
            reverse_sql=migrations.RunSQL.noop,
        ),
        migrations.RunSQL(
            sql="UPDATE core_weeklyactivity SET provider = 'claude_code' WHERE provider IS NULL",
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
