"""Drop the ``source`` column from ``Command``.

The field carried the filesystem origin (``commands_dir`` / ``skills_dir``
/ ``plugin``) of each Claude Code command, but no query ever filtered on
it and the frontend only used it to pick between the ``command`` and
``skill`` labels in the picker tag — a low-signal distinction that
doesn't transpose to Codex's skill catalogue. Removing it simplifies the
schema before the Codex sync starts writing into the same table.

The companion frontend change replaces the legacy ``source=='builtin'``
sentinel (used for Claude CLI built-ins) with a frontend-only
``is_builtin`` flag on those hardcoded entries, since those rows never
reach the database anyway.
"""

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0090_command_index_swap_name_for_activation_char"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="command",
            name="source",
        ),
    ]
