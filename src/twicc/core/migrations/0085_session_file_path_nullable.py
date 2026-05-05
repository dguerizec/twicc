"""Add nullable file_path field to Session and backfill existing rows.

The field is added as nullable here so existing rows can be backfilled
with the path reconstructed from project_id / session_id (claude_code is
the only provider with data so far). A follow-up migration makes the
field non-nullable.

Reconstruction rules for claude_code (relative to ~/.claude/projects/):
  * Top-level session: ``{project_id}/{session_id}.jsonl``
  * Subagent session:  ``{project_id}/{parent_session_id}/subagents/agent-{session_id}.jsonl``
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0084_slashcommand_provider_not_null"),
    ]

    operations = [
        migrations.AddField(
            model_name="session",
            name="file_path",
            field=models.CharField(max_length=500, null=True),
        ),
        migrations.RunSQL(
            sql=(
                "UPDATE core_session "
                "SET file_path = CASE "
                "    WHEN parent_session_id IS NULL "
                "        THEN project_id || '/' || id || '.jsonl' "
                "    ELSE project_id || '/' || parent_session_id "
                "         || '/subagents/agent-' || id || '.jsonl' "
                "END "
                "WHERE provider = 'claude_code' AND file_path IS NULL"
            ),
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
