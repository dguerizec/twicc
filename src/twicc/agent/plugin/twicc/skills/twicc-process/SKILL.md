---
name: twicc-process
description: Inspect the currently running process of a single TwiCC session by session ID — its state and OS PID. Use when the user wants details on the live process of one session, or wants to find the OS PID attached to a session for external inspection (ps, attach a debugger, etc.).
argument-hint: <session_id>
---

# TwiCC Process

Show the currently running process (live `ProcessRun`) for one session:
its state, when it last transitioned, and the OS PID of the underlying
provider subprocess. Returns nothing when the session has no live
process (stopped, never started, or kept around only as a DEAD row for
cron restart).

## When to use

- The user has a session ID and wants to know whether (and where) its process is running
- The user wants the OS PID of a specific session's subprocess to attach external tools (debugger, `ps`, `/proc/<pid>/…`)
- The user is debugging a stuck session and wants to confirm the process is still alive

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
