"""Create the ``Command`` model as a copy of ``SlashCommand`` with an
activation-character column, and seed it from the existing rows.

``Command`` is the multi-provider-friendly replacement for
``SlashCommand``: not every provider uses ``/`` as the only invocation
prefix (e.g. Codex also exposes ``$``), so the prefix needs to live
next to the name instead of being implied by the model. The legacy
``SlashCommand`` table stays in place for now; subsequent migrations
will move readers and writers over and eventually drop it.

The data copy keeps every existing row reachable through the new
table by stamping ``'/'`` into ``activation_char`` — that matches the
single prefix every legacy row was implicitly using.

The matching ``reverse_sql`` replays the copy in the opposite
direction (only ``activation_char = '/'`` rows, since SlashCommand
cannot represent any other prefix) so that ``migrate core 0087``
restores the data instead of losing it.
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0087_alter_session_provider"),
    ]

    operations = [
        migrations.CreateModel(
            name="Command",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("provider", models.CharField(max_length=50)),
                ("name", models.CharField(max_length=200)),
                ("activation_char", models.CharField(max_length=1)),
                (
                    "source",
                    models.CharField(
                        choices=[
                            ("commands_dir", "Commands directory"),
                            ("skills_dir", "Skills directory"),
                            ("plugin", "Plugin"),
                        ],
                        max_length=20,
                    ),
                ),
                ("plugin_name", models.CharField(blank=True, max_length=100, null=True)),
                ("description", models.TextField()),
                ("argument_hint", models.CharField(blank=True, max_length=200, null=True)),
                (
                    "project",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="commands",
                        to="core.project",
                    ),
                ),
            ],
            options={
                "indexes": [
                    models.Index(
                        fields=["provider", "project", "name"],
                        name="idx_command_prov_proj_name",
                    ),
                ],
            },
        ),
        migrations.RunSQL(
            sql=(
                "INSERT INTO core_command "
                "(provider, project_id, name, activation_char, source, plugin_name, description, argument_hint) "
                "SELECT provider, project_id, name, '/', source, plugin_name, description, argument_hint "
                "FROM core_slashcommand"
            ),
            # On rollback ``core_slashcommand`` has just been recreated empty by
            # the reverse of migration 0089. Wipe it defensively (in case a
            # manual INSERT slipped in between the two reverse steps) and
            # replay the data flow in the other direction: every ``Command``
            # row that was using the ``/`` prefix maps cleanly back onto a
            # ``SlashCommand`` row. Rows with any other ``activation_char``
            # were created after 0088 and intentionally drop on the way out —
            # ``SlashCommand`` has nowhere to store the prefix and would
            # collapse them onto plain names anyway. The reverse of the
            # surrounding ``CreateModel`` then drops ``core_command``.
            reverse_sql=[
                "DELETE FROM core_slashcommand",
                (
                    "INSERT INTO core_slashcommand "
                    "(provider, project_id, name, source, plugin_name, description, argument_hint) "
                    "SELECT provider, project_id, name, source, plugin_name, description, argument_hint "
                    "FROM core_command "
                    "WHERE activation_char = '/'"
                ),
            ],
        ),
    ]
