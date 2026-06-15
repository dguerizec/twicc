"""Auto-approve tool approvals scoped entirely to a session's system work dirs.

A TwiCC session owns a handful of system directories — its ``artifacts/<id>``,
its ``scratch/<id>``, and (inside an orchestration tree) the spawn-root
session's shared ``scratch/<root_id>``. They are TwiCC-managed scratch space,
conceptually like the agent's own ``.claude`` / ``.codex`` home: an approval
request that touches *only* these directories carries no risk to the user's
project, so TwiCC answers it on the agent's behalf instead of interrupting the
human with a prompt.

This module is the provider-neutral decision core. Each provider extracts the
concrete target paths from its own approval payload (Claude tool input, Codex
``commandActions`` / file-change diff) and hands them here together with a
``fully_known`` flag. The rule is deliberately strict:

    auto-approve  ⇔  the target set is fully enumerable (``fully_known``)
                     AND non-empty
                     AND every path resolves inside one of the session's work dirs

A single out-of-scope path — or any request whose footprint cannot be fully
enumerated (an opaque shell command, a network grant, a privilege escalation) —
falls through to the normal approval flow. Because the work-dir set passed in is
*this* session's own (``BaseAgent._work_dirs``), a request against another
session's directories is never in scope.

Containment uses ``os.path.realpath`` so ``..`` segments are normalized and
symlinks in existing ancestors are resolved: a symlink planted inside a work dir
that escapes it resolves outside and is correctly rejected (conservative).
Not-yet-created targets (a fresh ``Write``) resolve fine — only existing
components are followed.

Trust is intentionally NOT considered here: callers gate on a live untrusted
read first, because system-dir auto-approval is disabled in untrusted projects
(trust stays human-only).
"""

from __future__ import annotations

import os


def _normalized_roots(work_dirs: list[str] | None) -> list[str]:
    if not work_dirs:
        return []
    return [os.path.realpath(d) for d in work_dirs if d]


def _path_within_roots(path: str, roots: list[str]) -> bool:
    if not path:
        return False
    resolved = os.path.realpath(path)
    return any(
        resolved == root or resolved.startswith(root + os.sep) for root in roots
    )


def all_targets_within_work_dirs(
    candidate_paths: list[str], work_dirs: list[str] | None,
) -> bool:
    """True iff every candidate path resolves inside one of ``work_dirs``.

    Returns ``False`` if ``candidate_paths`` is empty or ``work_dirs`` is
    empty/``None`` — an approval with no enumerable in-scope target is never
    auto-approved.
    """
    roots = _normalized_roots(work_dirs)
    if not candidate_paths or not roots:
        return False
    return all(_path_within_roots(p, roots) for p in candidate_paths)
