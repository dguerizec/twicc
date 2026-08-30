import django.db.models.deletion
from django.db import migrations, models


def invalidate_existing_active_peers(apps, schema_editor):
    Peer = apps.get_model("core", "Peer")
    Peer.objects.filter(state="active").update(
        state="broken",
        broken_reason="local_address_changed",
        token_ours=None,
        token_theirs=None,
        verification_code="",
        verification_attempts=0,
        verification_regens=0,
        verified_at=None,
        code_confirmed_at=None,
        remote_accepted_at=None,
        reconnect_direction="",
    )


class Migration(migrations.Migration):
    dependencies = [("core", "0134_peermessage_threading")]

    operations = [
        migrations.AddField(
            model_name="peer",
            name="broken_reason",
            field=models.CharField(
                blank=True,
                choices=[
                    ("remote_credential_rejected", "Remote credential rejected"),
                    ("local_address_changed", "Local address changed"),
                    ("local_address_disabled", "Local address disabled"),
                ],
                default="",
                max_length=32,
            ),
        ),
        migrations.AddField(
            model_name="peer",
            name="paired_local_base_url",
            field=models.URLField(blank=True, default="", max_length=500),
        ),
        migrations.AddField(
            model_name="peer",
            name="reconnect_direction",
            field=models.CharField(
                blank=True,
                choices=[("sent", "Sent"), ("received", "Received")],
                default="",
                max_length=8,
            ),
        ),
        migrations.AlterField(
            model_name="peermessage",
            name="peer",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="messages",
                to="core.peer",
            ),
        ),
        migrations.AlterField(
            model_name="peer",
            name="state",
            field=models.CharField(
                choices=[
                    ("pending_sent", "Pending (sent)"),
                    ("pending_received", "Pending (received)"),
                    ("active", "Active"),
                    ("broken", "Broken"),
                    ("revoked", "Revoked"),
                ],
                max_length=20,
            ),
        ),
        migrations.RunPython(invalidate_existing_active_peers, migrations.RunPython.noop),
    ]
