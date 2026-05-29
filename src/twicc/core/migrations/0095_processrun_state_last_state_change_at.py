"""Add ``state`` and ``last_state_change_at`` to :class:`ProcessRun`.

Existing rows are stale at boot anyway (no in-process agent owns them yet),
so they are marked ``DEAD`` with ``last_state_change_at = started_at``. The
runtime model default for new rows is ``STARTING`` — declared on the field
itself; the migration-time ``default`` exists only to satisfy SQLite's NOT
NULL constraint on the freshly-added ``state`` column and is overwritten by
the ``RunSQL`` backfill that follows.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0094_usagesnapshot_extra_usage_remaining_credits"),
    ]

    operations = [
        migrations.AddField(
            model_name="processrun",
            name="state",
            field=models.CharField(
                choices=[
                    ("starting", "Starting"),
                    ("assistant_turn", "Assistant Turn"),
                    ("user_turn", "User Turn"),
                    ("dead", "Dead"),
                ],
                default="starting",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="processrun",
            name="last_state_change_at",
            field=models.DateTimeField(null=True),
        ),
        migrations.RunSQL(
            sql="UPDATE core_processrun SET state = 'dead', last_state_change_at = started_at;",
            reverse_sql=migrations.RunSQL.noop,
        ),
        migrations.AlterField(
            model_name="processrun",
            name="last_state_change_at",
            field=models.DateTimeField(),
        ),
    ]
