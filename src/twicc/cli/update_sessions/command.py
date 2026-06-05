"""Top-level ``twicc update-sessions`` sub-app (batch updates).

One sub-command per supported batch update — ``archive`` / ``unarchive``,
``pin`` / ``unpin``, ``hide`` / ``unhide``, ``annotations`` — each applying the
SAME change to every targeted session via the shared
:func:`twicc.cli.update_sessions._runner.run_batch_update`.

Deliberately excluded from the batch surface:

- ``settings`` — deferred: its preset resolution and value validation are
  per-provider, so a batch over mixed providers needs its own design.
- ``title`` — setting the same title on several sessions is meaningless.

Unlike ``update-session`` (where the single id is a parent-callback argument),
the session selector here is a positional ``SESSION_ID...`` list on each
sub-command, mirroring ``processes stop``. So the values the singular command
took positionally move to options: ``pin --mode`` and ``annotations --op``.
The shared ``--spawned-by`` / ``--descendants`` / ``--annotation`` scope
filters are merged (union) with the explicit ids, explicit ids first.
"""

from __future__ import annotations

import typer

from twicc.cli._output import emit_error
from twicc.cli.update_sessions._runner import run_batch_update


# Mirrors :class:`twicc.core.models.PinMode`. Kept as a flat tuple so ``pin``
# validates ``--mode`` locally without importing Django.
_VALID_PIN_MODES: tuple[str, ...] = ("project", "workspace", "all")


# Shared help strings — every sub-command reuses the same scope-filter and
# timeout wording (kept identical to ``processes stop`` semantics).
_SESSION_IDS_HELP = (
    "Sessions to update. Optional if you pass --spawned-by or --descendants "
    "(explicit ids and scope-selected ids are merged, explicit first). "
    "Use 'self' for the current session."
)
_SPAWNED_BY_HELP = (
    "Also target sessions spawned by the given session_id, or 'self' for the "
    "current session. Merged (union) with explicit SESSION_IDs. "
    "'parent' is not supported. Mutually exclusive with --descendants."
)
_DESCENDANTS_HELP = (
    "Also target the proper descendants of the given session_id, or 'self' "
    "for the current session. Merged (union) with explicit SESSION_IDs. "
    "'parent' is not supported. Mutually exclusive with --spawned-by."
)
_ANNOTATION_HELP = (
    "Narrow the --spawned-by / --descendants scope by annotation. Repeatable, "
    "AND-combined. Requires a filiation scope; does NOT filter explicit "
    "SESSION_IDs. Same syntax as `twicc sessions --annotation`."
)
_TIMEOUT_HELP = (
    "Wall-clock seconds to wait for the whole batch (drops are processed in "
    "parallel server-side, so this is a budget, not N×timeout). Per-id "
    'entries with no final status by the deadline get status="timeout". '
    "Must be > 0."
)
_OP_HELP = (
    "Annotation operation, applied in order to every targeted session. "
    "Repeatable; at least one required. One of: clear, replace-file:PATH, "
    "merge-file:PATH, set:KEY=VALUE, unset:KEY."
)


update_sessions_app = typer.Typer(
    name="update-sessions",
    help=(
        "Apply the same update to several sessions at once (archive, "
        "unarchive, pin, unpin, hide, unhide, annotations)."
    ),
    invoke_without_command=False,
)


@update_sessions_app.command(name="archive")
def _archive(
    session_ids: list[str] | None = typer.Argument(
        None, metavar="SESSION_ID...", help=_SESSION_IDS_HELP,
    ),
    spawned_by: str = typer.Option(None, "--spawned-by", help=_SPAWNED_BY_HELP),
    descendants: str = typer.Option(None, "--descendants", help=_DESCENDANTS_HELP),
    annotation: list[str] = typer.Option([], "--annotation", help=_ANNOTATION_HELP),
    timeout: int = typer.Option(30, "--timeout", help=_TIMEOUT_HELP),
) -> None:
    """Archive every targeted session.

    Per session, same effect as `update-session <ID> archive`: kills the live
    agent (reason=archived) and any tmux terminal, may auto-unpin
    (autoUnpinOnArchive synced setting), broadcasts session_updated.
    """
    run_batch_update(
        session_ids or [],
        kind="session:update_archived",
        build_payload=lambda r: {"session_id": r.session_id, "archived": True},
        timeout=timeout,
        spawned_by=spawned_by,
        descendants=descendants,
        annotation=annotation,
    )


@update_sessions_app.command(name="unarchive")
def _unarchive(
    session_ids: list[str] | None = typer.Argument(
        None, metavar="SESSION_ID...", help=_SESSION_IDS_HELP,
    ),
    spawned_by: str = typer.Option(None, "--spawned-by", help=_SPAWNED_BY_HELP),
    descendants: str = typer.Option(None, "--descendants", help=_DESCENDANTS_HELP),
    annotation: list[str] = typer.Option([], "--annotation", help=_ANNOTATION_HELP),
    timeout: int = typer.Option(30, "--timeout", help=_TIMEOUT_HELP),
) -> None:
    """Unarchive every targeted session (flips archived back to False; no resume)."""
    run_batch_update(
        session_ids or [],
        kind="session:update_archived",
        build_payload=lambda r: {"session_id": r.session_id, "archived": False},
        timeout=timeout,
        spawned_by=spawned_by,
        descendants=descendants,
        annotation=annotation,
    )


@update_sessions_app.command(name="pin")
def _pin(
    session_ids: list[str] | None = typer.Argument(
        None, metavar="SESSION_ID...", help=_SESSION_IDS_HELP,
    ),
    mode: str = typer.Option(
        ...,
        "--mode",
        help=(
            "Pin scope applied to every targeted session: 'project', "
            "'workspace', or 'all'. Same vocabulary as the UI's pin menu."
        ),
    ),
    spawned_by: str = typer.Option(None, "--spawned-by", help=_SPAWNED_BY_HELP),
    descendants: str = typer.Option(None, "--descendants", help=_DESCENDANTS_HELP),
    annotation: list[str] = typer.Option([], "--annotation", help=_ANNOTATION_HELP),
    timeout: int = typer.Option(30, "--timeout", help=_TIMEOUT_HELP),
) -> None:
    """Pin every targeted session in the given scope."""
    if mode not in _VALID_PIN_MODES:
        emit_error(
            f"Error: invalid --mode {mode!r}. Accepted: {list(_VALID_PIN_MODES)}.",
            code=1,
        )
    run_batch_update(
        session_ids or [],
        kind="session:update_pinned",
        build_payload=lambda r: {"session_id": r.session_id, "pinned": mode},
        timeout=timeout,
        spawned_by=spawned_by,
        descendants=descendants,
        annotation=annotation,
    )


@update_sessions_app.command(name="unpin")
def _unpin(
    session_ids: list[str] | None = typer.Argument(
        None, metavar="SESSION_ID...", help=_SESSION_IDS_HELP,
    ),
    spawned_by: str = typer.Option(None, "--spawned-by", help=_SPAWNED_BY_HELP),
    descendants: str = typer.Option(None, "--descendants", help=_DESCENDANTS_HELP),
    annotation: list[str] = typer.Option([], "--annotation", help=_ANNOTATION_HELP),
    timeout: int = typer.Option(30, "--timeout", help=_TIMEOUT_HELP),
) -> None:
    """Unpin every targeted session (regardless of its current pin scope)."""
    run_batch_update(
        session_ids or [],
        kind="session:update_pinned",
        build_payload=lambda r: {"session_id": r.session_id, "pinned": None},
        timeout=timeout,
        spawned_by=spawned_by,
        descendants=descendants,
        annotation=annotation,
    )


@update_sessions_app.command(name="hide")
def _hide(
    session_ids: list[str] | None = typer.Argument(
        None, metavar="SESSION_ID...", help=_SESSION_IDS_HELP,
    ),
    spawned_by: str = typer.Option(None, "--spawned-by", help=_SPAWNED_BY_HELP),
    descendants: str = typer.Option(None, "--descendants", help=_DESCENDANTS_HELP),
    annotation: list[str] = typer.Option([], "--annotation", help=_ANNOTATION_HELP),
    timeout: int = typer.Option(30, "--timeout", help=_TIMEOUT_HELP),
) -> None:
    """Hide every targeted session.

    Per session, same invariants as `update-session <ID> hide`: a session
    whose settings break the hidden whitelist (permission_mode /
    question_widget) is rejected individually (status=rejected) while the
    others are hidden.
    """
    run_batch_update(
        session_ids or [],
        kind="session:update_hidden",
        build_payload=lambda r: {"session_id": r.session_id, "hidden": True},
        timeout=timeout,
        spawned_by=spawned_by,
        descendants=descendants,
        annotation=annotation,
    )


@update_sessions_app.command(name="unhide")
def _unhide(
    session_ids: list[str] | None = typer.Argument(
        None, metavar="SESSION_ID...", help=_SESSION_IDS_HELP,
    ),
    spawned_by: str = typer.Option(None, "--spawned-by", help=_SPAWNED_BY_HELP),
    descendants: str = typer.Option(None, "--descendants", help=_DESCENDANTS_HELP),
    annotation: list[str] = typer.Option([], "--annotation", help=_ANNOTATION_HELP),
    timeout: int = typer.Option(30, "--timeout", help=_TIMEOUT_HELP),
) -> None:
    """Unhide every targeted session (re-enters lists / search / counters)."""
    run_batch_update(
        session_ids or [],
        kind="session:update_hidden",
        build_payload=lambda r: {"session_id": r.session_id, "hidden": False},
        timeout=timeout,
        spawned_by=spawned_by,
        descendants=descendants,
        annotation=annotation,
    )


@update_sessions_app.command(name="annotations")
def _annotations(
    session_ids: list[str] | None = typer.Argument(
        None, metavar="SESSION_ID...", help=_SESSION_IDS_HELP,
    ),
    op: list[str] = typer.Option([], "--op", metavar="OPERATION", help=_OP_HELP),
    spawned_by: str = typer.Option(None, "--spawned-by", help=_SPAWNED_BY_HELP),
    descendants: str = typer.Option(None, "--descendants", help=_DESCENDANTS_HELP),
    annotation: list[str] = typer.Option([], "--annotation", help=_ANNOTATION_HELP),
    timeout: int = typer.Option(30, "--timeout", help=_TIMEOUT_HELP),
) -> None:
    """Apply the same ordered annotation operations to every targeted session.

    Note the two distinct annotation flags: ``--op`` is the mutation applied to
    each session; ``--annotation`` is a read-only FILTER that narrows the
    --spawned-by / --descendants scope. Operations are parsed once (a malformed
    --op fails the whole command); a per-session apply error (e.g. a path
    conflict against that session's existing annotations) is reported per id.
    """
    # Operation parsing is global (same ops for every id) and Django-free, so
    # do it up-front: a malformed --op is a classic argument error (exit 1).
    import os
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "twicc.settings")
    import django
    django.setup()

    from twicc.cli._drop_request.annotations import parse_annotation_update_operations

    parsed_operations, errors = parse_annotation_update_operations(op)
    if errors:
        emit_error(
            "Error: invalid --op:\n"
            + "\n".join(f"  - {e.code}: {e.message}" for e in errors),
            code=1,
        )

    run_batch_update(
        session_ids or [],
        kind="session:update_annotations",
        build_payload=lambda r: {
            "session_id": r.session_id, "operations": parsed_operations,
        },
        timeout=timeout,
        spawned_by=spawned_by,
        descendants=descendants,
        annotation=annotation,
    )
