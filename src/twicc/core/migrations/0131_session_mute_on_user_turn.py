from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0130_project_icon"),
    ]

    operations = [
        migrations.AddField(
            model_name="session",
            name="mute_on_user_turn",
            field=models.BooleanField(default=False),
        ),
    ]
