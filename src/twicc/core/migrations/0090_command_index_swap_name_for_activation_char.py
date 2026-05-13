"""Swap ``name`` for ``activation_char`` in the ``Command`` lookup index.

The previous lookup index ``(provider, project, name)`` carried
``name`` for historical reasons: it was inherited from the
``SlashCommand`` table where the column was the tail of a
``UniqueConstraint(project, name)``. That constraint was already
gone (migrations 0052 → 0053), and no current query filters by
``name`` — the column only appears in an ``ORDER BY name`` on the
listing endpoint, which would not benefit from the index anyway
because the OR on ``project_id`` breaks the prefix.

The listing endpoint now also filters by ``activation_char`` (the
backing column for the per-provider invocation prefix, e.g. ``/``
for Claude Code or ``$`` for Codex). Placing it at the position
``name`` used to occupy makes the index a clean leftmost match for
the actual query ``WHERE provider=? AND activation_char=? AND
project_id …``, while keeping ``provider`` alone as a valid prefix
for the sync task's full scan.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0089_delete_slashcommand"),
    ]

    operations = [
        migrations.RemoveIndex(
            model_name="command",
            name="idx_command_prov_proj_name",
        ),
        migrations.AddIndex(
            model_name="command",
            index=models.Index(
                fields=["provider", "activation_char", "project"],
                name="idx_command_prov_act_proj",
            ),
        ),
    ]
