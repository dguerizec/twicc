"""Create / update / delete artifact bookmarks.

Single source of truth for the two surfaces that mutate
:class:`twicc.core.models.ArtifactBookmark`:

- the HTTP REST endpoints in :mod:`twicc.views`
  (``POST/PATCH/DELETE /api/artifact-bookmarks/``), and
- the CLI drop-request handler dispatched by
  :class:`~twicc.drop_requests_watcher.DropRequestsWatcher`:

  - ``kind="artifact_bookmark:upsert"`` → :func:`upsert_artifact_bookmark_from_payload`
  - ``kind="artifact_bookmark:delete"`` → :func:`delete_artifact_bookmark_from_payload`

Both surfaces share the path-confinement helper, the DB write (under the
write lock) and the ``artifact_bookmark_updated`` / ``artifact_bookmark_removed``
broadcasts, so the byte-for-byte semantics stay aligned.

The ``*_from_payload`` functions do NOT raise for business-rule errors
(missing session, path escapes, not bookmarked, ...); they return an
:class:`ArtifactBookmarkMutationResult` with ``success=False`` and a list of
structured errors. Unexpected exceptions propagate and are translated to
``status: failed`` by the watcher. The drop-file is a trust boundary: even
though the CLI pre-validates for nicer messages, every check is re-run here.
"""

from __future__ import annotations

import logging
import os
from typing import NamedTuple

from asgiref.sync import sync_to_async
from channels.layers import get_channel_layer

from twicc.paths import get_session_artifacts_dir
from twicc.providers.db_writer import run_under_db_write_lock


logger = logging.getLogger(__name__)


class ArtifactBookmarkError(NamedTuple):
    field: str
    code: str
    message: str


class ArtifactBookmarkMutationResult(NamedTuple):
    success: bool
    bookmark_id: int | None
    session_id: str | None
    project_id: str | None
    errors: list[ArtifactBookmarkError] | None


# ──────────────────────────────────────────────────────────────────────────
# Path confinement (shared with the REST GET availability check)
# ──────────────────────────────────────────────────────────────────────────

def confined_artifact_path(session_id: str, path: str) -> str | None:
    """Resolve ``path`` inside the session's artifacts dir.

    ``path`` may be relative to the session's artifacts dir (the canonical
    bookmark key, e.g. ``demo/index.html``) or absolute — ``os.path.join``
    discards the base for an absolute path, so both forms funnel through the
    same realpath + confinement check. Returns the realpath when it stays a
    strict sub-path of the artifacts dir, else ``None``.
    """
    root_real = os.path.realpath(str(get_session_artifacts_dir(session_id)))
    abs_path = os.path.realpath(os.path.join(root_real, path))
    if not abs_path.startswith(root_real + os.sep):
        return None
    return abs_path


def relative_artifact_path(session_id: str, abs_path: str) -> str:
    """The path of ``abs_path`` relative to the session's artifacts dir.

    ``abs_path`` is expected to be a confined realpath (see
    :func:`confined_artifact_path`); the result is the canonical
    ``relative_path`` stored on the bookmark.
    """
    root_real = os.path.realpath(str(get_session_artifacts_dir(session_id)))
    return os.path.relpath(abs_path, root_real)


# ──────────────────────────────────────────────────────────────────────────
# Broadcasts (shared with the REST endpoints)
# ──────────────────────────────────────────────────────────────────────────

async def broadcast_artifact_bookmark_updated(bookmark) -> None:
    from twicc.core.serializers import serialize_artifact_bookmark

    channel_layer = get_channel_layer()
    if channel_layer is None:
        return
    await channel_layer.group_send("updates", {
        "type": "broadcast",
        "data": {"type": "artifact_bookmark_updated",
                 "bookmark": serialize_artifact_bookmark(bookmark)},
    })


async def broadcast_artifact_bookmark_removed(bookmark_id) -> None:
    channel_layer = get_channel_layer()
    if channel_layer is None:
        return
    await channel_layer.group_send("updates", {
        "type": "broadcast",
        "data": {"type": "artifact_bookmark_removed", "bookmark_id": bookmark_id},
    })


# ──────────────────────────────────────────────────────────────────────────
# Write helpers (shared by REST + drop-request)
# ──────────────────────────────────────────────────────────────────────────

async def create_or_update_artifact_bookmark(*, session, relative_path: str, name: str, scope: str):
    """Upsert a bookmark on the (session, relative_path) key, then broadcast.

    ``scope`` is concrete here (one of ``PinMode.values``); callers resolve
    "keep existing / default to project" before calling. Returns
    ``(bookmark, created)``.
    """
    from twicc.core.models import ArtifactBookmark

    bookmark, created = await run_under_db_write_lock(
        lambda: ArtifactBookmark.objects.aupdate_or_create(
            session=session, relative_path=relative_path,
            defaults={"project_id": session.project_id, "name": name, "scope": scope},
        )
    )
    await broadcast_artifact_bookmark_updated(bookmark)
    return bookmark, created


async def patch_artifact_bookmark(*, bookmark, name: str | None = None, scope: str | None = None) -> bool:
    """Apply a partial (name and/or scope) update to an existing bookmark.

    ``None`` means "leave this field untouched". Writes only the changed
    fields under the lock and broadcasts when something actually changed.
    Returns whether a write happened.
    """
    update_fields: list[str] = []
    if name is not None:
        bookmark.name = name
        update_fields.append("name")
    if scope is not None:
        bookmark.scope = scope
        update_fields.append("scope")
    if not update_fields:
        return False
    update_fields.append("updated_at")
    await run_under_db_write_lock(lambda: bookmark.asave(update_fields=update_fields))
    await broadcast_artifact_bookmark_updated(bookmark)
    return True


async def delete_artifact_bookmark(*, bookmark) -> None:
    """Delete a bookmark under the lock, then broadcast its removal."""
    bookmark_id = bookmark.id
    await run_under_db_write_lock(lambda: bookmark.adelete())
    await broadcast_artifact_bookmark_removed(bookmark_id)


# ──────────────────────────────────────────────────────────────────────────
# Network-broker allowlist (allowed_hosts) — REST-only, human consent
# ──────────────────────────────────────────────────────────────────────────
#
# Granting a widget network egress is a *consent* act, so unlike the bookmark
# upsert/delete there is deliberately NO drop-request kind: the browser host is
# the only caller. The write still goes through this service (lock + broadcast)
# to stay aligned with the rest of the bookmark surface.

async def add_artifact_allowed_host(*, bookmark, url: str, kind: str) -> str:
    """Approve ``url``'s ``scheme://host:port`` (normalized) for this artifact,
    recording the consented ``kind``. Idempotent on the normalized key (port-by-
    port — §6.4). Returns the normalized key. Raises ``ValueError`` for an
    unsupported scheme (via :func:`normalize_host_key`)."""
    from twicc.artifacts.proxy import normalize_host_key

    key = normalize_host_key(url)
    allowed = dict(bookmark.allowed_hosts or {})
    allowed[key] = {"kind": kind}
    bookmark.allowed_hosts = allowed
    await run_under_db_write_lock(
        lambda: bookmark.asave(update_fields=["allowed_hosts", "updated_at"])
    )
    await broadcast_artifact_bookmark_updated(bookmark)
    return key


async def remove_artifact_allowed_host(*, bookmark, url: str) -> bool:
    """Revoke ``url``'s normalized key from this artifact's allowlist. Returns
    whether an entry was actually removed. Raises ``ValueError`` for an
    unsupported scheme (via :func:`normalize_host_key`)."""
    from twicc.artifacts.proxy import normalize_host_key

    key = normalize_host_key(url)
    allowed = dict(bookmark.allowed_hosts or {})
    if key not in allowed:
        return False
    del allowed[key]
    bookmark.allowed_hosts = allowed
    await run_under_db_write_lock(
        lambda: bookmark.asave(update_fields=["allowed_hosts", "updated_at"])
    )
    await broadcast_artifact_bookmark_updated(bookmark)
    return True


# ──────────────────────────────────────────────────────────────────────────
# Drop-request glue (kind="artifact_bookmark:*")
# ──────────────────────────────────────────────────────────────────────────

async def upsert_artifact_bookmark_from_payload(payload: dict) -> ArtifactBookmarkMutationResult:
    """Drop-file glue for ``kind="artifact_bookmark:upsert"``.

    Expected payload::

        {
            "kind": "artifact_bookmark:upsert",
            "session_id": str,
            "path": str,            # relative to the session artifacts dir, or absolute
            "name": str,
            "scope": str | null,    # optional: project/workspace/all
        }

    Scope handling mirrors the UI: on create, an omitted scope defaults to
    ``project``; on update, an omitted scope keeps the existing one (only an
    explicit value changes it). The name is always written (required).

    Business-rule rejections (``success=False``):
    ``missing`` (session_id/path/name), ``invalid_scope``,
    ``session_not_found``, ``path_escapes``, ``file_not_found``.
    """
    from twicc.core.models import ArtifactBookmark, PinMode, Session

    session_id = (payload.get("session_id") or "").strip()
    path = (payload.get("path") or "").strip()
    name = (payload.get("name") or "").strip()
    scope = payload.get("scope")

    errors: list[ArtifactBookmarkError] = []
    if not session_id:
        errors.append(ArtifactBookmarkError("session_id", "missing", "session_id is required"))
    if not path:
        errors.append(ArtifactBookmarkError("path", "missing", "path is required"))
    if not name:
        errors.append(ArtifactBookmarkError("name", "missing", "name is required"))
    if scope is not None and scope not in PinMode.values:
        errors.append(ArtifactBookmarkError(
            "scope", "invalid_scope",
            f"scope must be one of {list(PinMode.values)}; got {scope!r}",
        ))
    if errors:
        return ArtifactBookmarkMutationResult(False, None, None, None, errors)

    session = await sync_to_async(lambda: Session.objects.filter(id=session_id).first())()
    if session is None:
        return ArtifactBookmarkMutationResult(False, None, None, None, [
            ArtifactBookmarkError("session_id", "session_not_found", f"Session {session_id!r} not found"),
        ])

    abs_path = confined_artifact_path(session_id, path)
    if abs_path is None:
        return ArtifactBookmarkMutationResult(False, None, session_id, session.project_id, [
            ArtifactBookmarkError("path", "path_escapes",
                                  "path escapes the session's artifacts directory"),
        ])
    if not await sync_to_async(os.path.isfile)(abs_path):
        return ArtifactBookmarkMutationResult(False, None, session_id, session.project_id, [
            ArtifactBookmarkError("path", "file_not_found",
                                  f"artifact file not found: {relative_artifact_path(session_id, abs_path)}"),
        ])
    rel = relative_artifact_path(session_id, abs_path)

    # Resolve the effective scope: explicit value wins; otherwise keep the
    # existing bookmark's scope (update) or default to "project" (create).
    if scope is None:
        existing = await sync_to_async(
            lambda: ArtifactBookmark.objects.filter(session=session, relative_path=rel).first()
        )()
        scope = existing.scope if existing is not None else PinMode.PROJECT

    bookmark, created = await create_or_update_artifact_bookmark(
        session=session, relative_path=rel, name=name, scope=scope,
    )
    logger.info("[artifact_bookmark_upsert] session=%s path=%r created=%s scope=%s",
                session_id, rel, created, scope)
    return ArtifactBookmarkMutationResult(True, bookmark.id, session_id, session.project_id, None)


async def delete_artifact_bookmark_from_payload(payload: dict) -> ArtifactBookmarkMutationResult:
    """Drop-file glue for ``kind="artifact_bookmark:delete"``.

    Expected payload::

        {
            "kind": "artifact_bookmark:delete",
            "session_id": str,
            "path": str,    # relative to the session artifacts dir, or absolute
        }

    The artifact file need NOT still exist — a bookmark of a moved/deleted
    file can always be removed; only the (session, relative_path) key is
    resolved (with the same confinement guard).

    Business-rule rejections (``success=False``):
    ``missing`` (session_id/path), ``path_escapes``, ``not_bookmarked``.
    """
    from twicc.core.models import ArtifactBookmark, Session

    session_id = (payload.get("session_id") or "").strip()
    path = (payload.get("path") or "").strip()

    errors: list[ArtifactBookmarkError] = []
    if not session_id:
        errors.append(ArtifactBookmarkError("session_id", "missing", "session_id is required"))
    if not path:
        errors.append(ArtifactBookmarkError("path", "missing", "path is required"))
    if errors:
        return ArtifactBookmarkMutationResult(False, None, None, None, errors)

    session = await sync_to_async(lambda: Session.objects.filter(id=session_id).first())()
    if session is None:
        return ArtifactBookmarkMutationResult(False, None, None, None, [
            ArtifactBookmarkError("session_id", "session_not_found", f"Session {session_id!r} not found"),
        ])

    abs_path = confined_artifact_path(session_id, path)
    if abs_path is None:
        return ArtifactBookmarkMutationResult(False, None, session_id, session.project_id, [
            ArtifactBookmarkError("path", "path_escapes",
                                  "path escapes the session's artifacts directory"),
        ])
    rel = relative_artifact_path(session_id, abs_path)

    bookmark = await sync_to_async(
        lambda: ArtifactBookmark.objects.filter(session=session, relative_path=rel).first()
    )()
    if bookmark is None:
        return ArtifactBookmarkMutationResult(False, None, session_id, session.project_id, [
            ArtifactBookmarkError("path", "not_bookmarked",
                                  f"no bookmark for {rel} in session {session_id!r}"),
        ])

    bookmark_id = bookmark.id
    project_id = bookmark.project_id
    await delete_artifact_bookmark(bookmark=bookmark)
    logger.info("[artifact_bookmark_delete] session=%s path=%r id=%s", session_id, rel, bookmark_id)
    return ArtifactBookmarkMutationResult(True, bookmark_id, session_id, project_id, None)
