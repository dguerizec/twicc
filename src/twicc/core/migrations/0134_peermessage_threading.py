import django.db.models.deletion
from django.db import migrations, models


def backfill_thread_ids(apps, schema_editor):
    PeerMessage = apps.get_model("core", "PeerMessage")
    PeerMessage.objects.update(thread_id=models.F("message_id"))


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0133_share_created_by_session"),
    ]

    operations = [
        migrations.AddField(
            model_name="peermessage",
            name="reply_to",
            field=models.CharField(blank=True, default="", max_length=40),
        ),
        migrations.AddField(
            model_name="peermessage",
            name="reply_to_message",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="replies",
                to="core.peermessage",
            ),
        ),
        migrations.AddField(
            model_name="peermessage",
            name="thread_id",
            field=models.CharField(default="", max_length=40),
        ),
        migrations.RunPython(backfill_thread_ids, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="peermessage",
            name="thread_id",
            field=models.CharField(max_length=40),
        ),
        migrations.AddIndex(
            model_name="peermessage",
            index=models.Index(fields=["peer", "thread_id"], name="idx_peermessage_peer_thread"),
        ),
    ]
