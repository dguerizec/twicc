"""Add an ``is_builtin`` flag to ``Command``.

Driven by Codex's ``skills/list`` exposing ``System`` and ``Admin``
scopes — skills shipped with the runtime, semantically the same as
"built-ins" that the picker tags as such. Claude Code currently keeps
its built-ins in a frontend constant (``BUILTIN_COMMANDS`` in
``frontend/src/providers/claude_code/helpers.js``), so every existing
``Command`` row stays at the default ``False`` and the picker tag
keeps the same wiring (``cmd.is_builtin`` already drives the
``(built-in)`` label).
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0091_drop_command_source"),
    ]

    operations = [
        migrations.AddField(
            model_name="command",
            name="is_builtin",
            field=models.BooleanField(default=False),
        ),
    ]
