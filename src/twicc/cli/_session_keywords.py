"""Resolve the ``self`` / ``parent`` session keywords for CLI arguments (§11
of the agent-sharing design).

One contract for the four call sites this lot touches (``artifacts bookmark`` /
``unbookmark``, ``share create session``, ``share`` list ``--session``): a
keyword that cannot resolve fails LOCALLY, before any request submission, with
the structured ``validation_error`` output and exit 1. The two older precedents
keep their own divergent behaviour on purpose — ``update-session`` resolves
``self`` only (plain error), ``send-message`` resolves ``parent`` only; the
whole-CLI harmonisation is deliberately out of scope (design §13).
"""

from __future__ import annotations

import typer

SELF_PARENT_KEYWORDS = frozenset({"self", "parent"})


def resolve_session_keyword(
        value: str, *, param_name: str, allowed: frozenset[str]) -> str:
    """Resolve a keyword declared by this call site; pass other values through.

    ``self`` → the current session's id (PID ancestry, or the MCP-forced id).
    ``parent`` → the current session's ``spawned_by`` id.
    Failures: ``session_context_not_found`` (no current session) or
    ``parent_not_found`` (root session), both structured + exit 1, naming
    ``param_name`` and the keyword.
    """
    if value not in allowed:
        return value

    import os
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "twicc.settings")
    import django
    django.setup()

    from twicc.cli._drop_request.output import emit_validation_errors
    from twicc.cli._drop_request.validation import ValidationError
    from twicc.cli._drop_request.whoami import resolve_current_session

    current = resolve_current_session()
    if current is None:
        emit_validation_errors([ValidationError(
            param_name, "session_context_not_found",
            f"{param_name}={value!r} could not be resolved: no TwiCC session "
            f"found in the process ancestry. Run from inside a TwiCC session, "
            f"or pass an explicit session id.",
        )])
        raise typer.Exit(1)
    if value == "self":
        return current.id
    if current.spawned_by_id is None:
        emit_validation_errors([ValidationError(
            param_name, "parent_not_found",
            f"{param_name}='parent': current session {current.id!r} has no "
            f"spawned_by — it was not created via `twicc create-session` from "
            f"a parent agent. Pass an explicit session id.",
        )])
        raise typer.Exit(1)
    return current.spawned_by_id
