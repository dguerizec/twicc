"""Resolve and pre-create the system work directories granted to an agent."""

from __future__ import annotations

import logging

from asgiref.sync import sync_to_async

logger = logging.getLogger(__name__)


async def resolve_and_create_work_dirs(
    session_id: str,
    *,
    pending_id: str | None = None,
) -> list[str]:
    """Pre-create one session's work directories and return absolute paths.

    Always covers the session's own ``artifacts/<id>`` and ``scratch/<id>``.
    For a session inside an orchestration tree (``Session.spawn_root`` set),
    the root session's ``scratch/<root_id>`` is added too, so the whole
    subtree shares one scratch folder.

    ``pending_id`` identifies the pending-session buffer entry when it differs
    from the canonical provider session id. Codex needs this during a fresh
    start: the pending attributes remain keyed by TwiCC's draft id after Codex
    has minted the canonical thread id used for the directory names.

    Each id must be a single safe path component. Directory creation is
    best-effort: an unsafe id or failed ``mkdir`` is logged and omitted so a
    filesystem problem never aborts agent startup.
    """
    from twicc.core.models import Session
    from twicc.paths import get_artifacts_dir, get_scratch_dir
    from twicc.pending_session_attributes import get_pending_session_attributes

    def _build() -> list[str]:
        # (root, segment) pairs to grant. ``segment`` is a session id used as
        # one path component under ``root``; the guard below prevents a
        # malformed id from selecting another directory.
        candidates = [
            (get_artifacts_dir(), session_id),
            (get_scratch_dir(), session_id),
        ]

        # The Session row is authoritative on resume. During a fresh start it
        # may not exist yet, so fall back to the pending attributes. Codex's
        # pending entry is still keyed by the draft id at this point.
        spawn_root_id = (
            Session.objects
            .filter(id=session_id)
            .values_list("spawn_root_id", flat=True)
            .first()
        )
        if spawn_root_id is None:
            pending = get_pending_session_attributes(pending_id or session_id)
            spawn_root_id = pending.spawn_root_id if pending else None
        if spawn_root_id and spawn_root_id != session_id:
            candidates.append((get_scratch_dir(), spawn_root_id))

        work_dirs: list[str] = []
        for root, segment in candidates:
            if (
                not segment
                or segment in (".", "..")
                or "/" in segment
                or "\\" in segment
                or "\x00" in segment
            ):
                logger.warning(
                    "Skipping unsafe work dir id %r under %s for session %s",
                    segment, root, session_id,
                )
                continue

            target = root / segment
            try:
                target.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                logger.warning(
                    "Could not pre-create work dir %s for session %s: %s",
                    target, session_id, exc,
                )
                continue
            work_dirs.append(str(target))
        return work_dirs

    return await sync_to_async(_build)()
