"""Make ProcessRun.provider and SessionCron.provider non-nullable now that all rows have been backfilled."""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0079_processrun_sessioncron_provider_nullable"),
    ]

    operations = [
        migrations.AlterField(
            model_name="processrun",
            name="provider",
            field=models.CharField(max_length=50),
        ),
        migrations.AlterField(
            model_name="sessioncron",
            name="provider",
            field=models.CharField(max_length=50),
        ),
    ]
