from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0122_project_default_browser_url")]

    operations = [
        migrations.AddField(
            model_name="session",
            name="browser_url",
            field=models.CharField(blank=True, default=None, max_length=2000, null=True),
        ),
    ]
