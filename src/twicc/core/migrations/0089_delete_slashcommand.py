"""Drop the legacy ``SlashCommand`` model.

Migration 0088 introduced the replacement ``Command`` model and
seeded it from the original table; every reader and writer has been
switched over (views, sync task), so the original table is no
longer referenced anywhere and can be removed.
"""

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0088_add_command"),
    ]

    operations = [
        migrations.DeleteModel(
            name="SlashCommand",
        ),
    ]
