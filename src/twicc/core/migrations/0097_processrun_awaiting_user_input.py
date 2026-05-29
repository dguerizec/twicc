"""Add ``awaiting_user_input`` to :class:`ProcessRun`.

Tracks whether the agent is currently blocked on a user click (tool
approval, AskUserQuestion, Codex approval) — orthogonal to the runtime
``state`` because the SDKs keep the agent in ``ASSISTANT_TURN`` while
waiting for the user's decision. Default is ``False``; no backfill is
needed (existing rows are necessarily ``DEAD``, and a dead agent does
not await anything).
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0096_processrun_twicc_pid_agent_pid"),
    ]

    operations = [
        migrations.AddField(
            model_name="processrun",
            name="awaiting_user_input",
            field=models.BooleanField(default=False),
        ),
    ]
