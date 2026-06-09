"""Back orchestration for project trust: ``resolve`` (with seeding) and ``decide``.

Glues the pure resolver (:mod:`twicc.trust`) to the provider-config projection
(:mod:`twicc.providers.claude_code.trust`, :mod:`twicc.providers.codex.trust`)
and the DB. The DB is the source of truth; provider configs are a write-mostly
projection, read once per project at seeding (guarded by ``trust_imported``).
See docs/plans/2026-06-09-project-trust-design.md §5–§6.

Both entry points are async (they do provider IO) and never raise on a provider
write failure — the DB decision stands regardless.
"""

from __future__ import annotations

import logging
import os

from asgiref.sync import sync_to_async

from twicc.trust import TrustResolution, effective_trust, load_trust_rows

logger = logging.getLogger(__name__)


# --- key / git helpers (sync, filesystem) ---------------------------------


def _is_git(directory: str) -> bool:
    from twicc.git import resolve_git_from_path

    return resolve_git_from_path(directory, use_cache=False) is not None


def _codex_root_key(directory: str) -> str:
    """The canonical (realpath) Codex trust key for *directory*.

    = the git root when *directory* is in a repo (the worktree's own root for a
    worktree), else the directory itself.
    """
    from twicc.git import resolve_git_from_path

    resolved = resolve_git_from_path(directory, use_cache=False)
    root = resolved[0] if resolved else directory
    return os.path.realpath(root)


# --- provider IO wrappers -------------------------------------------------


def _claude_write(directory: str, trusted: bool) -> None:
    from twicc.providers.claude_code import trust as claude_trust

    claude_trust.write_trust(directory, trusted)


async def _codex_write(root_key: str, trusted: bool) -> None:
    from twicc.providers.codex import trust as codex_trust

    await codex_trust.write_trust(root_key, trusted)


def _seed_read(directory: str) -> bool | None:
    """Read the project's OWN trust from both providers and collapse it.

    Claude: the exact-path entry. Codex: only when the project is itself its own
    git root (otherwise the ``[projects."<root>"]`` entry belongs to an ancestor
    repo, not this project). No walk-up.

    Collapse: *trusted on either → True*. For untrusted we trust only Codex's
    explicit ``"untrusted"`` (a deliberate decline) — a Claude ``false`` is
    treated as noise (the CLI auto-creates ``false`` entries before any decision)
    and ignored, so we don't import bogus declines for projects the user trusts.
    """
    from twicc.providers.claude_code import trust as claude_trust
    from twicc.providers.codex import trust as codex_trust

    claude = claude_trust.read_trust(directory)
    codex: bool | None = None
    root_key = _codex_root_key(directory)
    if root_key == os.path.realpath(directory):  # P is its own root
        codex = codex_trust.read_trust(root_key)

    if claude is True or codex is True:
        return True
    if codex is False:  # Codex only ever records a deliberate "untrusted"
        return False
    return None  # a Claude `false` is noise, not a decline


# --- projection / materialization -----------------------------------------


async def _project_decision(directory: str, trusted: bool) -> None:
    """Write an EXPLICIT decision onto both providers (the project's own keys)."""
    if not directory:
        return
    await sync_to_async(_claude_write)(directory, trusted)
    await _codex_write(_codex_root_key(directory), trusted)


async def _materialize(target, source_directory: str | None, state: bool) -> None:
    """Write a project's own provider entries for an INHERITED resolution, only
    where the provider would not natively see it as trusted.

    - Claude: only **worktrees** (the walk-up covers ordinary subdirs via the
      explicit ancestor's entry; worktrees are not inherited — security patch).
    - Codex: only when the project's git-root key differs from the source's
      (i.e. a worktree, or a descendant whose trust source sits above its git
      root). An ordinary in-repo subdir shares the repo's git-root entry → skip.
    """
    directory = target.directory
    if not directory:
        return

    if target.worktree_of_id is not None:
        await sync_to_async(_claude_write)(directory, state)

    project_key = await sync_to_async(_codex_root_key)(directory)
    source_key = (
        await sync_to_async(_codex_root_key)(source_directory)
        if source_directory
        else None
    )
    if project_key and project_key != source_key:
        await _codex_write(project_key, state)


# --- DB load --------------------------------------------------------------


def _load_resolution(project_id: str):
    """Return ``(target_row, resolution, source_directory)`` (sync DB access)."""
    rows = load_trust_rows()
    by_id = {r.id: r for r in rows}
    target = by_id.get(project_id)
    if target is None:
        return None, None, None
    resolution = effective_trust(target, rows)
    source_directory = None
    if resolution.source_id and resolution.source_id in by_id:
        source_directory = by_id[resolution.source_id].directory
    return target, resolution, source_directory


# --- public entry points --------------------------------------------------


async def resolve_project_trust(project_id: str) -> dict:
    """Resolve a project's effective trust, seeding from providers if needed.

    Returns ``{state, via, source_id}`` where ``state`` is True / False / None
    (None → the caller should prompt the user). Side effects: on a resolved
    project at its first gate, materializes the provider entries and marks
    ``trust_imported``; on an unresolved-but-seedable project, adopts and
    persists the provider's own decision.
    """
    from twicc.core.models import Project

    target, resolution, source_directory = await sync_to_async(_load_resolution)(
        project_id
    )
    if target is None:
        return {"state": None, "via": "unresolved", "source_id": None}

    # Resolved (self-explicit or inherited).
    if resolution.state is not None:
        # Materialize + mark imported ONLY at the first gate of this project.
        project = await Project.objects.filter(id=project_id).afirst()
        if project is not None and not project.trust_imported:
            await _materialize(target, source_directory, resolution.state)
            await Project.objects.filter(id=project_id).aupdate(trust_imported=True)
        return {
            "state": resolution.state,
            "via": resolution.via,
            "source_id": resolution.source_id,
        }

    # Unresolved: seed once from the providers (guarded by trust_imported).
    project = await Project.objects.filter(id=project_id).afirst()
    if project is None:
        return {"state": None, "via": "unresolved", "source_id": None}
    if project.trust_imported:
        return {"state": None, "via": "unresolved", "source_id": None}

    seeded = await sync_to_async(_seed_read)(target.directory)
    if seeded is not None:
        propagation = await sync_to_async(_is_git)(target.directory) if target.directory else False
        await Project.objects.filter(id=project_id).aupdate(
            trust=seeded, trust_propagation=propagation, trust_imported=True
        )
        # Reconcile the project's own provider entries to the adopted value.
        await _project_decision(target.directory, seeded)
        return {"state": seeded, "via": "seed", "source_id": project_id}

    # Nothing to import → ask. Do NOT mark imported; decide() will.
    return {"state": None, "via": "unresolved", "source_id": None}


async def decide_project_trust(
    project_id: str, trusted: bool, propagation: bool | None = None
) -> dict:
    """Persist an explicit user decision and project it onto both providers.

    ``propagation`` defaults to "the project is under git" when not provided.
    """
    from twicc.core.models import Project

    project = await Project.objects.filter(id=project_id).afirst()
    if project is None:
        return {"ok": False, "error": "project_not_found"}

    if propagation is None:
        propagation = (
            await sync_to_async(_is_git)(project.directory)
            if project.directory
            else False
        )

    await Project.objects.filter(id=project_id).aupdate(
        trust=trusted, trust_propagation=bool(propagation), trust_imported=True
    )
    await _project_decision(project.directory, trusted)
    return {"ok": True, "state": trusted, "propagation": bool(propagation)}


__all__ = [
    "resolve_project_trust",
    "decide_project_trust",
    "TrustResolution",
]
