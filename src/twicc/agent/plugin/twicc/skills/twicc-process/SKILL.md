---
name: twicc-process
description: Inspect or control the currently running process of a single TwiCC session by session ID. Default action shows the process state and OS PID; the `stop` sub-command kills the live agent attached to the session (equivalent to the UI's *Stop process* button). Use when the user wants details on the live process, an OS PID for external inspection (ps, debugger, etc.), or to stop a running agent.
argument-hint: <session_id> [stop]
---

# TwiCC Process

Inspect or control the live process attached to one session. Two flavours:

- `twicc process <SESSION_ID>` — shows the current `ProcessRun` row: state, last transition timestamp, OS PID. Default action.
- `twicc process <SESSION_ID> stop` — stops the live agent (same effect as the UI's *Stop process* button: `kill_agent(reason="manual")`).

## When to use

- The user has a session ID and wants to know whether (and where) its process is running → default action.
- The user wants the OS PID of a specific session's subprocess to attach external tools (debugger, `ps`, `/proc/<pid>/…`) → default action.
- The user is debugging a stuck session and wants to confirm the process is still alive → default action.
- The user asks to "stop / kill / interrupt the process for session X" (typically because it's stuck or runaway) → `stop` sub-command.

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

- **`id`** — internal `ProcessRun` row primary key
- **`provider`** — backend that owns the process: `"claude_code"` or `"codex"`
- **`session_id`** — the TwiCC session the process is bound to
- **`session_title`** — title of the bound session, or `null` if the session row has not been created yet by the file watcher (brand-new session, no JSONL line yet)
- **`project_id`** — the session's project
- **`state`** — one of four disjoint values:
  - `"starting"` — booting up
  - `"assistant_turn"` — actively generating
  - `"awaiting_user_input"` — blocked on a user click (tool approval, `AskUserQuestion`, Codex approval); the UI shows a pending dialog
  - `"user_turn"` — turn finished, awaiting the next user message
- **`started_at`** — when the row was created (= when the process was registered with the manager)
- **`last_state_change_at`** — last time the underlying state column was updated. Subtract from now to know how long the process has been in its current state
- **`pid`** — PID of the underlying provider subprocess (Claude Code SDK or Codex app-server). `null` only for the very brief window between row creation and the first state transition out of `starting`

## Related commands

- **List all running processes:** `twicc processes` — useful to find session IDs in the first place
- **Read the session metadata:** `twicc session <session_id>` — title, costs, message count, etc.
- **Read the session content:** `twicc session <session_id> content <line>` — actual conversation items

## How to present results

1. Lead with the session title and a human label for `state`:
   - `starting` — "spinning up"
   - `assistant_turn` — "currently working"
   - `awaiting_user_input` — "blocked waiting for your approval / answer" (the user has a pending dialog in the UI)
   - `user_turn` — "waiting for the next user message"
2. If `last_state_change_at` is more than a few minutes old and the state is `assistant_turn`, flag it: the process may be hung or slowly streaming a long response
3. Surface `pid` when the user asked about OS-level details (debugging, attaching tools)
4. You are in TwiCC, so you can link to the session using a relative Markdown link: `[link text](/project/{project_id}/session/{session_id})`
5. When the command returns a "no running process" error, suggest `twicc session <session_id>` (the session may still exist, just without a live process) or `twicc processes` (to see what is actually running)
