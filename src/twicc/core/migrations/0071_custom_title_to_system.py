"""Merge custom_title kind into system kind.

The CUSTOM_TITLE kind has been removed from the ItemKind enum. Items of
type 'custom-title' are now classified as SYSTEM (display_level stays
DEBUG_ONLY). The custom-title detection for session title updates now
checks both kind=SYSTEM and parsed type='custom-title'.
"""

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0070_session_pin_mode"),
    ]

    operations = [
        migrations.RunSQL(
            sql="UPDATE core_sessionitem SET kind = 'system' WHERE kind = 'custom_title'",
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
