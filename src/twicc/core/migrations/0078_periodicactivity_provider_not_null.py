"""Make DailyActivity/WeeklyActivity.provider non-nullable and switch the unique constraint.

Now that all rows have been backfilled with ``'claude_code'``, the field
can be made required, the previous ``(project, date)`` unique constraint
is dropped, and a new ``(project, date, provider)`` unique constraint is
added so several providers can coexist on the same project/date.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0077_periodicactivity_provider_nullable"),
    ]

    operations = [
        migrations.AlterField(
            model_name="dailyactivity",
            name="provider",
            field=models.CharField(max_length=50),
        ),
        migrations.AlterField(
            model_name="weeklyactivity",
            name="provider",
            field=models.CharField(max_length=50),
        ),
        migrations.RemoveConstraint(
            model_name="dailyactivity",
            name="unique_project_dailyactivity",
        ),
        migrations.RemoveConstraint(
            model_name="weeklyactivity",
            name="unique_project_weeklyactivity",
        ),
        migrations.AddConstraint(
            model_name="dailyactivity",
            constraint=models.UniqueConstraint(
                fields=["project", "date", "provider"],
                name="uniq_pdp_dailyactivity",
            ),
        ),
        migrations.AddConstraint(
            model_name="weeklyactivity",
            constraint=models.UniqueConstraint(
                fields=["project", "date", "provider"],
                name="uniq_pdp_weeklyactivity",
            ),
        ),
    ]
