"""Backfill auto-generated colors for existing projects that have none.

One-shot: fills ``Project.color`` for every non-worktree project that is
missing a color, using the same deterministic hash as the creation path. Git
worktrees are left untouched — they inherit their main repository's color.
User-chosen colors are never overwritten (only NULL/empty rows are touched).

The color logic is DUPLICATED below on purpose. A migration is a frozen
historical record replayed on fresh databases long after it was written, so it
must never import live app code (``twicc.project_color``): that code can be
renamed, removed, or change behavior, which would either break the replay or
silently make this migration produce a different result than it did the day it
ran. The canonical implementation lives in ``twicc.project_color``.
"""

import hashlib
import os

from django.db import migrations, models


def _final_segment(directory):
    if not directory:
        return ""
    return os.path.basename(directory.rstrip("/"))


def _color_for_project(name, directory):
    """Frozen copy of ``twicc.project_color.color_for_project`` (S=65, L=58)."""
    label = (name or "").strip() or _final_segment(directory)
    if not label:
        return None
    digest = hashlib.sha256(label.encode("utf-8")).digest()
    hue = int.from_bytes(digest[:4], "big") % 360
    sat, light = 0.65, 0.58
    a = sat * min(light, 1 - light)

    def channel(n):
        k = (n + hue / 30) % 12
        return round(255 * (light - a * max(-1, min(k - 3, 9 - k, 1))))

    return f"#{channel(0):02x}{channel(8):02x}{channel(4):02x}"


def backfill_project_colors(apps, schema_editor):
    Project = apps.get_model("core", "Project")
    qs = Project.objects.filter(worktree_of__isnull=True).filter(
        models.Q(color__isnull=True) | models.Q(color="")
    )
    to_update = []
    for project in qs.only("id", "name", "directory", "color"):
        color = _color_for_project(project.name, project.directory)
        if color is None:
            continue
        project.color = color
        to_update.append(project)
    if to_update:
        Project.objects.bulk_update(to_update, ["color"], batch_size=500)


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0128_usagesnapshot_extra_usage_currency_and_more"),
    ]

    operations = [
        migrations.RunPython(backfill_project_colors, migrations.RunPython.noop),
    ]
