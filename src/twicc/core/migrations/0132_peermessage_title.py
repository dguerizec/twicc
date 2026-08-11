from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0131_peer_peermessage"),
    ]

    operations = [
        migrations.AddField(
            model_name="peermessage",
            name="title",
            field=models.CharField(blank=True, default="", max_length=100),
        ),
    ]
