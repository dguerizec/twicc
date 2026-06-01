"""CLI implementation for the ``twicc processes`` subcommand."""

import sys

import orjson


def main(
    *,
    provider: str | None = None,
    state: str | None = None,
    limit: int = 20,
    offset: int = 0,
    include_hidden: bool = False,
    only_hidden: bool = False,
    spawned_by: str | None = None,
    spawn_root: str | None = None,
) -> None:
    """List currently running processes (live ProcessRuns) of the running TwiCC.

    Scopes to ``twicc_pid`` equal to the PID recorded in ``twicc.info.json``
    and excludes ``state=DEAD`` rows — a DEAD row never represents a
    running process (Claude Code keeps DEAD rows around solely so the boot
    cron restart can reclaim them, never to advertise a live worker).

    The CLI projects the persisted ``state`` + ``awaiting_user_input``
    columns onto a single 4-value vocabulary for output and filtering:

    - ``starting`` (state=STARTING)
    - ``assistant_turn`` (state=ASSISTANT_TURN AND awaiting_user_input=False)
    - ``awaiting_user_input`` (awaiting_user_input=True; always
      implies state=ASSISTANT_TURN by construction)
    - ``user_turn`` (state=USER_TURN; awaiting is always False here)

    Optional filters narrow the result further:

    - ``provider`` restricts to one backend (``claude_code``, ``codex``).
    - ``state`` restricts to one of the four virtual values above; ``dead``
      is rejected to keep the "live processes only" guarantee.
    """
    import django

    django.setup()

    from twicc.cli._drop_request.whoami import (
        resolve_spawn_root_filter,
        resolve_spawned_by_filter,
    )

    try:
        spawned_by_id = resolve_spawned_by_filter(spawned_by)
        spawn_root_id = resolve_spawn_root_filter(spawn_root)
    except RuntimeError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)

    from twicc.agent.states import AgentState
    from twicc.cli._process_state import (
        AWAITING_VIRTUAL_STATE,
        LIVE_VIRTUAL_STATES,
        serialize_process_row,
    )
    from twicc.cli._twicc_info import resolve_live_twicc_or_exit
    from twicc.core.models import ProcessRun, Session

    info = resolve_live_twicc_or_exit()

    qs = (
        ProcessRun.objects
        .filter(twicc_pid=info.pid)
        .exclude(state=AgentState.DEAD.value)
        .order_by("-started_at")
    )

    if state is not None:
        if state not in LIVE_VIRTUAL_STATES:
            print(
                f"Error: invalid --state '{state}'. Use one of: "
                f"{', '.join(sorted(LIVE_VIRTUAL_STATES))}.",
                file=sys.stderr,
            )
            sys.exit(1)
        if state == AWAITING_VIRTUAL_STATE:
            # awaiting_user_input rows are necessarily in ASSISTANT_TURN; the
            # flag is the canonical filter so we don't combine with state.
            qs = qs.filter(awaiting_user_input=True)
        else:
            # Real-state filters must also exclude awaiting rows so the four
            # virtual buckets stay disjoint — otherwise --state assistant_turn
            # would also return rows the CLI projects as awaiting_user_input.
            qs = qs.filter(state=state, awaiting_user_input=False)

    if provider is not None:
        qs = qs.filter(provider=provider)

    rows = list(qs[offset : offset + limit])

    # Enrich with the matching Session's title, project_id, hidden, spawned_by_id
    # and spawn_root_id when the session row has already been created by the
    # watcher. Brand-new sessions that haven't reached their first JSONL line yet
    # have no Session row, so those fields fall back to ``None``.
    session_ids = [r.session_id for r in rows]
    sessions_by_id = {
        s.id: s
        for s in Session.objects.filter(id__in=session_ids).only(
            "id", "title", "project_id", "hidden", "spawned_by_id", "spawn_root_id"
        )
    }

    # Apply hidden / spawned_by / spawn_root filters (post-enrichment, since these
    # fields come from the Session row, not from ProcessRun itself). Same semantics
    # as ``twicc sessions``:
    #
    # - ``--only-hidden``: keep hidden=True only.
    # - ``--include-hidden``: no implicit hidden filter (both kinds).
    # - ``--spawned-by`` or ``--spawn-root`` is set (without ``--include-hidden`` /
    #   ``--only-hidden``): the caller is explicitly asking about filiation,
    #   show every matching session in the tree whatever its visibility.
    # - Default (no flag): keep hidden=False only — match what the UI sees.
    filtered = []
    for row in rows:
        session = sessions_by_id.get(row.session_id)
        is_hidden = session.hidden if session is not None else False
        if only_hidden and not is_hidden:
            continue
        if (
            not include_hidden
            and not only_hidden
            and spawned_by_id is None
            and spawn_root_id is None
            and is_hidden
        ):
            continue
        sb = session.spawned_by_id if session is not None else None
        if spawned_by_id is not None and sb != spawned_by_id:
            continue
        sr = session.spawn_root_id if session is not None else None
        if spawn_root_id is not None and sr != spawn_root_id:
            continue
        filtered.append(row)
    rows = filtered

    data = [
        serialize_process_row(row, sessions_by_id.get(row.session_id))
        for row in rows
    ]

    sys.stdout.buffer.write(orjson.dumps(data, option=orjson.OPT_INDENT_2))
    sys.stdout.buffer.write(b"\n")
