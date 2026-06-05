"""
Read/write workspaces from/to workspaces.json in the data directory.

Workspaces group projects into named collections with optional layout and
filter configuration. They are stored as a simple JSON object in
<data_dir>/workspaces.json.

Follow the exact same pattern as synced_settings.py.

In addition to the low-level read/write helpers, this module hosts the
in-process serialisation lock, the validation helpers shared by the UI
and the CLI, the ID slugifier, and the atomic create/update/delete
operations called by :mod:`twicc.core.services.workspace_mutation`.
"""

import asyncio
import logging
import os
import re
import tempfile
from typing import NamedTuple

import orjson
from asgiref.sync import sync_to_async
from channels.layers import get_channel_layer

from twicc.paths import get_workspaces_path

logger = logging.getLogger(__name__)


MAX_WORKSPACE_NAME_LENGTH = 20

# Same character class as the frontend's `_generateId`
# (frontend/src/stores/workspaces.js): keep alphanumeric, underscore, hyphen
# in the slug; everything else collapses to a hyphen.
_SLUG_INVALID_CHARS_RE = re.compile(r"[^a-z0-9_-]")
_SLUG_REPEATED_HYPHENS_RE = re.compile(r"-{2,}")
_SLUG_TRIM_HYPHENS_RE = re.compile(r"^-|-$")

# Accepts CSS hex colors (#rgb, #rrggbb, #rrggbbaa) — same format produced
# by the UI's `<wa-color-picker>`. Other CSS color forms (named, rgb(),
# hsl()) are rejected from the CLI to keep the validation rule simple and
# unambiguous; users wanting a fancier color can still edit through the UI.
_HEX_COLOR_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")


# Serializes the read-modify-write cycle on workspaces.json across the main
# process: ``auto_add_project_to_workspaces`` (watcher, views, …),
# ``_handle_update_workspaces`` (WS handler that writes whole-blob updates
# from the UI), and the CLI-driven atomic ops below must not interleave
# their reads and writes, or one side's changes get clobbered.
_workspaces_lock = asyncio.Lock()


class WorkspaceMutationError(NamedTuple):
    """One structured error returned by a workspace mutation.

    ``field`` is the public name the caller used (e.g. ``"--name"``,
    ``"WORKSPACE_ID"``, ``"--add-project"``); ``code`` is the machine-
    readable token the CLI / UI can switch on (``invalid_name``,
    ``duplicate_name``, ``workspace_not_found``, ...).
    """
    field: str
    code: str
    message: str


class WorkspaceMutationResult(NamedTuple):
    """Outcome of a workspace mutation (create / update / delete).

    ``success=True`` → ``workspace_id`` is set; ``workspace`` carries the
    final state for create / update (None for delete).
    ``success=False`` → ``errors`` is a non-empty list and the other
    fields may be None.
    """
    success: bool
    workspace_id: str | None
    workspace: dict | None
    errors: list[WorkspaceMutationError] | None


# ---------------------------------------------------------------------------
# Low-level IO
# ---------------------------------------------------------------------------


def read_workspaces() -> dict:
    """Read workspaces from workspaces.json.

    Returns an empty dict if the file doesn't exist or is invalid.
    """
    path = get_workspaces_path()
    try:
        return orjson.loads(path.read_bytes())
    except (FileNotFoundError, orjson.JSONDecodeError):
        return {}


def write_workspaces(data: dict) -> None:
    """Write workspaces to workspaces.json atomically.

    Uses write-to-temp-then-rename to avoid partial writes.
    """
    path = get_workspaces_path()
    content = orjson.dumps(data, option=orjson.OPT_INDENT_2)

    # Write to a temp file in the same directory, then atomically replace.
    fd, tmp_path = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(content)
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def match_pattern(directory: str, pattern: str) -> bool:
    """Check if a directory path matches a pattern using ``*`` as wildcard.

    If the pattern contains no ``*``, it is treated as a directory prefix
    (``/some/path`` behaves like ``/some/path/*``).
    """
    effective = pattern if "*" in pattern else pattern.rstrip("/") + "/*"
    regex = re.compile("^" + ".*".join(re.escape(part) for part in effective.split("*")) + "$", re.IGNORECASE)
    return regex.search(directory) is not None


# ---------------------------------------------------------------------------
# Validation helpers (pure, no IO)
# ---------------------------------------------------------------------------


def validate_workspace_name(
    name: str,
    *,
    existing_workspaces: list[dict],
    current_id: str | None = None,
    field: str = "name",
) -> list[WorkspaceMutationError]:
    """Validate a workspace name: trimmed, non-empty, ≤ MAX length, unique.

    ``existing_workspaces`` is the current contents of ``workspaces.json``
    (the ``"workspaces"`` list). ``current_id`` is the id of the workspace
    being renamed (excluded from the uniqueness check); leave None for
    creation. ``field`` is echoed back in the returned errors so callers
    can route per-flag messages (``"--name"`` vs ``"NAME"``, etc.).
    """
    trimmed = (name or "").strip()
    if not trimmed:
        return [WorkspaceMutationError(field, "invalid_name",
                                       "Name cannot be empty (or whitespace only).")]
    if len(trimmed) > MAX_WORKSPACE_NAME_LENGTH:
        return [WorkspaceMutationError(field, "invalid_name",
                                       f"Name must be ≤ {MAX_WORKSPACE_NAME_LENGTH} characters "
                                       f"(got {len(trimmed)}).")]
    lower = trimmed.lower()
    for ws in existing_workspaces:
        if current_id is not None and ws.get("id") == current_id:
            continue
        if (ws.get("name") or "").strip().lower() == lower:
            return [WorkspaceMutationError(field, "duplicate_name",
                                           f"A workspace named {trimmed!r} already exists "
                                           f"(id: {ws.get('id')!r}).")]
    return []


def validate_color(color: str | None, *, field: str = "color") -> list[WorkspaceMutationError]:
    """Validate a CSS color value (hex only). ``None`` and empty are OK."""
    if color is None or color == "":
        return []
    if not _HEX_COLOR_RE.match(color):
        return [WorkspaceMutationError(field, "invalid_color",
                                       f"Color must be a CSS hex value (#rgb, #rrggbb, or "
                                       f"#rrggbbaa); got {color!r}.")]
    return []


def validate_pattern(pattern: str, *, field: str = "pattern") -> list[WorkspaceMutationError]:
    """Validate an auto-add directory pattern: trimmed, non-empty."""
    trimmed = (pattern or "").strip()
    if not trimmed:
        return [WorkspaceMutationError(field, "invalid_pattern",
                                       "Pattern cannot be empty (or whitespace only).")]
    return []


def slugify_workspace_id(name: str, *, existing_ids: set[str]) -> str:
    """Generate a workspace ID slug from a name, appending -2/-3/... on collision.

    Mirrors the frontend's ``_generateId`` (frontend/src/stores/workspaces.js)
    byte-for-byte so the same name produces the same id on both sides:
    lowercase, replace non-alphanumeric with ``-``, collapse repeated
    hyphens, trim leading/trailing hyphens. Falls back to ``"workspace"``
    if nothing survives.
    """
    base = name.lower()
    base = _SLUG_INVALID_CHARS_RE.sub("-", base)
    base = _SLUG_REPEATED_HYPHENS_RE.sub("-", base)
    base = _SLUG_TRIM_HYPHENS_RE.sub("", base)
    if not base:
        base = "workspace"
    if base not in existing_ids:
        return base
    suffix = 2
    while f"{base}-{suffix}" in existing_ids:
        suffix += 1
    return f"{base}-{suffix}"


# ---------------------------------------------------------------------------
# Existing background helper (untouched API)
# ---------------------------------------------------------------------------


async def auto_add_project_to_workspaces(project_id: str, directory: str) -> None:
    """Auto-add a newly detected project to workspaces whose patterns match
    its directory.

    The read-modify-write on ``workspaces.json`` runs inside
    ``_workspaces_lock`` so a concurrent ``_handle_update_workspaces``
    (whole-blob writes from the UI) can't clobber the appended project_id
    (and vice versa). If at least one workspace was modified, broadcasts
    ``workspaces_updated`` outside the lock.

    Idempotent: a workspace that already lists the project is skipped, no
    write, no broadcast.
    """
    def _read_modify_write() -> list[dict] | None:
        data = read_workspaces()
        workspaces = data.get("workspaces", [])
        modified = False
        for ws in workspaces:
            patterns = ws.get("autoProjectPatterns", [])
            if not patterns or project_id in ws.get("projectIds", []):
                continue
            if any(match_pattern(directory, p) for p in patterns):
                ws.setdefault("projectIds", []).append(project_id)
                modified = True
                logger.info("Auto-added project %s to workspace %r",
                            project_id, ws.get("name", ws.get("id")))
        if not modified:
            return None
        write_workspaces(data)
        return workspaces

    async with _workspaces_lock:
        workspaces = await sync_to_async(_read_modify_write)()
    if workspaces is None:
        return

    await _broadcast_workspaces_updated(workspaces)


async def add_project_to_workspaces(project_id: str, workspace_ids: list[str]) -> None:
    """Add a project to an explicit list of workspaces (by id).

    The symmetric companion to :func:`auto_add_project_to_workspaces`: that
    one adds a project to workspaces whose *patterns* match its directory,
    this one to the workspaces the user *explicitly* picked (e.g. from the
    project edit dialog at creation time). Both append under
    ``_workspaces_lock`` so neither clobbers the other — nor a concurrent
    whole-blob UI write. Doing the explicit add server-side (instead of a
    frontend whole-blob write right after the project is created) is what
    keeps it from racing with, and overwriting, the auto-add that
    ``register_project`` runs at creation.

    Idempotent: a workspace that already lists the project, or an id that
    matches no workspace, is silently skipped. Broadcasts
    ``workspaces_updated`` once, outside the lock, only if something changed.
    """
    wanted = [wid for wid in dict.fromkeys(workspace_ids) if wid]
    if not wanted:
        return

    def _read_modify_write() -> list[dict] | None:
        data = read_workspaces()
        workspaces = data.get("workspaces", [])
        wanted_set = set(wanted)
        modified = False
        for ws in workspaces:
            if ws.get("id") not in wanted_set:
                continue
            if project_id in ws.get("projectIds", []):
                continue
            ws.setdefault("projectIds", []).append(project_id)
            modified = True
            logger.info("Added project %s to workspace %r (explicit)",
                        project_id, ws.get("name", ws.get("id")))
        if not modified:
            return None
        write_workspaces(data)
        return workspaces

    async with _workspaces_lock:
        workspaces = await sync_to_async(_read_modify_write)()
    if workspaces is None:
        return

    await _broadcast_workspaces_updated(workspaces)


# ---------------------------------------------------------------------------
# Atomic create / update / delete (CLI / future-WS entry points)
# ---------------------------------------------------------------------------


async def create_workspace_atomic(
    *,
    name: str,
    color: str | None = None,
    project_ids: list[str] | None = None,
    auto_project_patterns: list[str] | None = None,
    archived: bool = False,
) -> WorkspaceMutationResult:
    """Atomically create a new workspace. Returns the new workspace dict.

    The full create flow runs under ``_workspaces_lock`` so the name
    uniqueness check and the slug collision check see the same snapshot
    used for the write. After the lock is released, broadcasts the full
    new workspaces list as ``workspaces_updated``.

    Validation errors (empty/too-long name, duplicate name, invalid color,
    invalid pattern) are returned as a failed ``WorkspaceMutationResult``
    and never raise.
    """
    project_ids = list(project_ids or [])
    auto_project_patterns = list(auto_project_patterns or [])

    def _read_validate_write() -> WorkspaceMutationResult:
        data = read_workspaces()
        workspaces = data.setdefault("workspaces", [])

        errors: list[WorkspaceMutationError] = []
        errors.extend(validate_workspace_name(name, existing_workspaces=workspaces))
        errors.extend(validate_color(color))
        for p in auto_project_patterns:
            errors.extend(validate_pattern(p))
        if errors:
            return WorkspaceMutationResult(False, None, None, errors)

        trimmed_name = name.strip()
        existing_ids = {ws.get("id") for ws in workspaces if ws.get("id")}
        ws_id = slugify_workspace_id(trimmed_name, existing_ids=existing_ids)

        # Dedupe while preserving order (append semantics).
        deduped_projects: list[str] = []
        seen_projects: set[str] = set()
        for pid in project_ids:
            if pid not in seen_projects:
                deduped_projects.append(pid)
                seen_projects.add(pid)

        deduped_patterns: list[str] = []
        seen_patterns: set[str] = set()
        for p in auto_project_patterns:
            trimmed_p = p.strip()
            if trimmed_p not in seen_patterns:
                deduped_patterns.append(trimmed_p)
                seen_patterns.add(trimmed_p)

        ws = {
            "id": ws_id,
            "name": trimmed_name,
            "archived": bool(archived),
            "projectIds": deduped_projects,
            "color": color if color else None,
            "autoProjectPatterns": deduped_patterns,
        }
        workspaces.append(ws)
        write_workspaces(data)
        return WorkspaceMutationResult(True, ws_id, ws, None)

    async with _workspaces_lock:
        result = await sync_to_async(_read_validate_write)()

    if result.success:
        # Re-read inside the broadcast path so the payload always reflects
        # the latest on-disk state (cheap, and immune to a parallel auto-add
        # broadcast slipping in between).
        await _broadcast_after_write()
    return result


async def update_workspace_atomic(
    workspace_id: str,
    *,
    new_name: str | None = None,
    color: str | None = None,
    unset_color: bool = False,
    add_projects: list[str] | None = None,
    remove_projects: list[str] | None = None,
    add_patterns: list[str] | None = None,
    remove_patterns: list[str] | None = None,
    archived: bool | None = None,
) -> WorkspaceMutationResult:
    """Atomically apply a patch to an existing workspace.

    A ``None`` (or empty list) for any keyword leaves the corresponding
    field untouched. ``unset_color=True`` and ``color=<value>`` are
    mutually exclusive — the caller must enforce that constraint before
    calling (this function trusts its arguments and would silently let the
    unset win).

    Add/remove on project_ids and patterns are idempotent (silently skip
    duplicates / absentees), matching the auto-add helper's semantics.
    """
    add_projects = list(add_projects or [])
    remove_projects = list(remove_projects or [])
    add_patterns = list(add_patterns or [])
    remove_patterns = list(remove_patterns or [])

    def _read_validate_write() -> WorkspaceMutationResult:
        data = read_workspaces()
        workspaces = data.setdefault("workspaces", [])
        ws = next((w for w in workspaces if w.get("id") == workspace_id), None)
        if ws is None:
            return WorkspaceMutationResult(False, None, None, [
                WorkspaceMutationError("WORKSPACE_ID", "workspace_not_found",
                                       f"Workspace {workspace_id!r} not found."),
            ])

        errors: list[WorkspaceMutationError] = []
        if new_name is not None:
            errors.extend(validate_workspace_name(
                new_name,
                existing_workspaces=workspaces,
                current_id=workspace_id,
                field="--name",
            ))
        if not unset_color and color is not None:
            errors.extend(validate_color(color, field="--color"))
        for p in add_patterns:
            errors.extend(validate_pattern(p, field="--add-pattern"))
        if errors:
            return WorkspaceMutationResult(False, workspace_id, None, errors)

        if new_name is not None:
            ws["name"] = new_name.strip()
        if unset_color:
            ws["color"] = None
        elif color is not None:
            ws["color"] = color
        if archived is not None:
            ws["archived"] = bool(archived)

        if add_projects or remove_projects:
            current_projects = list(ws.get("projectIds", []))
            seen = set(current_projects)
            for pid in add_projects:
                if pid not in seen:
                    current_projects.append(pid)
                    seen.add(pid)
            to_remove = set(remove_projects)
            current_projects = [pid for pid in current_projects if pid not in to_remove]
            ws["projectIds"] = current_projects

        if add_patterns or remove_patterns:
            current_patterns = list(ws.get("autoProjectPatterns", []))
            seen_p = set(current_patterns)
            for p in add_patterns:
                trimmed_p = p.strip()
                if trimmed_p not in seen_p:
                    current_patterns.append(trimmed_p)
                    seen_p.add(trimmed_p)
            to_remove_p = set(remove_patterns)
            current_patterns = [p for p in current_patterns if p not in to_remove_p]
            ws["autoProjectPatterns"] = current_patterns

        write_workspaces(data)
        return WorkspaceMutationResult(True, workspace_id, ws, None)

    async with _workspaces_lock:
        result = await sync_to_async(_read_validate_write)()

    if result.success:
        await _broadcast_after_write()
    return result


async def delete_workspace_atomic(workspace_id: str) -> WorkspaceMutationResult:
    """Atomically remove a workspace from the list.

    Projects referenced by the workspace are not affected (they continue
    to exist in DB and in any other workspace listing them).
    """
    def _read_validate_write() -> WorkspaceMutationResult:
        data = read_workspaces()
        workspaces = data.setdefault("workspaces", [])
        idx = next((i for i, w in enumerate(workspaces) if w.get("id") == workspace_id), None)
        if idx is None:
            return WorkspaceMutationResult(False, None, None, [
                WorkspaceMutationError("WORKSPACE_ID", "workspace_not_found",
                                       f"Workspace {workspace_id!r} not found."),
            ])
        del workspaces[idx]
        write_workspaces(data)
        return WorkspaceMutationResult(True, workspace_id, None, None)

    async with _workspaces_lock:
        result = await sync_to_async(_read_validate_write)()

    if result.success:
        await _broadcast_after_write()
    return result


# ---------------------------------------------------------------------------
# Broadcast helpers
# ---------------------------------------------------------------------------


async def _broadcast_after_write() -> None:
    """Re-read workspaces.json from disk and broadcast the full list.

    Called by every atomic op after the lock is released. Re-reading on
    the broadcast path keeps the payload aligned with on-disk state even
    if another writer (auto-add, WS whole-blob) slipped in between, so
    UI clients always converge to the same snapshot.
    """
    data = await sync_to_async(read_workspaces)()
    workspaces = data.get("workspaces", [])
    await _broadcast_workspaces_updated(workspaces)


async def _broadcast_workspaces_updated(workspaces: list[dict]) -> None:
    channel_layer = get_channel_layer()
    await channel_layer.group_send("updates", {
        "type": "broadcast",
        "data": {"type": "workspaces_updated", "workspaces": workspaces},
    })
