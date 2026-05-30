---
name: twicc-process
description: Inspect or control the currently running process of a single TwiCC session by session ID. Default action shows the process state and OS PID; the `stop` sub-command kills the live agent attached to the session (equivalent to the UI's *Stop process* button); the `wait` sub-command blocks until the process reaches one of the listed states (`starting`, `assistant_turn`, `awaiting_user_input`, `user_turn`, or `dead`). Use when the user wants details on the live process, an OS PID for external inspection (ps, debugger, etc.), to stop a running agent, or to block a script until the session finishes its turn or dies.
argument-hint: <session_id> [stop | wait <STATUS>...]
---

# TwiCC Process

Inspect or control the live process attached to one session. Three flavours:

- `twicc process <SESSION_ID>` — shows the current `ProcessRun` row: state, last transition timestamp, OS PID. Default action.
- `twicc process <SESSION_ID> stop` — stops the live agent (same effect as the UI's *Stop process* button: `kill_agent(reason="manual")`).
- `twicc process <SESSION_ID> wait <STATUS> [<STATUS> ...] --timeout SECONDS` — blocks until the live process reaches any of the listed virtual states (or timeout). `dead` matches when no live `ProcessRun` exists.

## When to use

- The user has a session ID and wants to know whether (and where) its process is running → default action.
- The user wants the OS PID of a specific session's subprocess to attach external tools (debugger, `ps`, `/proc/<pid>/…`) → default action.
- The user is debugging a stuck session and wants to confirm the process is still alive → default action.
- The user asks to "stop / kill / interrupt the process for session X" (typically because it's stuck or runaway) → `stop` sub-command.
- The user wants to block a script until a session finishes its current turn before running follow-up logic → `wait user_turn`.
- The user wants to wait until a session is fully dead (cleanup script, teardown verification) → `wait dead`.
- The user has triggered an action (`stop`, `send-message`) and wants to observe the next state transition rather than the current value → `wait <STATE> --transition`.

## How to invoke

TwiCC's executable varies by launch mode (uvx, dev, installed tool). Resolve it once at the start of each Bash invocation:

```bash
TWICC=${TWICC_BIN:-$(command -v twicc 2>/dev/null)}
[ -n "$TWICC" ] || { echo "TwiCC executable not found in this context" >&2; exit 1; }
```

Then run any subcommand via `$TWICC <args>` — **do NOT quote `$TWICC`** (use `$TWICC args`, never `"$TWICC" args`). The variable may expand to a multi-word command (e.g. `uv run --directory ... run.py`); Bash relies on word-splitting to parse it, and quoting it would treat the entire expansion as a single program name and fail with "No such file or directory". All bash examples below use the unquoted form.

## How to inspect a process

```bash
$TWICC process <SESSION_ID>
```

The command reads the live TwiCC's PID from `<data_dir>/twicc.info.json`
and returns the `ProcessRun` row whose `twicc_pid` matches AND whose
`state` is not `dead`. Exits with status 1 with a descriptive error
when:

- No TwiCC is currently running (info file missing or recorded PID dead)
- The session has no live process (was never started in this TwiCC, was stopped, or is kept only as a DEAD row for the boot cron restart)

### Examples

```bash
$TWICC process abc123-def456
```

## How to stop a process

```bash
$TWICC process <SESSION_ID> stop [OPTIONS]
```

Drops a request the live TwiCC server picks up; on success, the same teardown the UI's *Stop process* button triggers happens — `kill_agent(reason="manual")` against the provider's manager. The session row itself is **not** modified (no archive, no settings change, no title change).

### Required argument

- **`SESSION_ID`** — the session whose live agent should be killed.

### Options

Same standard output controls as the other CLI commands:

| Flag | |
|------|---|
| `--timeout SECONDS` | seconds to wait for the server's final status (default 30) |
| `--json` | emit a single JSON object on stdout (implies `--no-color`) |
| `--no-color` | disable ANSI colors |

### Behaviour

- **Idempotent.** Calling `stop` when no live agent is attached to the session still exits 0 with `status="stopped"`. The UI's *Stop process* button works the same way.
- **No effect on the row.** `archived`, `pinned`, `title`, agent settings, and every other column on `Session` are untouched. If you want to archive as well (which also stops the agent), use `twicc update-session <ID> archive` instead.
- **Subagent guard.** Subagents have no live process attached to the manager; `stop` rejects them locally with `is_subagent` (exit 1).

### Exit codes

| Code | Meaning |
|------|---------|
| 0 | Process stopped (or already not running) |
| 1 | Local validation error (`is_subagent`, `session_not_found`, `session_stale`, `project_no_directory`, ...) |
| 2 | TwiCC server not running (heartbeat missing or stale) |
| 3 | Server rejected the request (`provider_disabled`, race-detected guards, ...) |
| 4 | Server hit an unexpected error mid-flight |
| 5 | Timeout waiting for the server's final status |
| 64 | Bad CLI usage (handled by Typer) |

### Examples

```bash
# Stop a stuck process.
$TWICC process abc123-def456 stop

# Stop + JSON for scripts.
$TWICC process --json abc123-def456 stop
# → {"status":"stopped","session_id":"...","provider":"claude_code","project_id":"...","request_uuid":"..."}
```

## How to wait for a process state

```bash
$TWICC process <SESSION_ID> wait <STATUS> [<STATUS> ...] --timeout SECONDS [OPTIONS]
```

Blocks until the session's live process reaches **any** of the listed virtual states, or until `--timeout` elapses. Pure local observation — no server request, no side effect on the session.

### Required arguments

- **`SESSION_ID`** — the session whose process to observe.
- **`STATUS...`** — one or more virtual states (positional, any-of match). Valid values:
  - `starting` — agent booting up.
  - `assistant_turn` — actively generating.
  - `awaiting_user_input` — blocked on a user click (tool approval, AskUserQuestion, Codex approval).
  - `user_turn` — turn finished, awaiting the next user message.
  - `dead` — **no live `ProcessRun` exists** for this session on the current TwiCC (the row is `state=DEAD` or there is no row at all).

### Required option

- **`--timeout SECONDS`** — float > 0. **Required** (no default). Pass `--timeout=N` explicitly to bound the wait; the command exits 5 if no status matches before the deadline.

### Other options

| Flag | |
|------|---|
| `--transition` | Only evaluate the match **after** observing at least one state transition since the initial snapshot. Without this flag, if the current state is already in the requested list, the command returns immediately. |
| `--json` | Emit only the final JSON object on stdout (no progress lines). Implies `--no-color`. |
| `--no-color` | Disable ANSI colors in progress lines. |

### Behaviour

- **Polling.** Reads the DB every 250 ms; the live TwiCC writes process transitions to the same row this command reads. Latency is bounded by the poll interval.
- **Immediate match (normal mode).** If the current state is already in the requested set at startup, the command emits the match and exits 0 immediately. No artificial delay.
- **`--transition` trap with `dead`.** `wait dead --transition` on an already-dead session **can never match**: the DEAD row (or its absence) is frozen and will never produce a transition. The command will always timeout in that case. To verify a session is dead, use `wait dead` without `--transition`.
- **Server vanishes mid-wait.** If TwiCC stops (or restarts with a different PID) while the command is polling, it exits 2 immediately — the observation is no longer reliable.
- **Session validation.** The command rejects unknown session IDs (no `Session` row **and** no `ProcessRun` row in this TwiCC) with exit 1, to avoid silently matching `dead` on a typo'd ID.

### Exit codes

| Code | Meaning |
|------|---------|
| 0 | One of the requested statuses matched |
| 1 | Local validation error (`unknown session`, etc.) |
| 2 | TwiCC server not running (info file missing, PID dead, or disappeared mid-wait), **or** typer-level CLI usage error (missing `--timeout`, wrong argument type, …) |
| 5 | Timeout reached without a match |
| 64 | Application-level CLI validation (invalid status value, `--timeout <= 0`, empty status list) |

### Examples

```bash
# Block until the agent has finished its current turn (returns immediately
# if already at user_turn).
$TWICC process abc123 wait user_turn --timeout 600

# Wait until the agent finishes OR the process is killed/dies.
$TWICC process abc123 wait user_turn dead --timeout 120

# After kicking the agent: wait for the NEXT user_turn (don't immediately
# return on the current one).
$TWICC process abc123 wait user_turn --transition --timeout 300

# Wait for full teardown (cleanup script).
$TWICC process abc123 wait dead --timeout 60

# JSON mode — single object on stdout, suitable for jq / scripts.
$TWICC process --json abc123 wait user_turn dead --timeout 600
# → {"id":42,"provider":"claude_code","session_id":"abc123","state":"user_turn","matched_state":"user_turn",...}
```

## Output format

Returns a single JSON object:

```json
{
  "id": 42,
  "provider": "claude_code",
  "session_id": "abc123-def456",
  "session_title": "Implement user authentication",
  "project_id": "-home-twidi-dev-myproject",
  "state": "awaiting_user_input",
  "started_at": "2026-05-29T15:30:00+00:00",
  "last_state_change_at": "2026-05-29T15:45:12+00:00",
  "pid": 81287
}
```

### Fields

- **`id`** — internal `ProcessRun` row primary key. `null` only in `wait dead` matches where no row exists at all for the session.
- **`provider`** — backend that owns the process: `"claude_code"` or `"codex"`. In the `wait dead` no-row case, falls back to the `Session` row's provider; only `null` if neither row nor session exist (cannot happen in practice — the validation step requires one of them).
- **`session_id`** — the TwiCC session the process is bound to
- **`session_title`** — title of the bound session, or `null` if the session row has not been created yet by the file watcher (brand-new session, no JSONL line yet)
- **`project_id`** — the session's project, or `null` if no session row exists yet
- **`state`** — one of five disjoint values:
  - `"starting"` — booting up
  - `"assistant_turn"` — actively generating
  - `"awaiting_user_input"` — blocked on a user click (tool approval, `AskUserQuestion`, Codex approval); the UI shows a pending dialog
  - `"user_turn"` — turn finished, awaiting the next user message
  - `"dead"` — **only emitted by `wait dead` matches.** Default action (`twicc process <ID>`) never returns this; it exits 1 with an error instead. Means no live `ProcessRun` exists.
- **`matched_state`** — **`wait` only.** The element of the requested status list that satisfied the wait (= `state` in most cases, but distinct when multiple statuses were requested).
- **`started_at`** — when the row was created (= when the process was registered with the manager). `null` in the `wait dead` no-row case.
- **`last_state_change_at`** — last time the underlying state column was updated. Subtract from now to know how long the process has been in its current state. `null` in the `wait dead` no-row case.
- **`pid`** — PID of the underlying provider subprocess (Claude Code SDK or Codex app-server). `null` for the brief window between row creation and the first state transition out of `starting`, **or** in the `wait dead` no-row case.

## Related commands

- **List all running processes:** `twicc processes` — useful to find session IDs in the first place
- **Read the session metadata:** `twicc session <session_id>` — title, costs, message count, etc.
- **Read the session content:** `twicc session <session_id> content <line>` — actual conversation items
- **Wait for a state from a script:** `twicc process <session_id> wait <STATUS>... --timeout N` — block until the process reaches one of the listed states

## How to present results

1. Lead with the session title and a human label for `state`:
   - `starting` — "spinning up"
   - `assistant_turn` — "currently working"
   - `awaiting_user_input` — "blocked waiting for your approval / answer" (the user has a pending dialog in the UI)
   - `user_turn` — "waiting for the next user message"
   - `dead` — "no live process for this session" (only seen from `wait dead`)
2. If `last_state_change_at` is more than a few minutes old and the state is `assistant_turn`, flag it: the process may be hung or slowly streaming a long response
3. Surface `pid` when the user asked about OS-level details (debugging, attaching tools)
4. You are in TwiCC, so you can link to the session using a relative Markdown link: `[link text](/project/{project_id}/session/{session_id})`
5. When the default-action command returns a "no running process" error, suggest `twicc session <session_id>` (the session may still exist, just without a live process), `twicc processes` (to see what is actually running), or `twicc process <session_id> wait dead --timeout N` (to confirm the absence is stable)
6. For `wait` matches, also report which state matched (`matched_state`) when multiple statuses were requested, and how long the wait took (compute from `last_state_change_at` vs `started_at` is misleading — use the human-mode `✓ Matched ... after Xs` line if available)
