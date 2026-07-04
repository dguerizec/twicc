from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0121_session_plan_paths")]

    operations = [
        migrations.AddField(
            model_name="project",
            name="default_browser_url",
            field=models.CharField(blank=True, default=None, max_length=2000, null=True),
        ),
    ]
