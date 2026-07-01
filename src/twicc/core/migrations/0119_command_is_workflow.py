"""Add an ``is_workflow`` flag to ``Command``.

Saved Claude Code workflows (``.js`` files under ``.claude/workflows/`` —
user-level, project-level, or shipped by a plugin) are discovered like
commands and skills and land in this table. This flag distinguishes them
so the picker can tag them ``(workflow)`` (a plugin-provided workflow keeps
its ``plugin_name`` on top of this flag). Only Claude Code discovers
workflows today, so every Codex row stays at the default ``False``.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0118_workflow_phases_cost"),
    ]

    operations = [
        migrations.AddField(
            model_name="command",
            name="is_workflow",
            field=models.BooleanField(default=False),
        ),
    ]
