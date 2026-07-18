from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0129_backfill_project_color"),
    ]

    operations = [
        migrations.AddField(
            model_name="project",
            name="icon",
            field=models.CharField(default="inherit", max_length=100),
        ),
        migrations.AddField(
            model_name="project",
            name="icon_anchor",
            field=models.CharField(blank=True, max_length=500, null=True),
        ),
    ]
