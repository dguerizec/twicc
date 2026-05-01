"""Make UsageSnapshot.provider non-nullable and switch the index to a composite one."""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0075_usagesnapshot_provider_nullable"),
    ]

    operations = [
        migrations.AlterField(
            model_name="usagesnapshot",
            name="provider",
            field=models.CharField(max_length=50),
        ),
        migrations.RemoveIndex(
            model_name="usagesnapshot",
            name="idx_usage_snapshot_fetched",
        ),
        migrations.AddIndex(
            model_name="usagesnapshot",
            index=models.Index(
                fields=["provider", "-fetched_at"],
                name="idx_usage_snap_prov_fetch",
            ),
        ),
    ]
