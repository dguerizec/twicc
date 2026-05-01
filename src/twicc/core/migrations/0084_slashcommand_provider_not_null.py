"""Make SlashCommand.provider NOT NULL and swap to a provider-aware lookup index.

The pre-existing ``(project, name)`` lookup index is replaced by
``(provider, project, name)`` so the per-provider sync task and the
per-project lookup endpoint both hit a leftmost-prefix match. The
table is small (one row per command) so the index swap is essentially
free.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0083_slashcommand_provider_nullable"),
    ]

    operations = [
        migrations.AlterField(
            model_name="slashcommand",
            name="provider",
            field=models.CharField(max_length=50),
        ),
        migrations.RemoveIndex(
            model_name="slashcommand",
            name="idx_slash_command_project_name",
        ),
        migrations.AddIndex(
            model_name="slashcommand",
            index=models.Index(
                fields=["provider", "project", "name"],
                name="idx_slash_prov_proj_name",
            ),
        ),
    ]
