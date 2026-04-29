"""Make Session.provider non-nullable now that all rows have been backfilled."""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0072_session_provider_nullable"),
    ]

    operations = [
        migrations.AlterField(
            model_name="session",
            name="provider",
            field=models.CharField(max_length=50),
        ),
    ]
