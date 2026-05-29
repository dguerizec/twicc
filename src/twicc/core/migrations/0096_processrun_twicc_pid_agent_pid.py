"""Add ``twicc_pid`` and ``agent_pid`` to :class:`ProcessRun`.

Both are nullable. Existing rows have no PID information available and stay
``NULL``; only newly created rows (from :meth:`BaseAgentManager._register_and_start`)
populate them. Nothing reads these columns today — they exist so external
tooling can correlate rows with the live TwiCC process (via ``twicc.info.json``)
and with the agent's subprocess.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0095_processrun_state_last_state_change_at"),
    ]

    operations = [
        migrations.AddField(
            model_name="processrun",
            name="twicc_pid",
            field=models.IntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="processrun",
            name="agent_pid",
            field=models.IntegerField(blank=True, null=True),
        ),
    ]
