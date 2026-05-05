"""Make Session.file_path non-nullable and unique.

One JSONL file maps to exactly one session, so the path is unique. The
implicit index also supports path-based lookups during initial sync and
the file watcher.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0085_session_file_path_nullable"),
    ]

    operations = [
        migrations.AlterField(
            model_name="session",
            name="file_path",
            field=models.CharField(max_length=500, unique=True),
        ),
    ]
