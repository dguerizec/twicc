"""Shared diff/apply helper used by every provider's command-sync task.

Each provider has its own way of discovering commands — Claude Code
scans the filesystem, Codex queries the app-server's ``skills/list``
JSON-RPC, and future providers may have their own protocol. What they
all share is the final step: reconcile the discovered catalogue against
the ``Command`` table for ``(provider, activation_char)`` and emit
create / update / delete in bulk.

This module owns that reconciliation. Caller provides a flat ``desired``
dict — one entry per command they want present — and gets a stats dict
back. Nothing here talks to disk or to a backend SDK.
"""

from __future__ import annotations

from typing import Any

from twicc.core.models import Command

# Columns the helper diffs and bulk-updates. ``provider``,
# ``activation_char``, ``project_id`` and ``name`` form the natural key
# (caller has already split them into the ``desired`` dict's key
# tuple), so they're not in this list.
_COMPARE_FIELDS = ("plugin_name", "description", "argument_hint", "is_builtin", "is_workflow")


def apply_desired_commands(
    *,
    provider: str,
    activation_char: str,
    desired: dict[tuple[str | None, str], dict[str, Any]],
) -> dict[str, int]:
    """Reconcile ``Command`` rows for ``(provider, activation_char)`` against ``desired``.

    Args:
        provider: Provider key (``Provider.<X>.value``). The diff is
            strictly scoped to this provider so each provider's sync
            only ever touches its own subset of rows.
        activation_char: Invocation prefix (e.g. ``'/'`` for Claude
            Code, ``'$'`` for Codex skills). Scoped on the same axis
            so two prefixes within the same provider don't trample
            each other.
        desired: Map ``(project_id_or_None, name) -> fields`` where
            ``fields`` carries every column outside the natural key:
            ``plugin_name``, ``description``, ``argument_hint``,
            ``is_builtin``, ``is_workflow``. Missing keys land as
            ``None`` / ``False``.

    Returns:
        ``{"created": int, "updated": int, "deleted": int, "unchanged": int}``.
    """
    # Snapshot of what's currently in the table for this scope. Keyed
    # the same way as ``desired`` so the diff is a straight set
    # comparison.
    existing: dict[tuple[str | None, str], Command] = {}
    for obj in Command.objects.filter(provider=provider, activation_char=activation_char):
        existing[(obj.project_id, obj.name)] = obj

    stats = {"created": 0, "updated": 0, "deleted": 0, "unchanged": 0}

    # --- Delete rows that no longer match a desired entry ---
    to_delete_ids: list[int] = []
    for key, obj in existing.items():
        if key not in desired:
            to_delete_ids.append(obj.pk)
            stats["deleted"] += 1
    if to_delete_ids:
        Command.objects.filter(pk__in=to_delete_ids).delete()

    # --- Create or update the rest ---
    to_create: list[Command] = []
    to_update: list[Command] = []

    for key, fields in desired.items():
        project_id, name = key
        obj = existing.get(key)

        if obj is None:
            to_create.append(Command(
                provider=provider,
                activation_char=activation_char,
                project_id=project_id,
                name=name,
                **fields,
            ))
            stats["created"] += 1
            continue

        changed = False
        for field_name in _COMPARE_FIELDS:
            if getattr(obj, field_name) != fields.get(field_name):
                changed = True
                setattr(obj, field_name, fields.get(field_name))
        if changed:
            to_update.append(obj)
            stats["updated"] += 1
        else:
            stats["unchanged"] += 1

    if to_create:
        Command.objects.bulk_create(to_create)
    if to_update:
        Command.objects.bulk_update(to_update, _COMPARE_FIELDS)

    return stats
