---
name: twicc-process
description: Inspect or control (stop or wait for state) the live process of a single TwiCC session. Use when you or the user want details on a live process, need to stop a running agent, or need to block a script until a session finishes.
argument-hint: <session_id> [stop | wait <status>...]
---

# TwiCC Process

Inspect or control the live process attached to one session. Three sub-commands:

- Default (no sub-command) — show current state, last transition, OS PID.
- `stop` — kill the live agent (same as the UI's *Stop process* button). Does not modify the session row.
- `wait <STATUS>... --timeout N` — block until the process reaches any of the listed states, or timeout.

## When to use

- You want to know whether a session's process is running and what state it's in.
- You need the OS PID to attach external tools.
- You want to stop a stuck or runaway agent.
- You want to block a script until a session finishes its turn (`wait user_turn`) or is fully dead (`wait dead`).
- You just triggered an action and want to observe the next state transition (`wait --transition`).

## How to invoke

TwiCC's executable varies by launch mode (uvx, dev, installed tool). ALWAYS USE THIS TO RESOLVE $TWICC AT THE START OF EACH BASH INVOCATION:

```bash
TWICC=${TWICC_BIN:-$(command -v twicc 2>/dev/null)}
[ -n "$TWICC" ] || { echo "TwiCC executable not found in this context" >&2; exit 1; }
```

Then run `$TWICC <args>` — **never quote `$TWICC`** (use `$TWICC args`, never `"$TWICC" args`): it may expand to multiple words, which quoting would break.

## Usage

### Inspect (default)

```bash
$TWICC process <SESSION_ID>
```

### Stop

```bash
$TWICC process <SESSION_ID> stop [--timeout N] [--json] [--no-color]
```

Idempotent — succeeds even if no live process is attached.

### Wait

```bash
$TWICC process <SESSION_ID> wait <STATUS>... --timeout N [--transition] [--json] [--no-color]
```

- `STATUS` — one or more of: `starting`, `assistant_turn`, `awaiting_user_input`, `user_turn`, `dead`.
- `--timeout N` — **required**. Exits 5 if no match before deadline.
- `--transition` — only match after at least one state change since the initial snapshot. Note: `wait dead --transition` on an already-dead session will always timeout.

## Errors (stop only)

### Local (exit 1)

- `is_subagent` — subagents cannot be stopped directly; target the parent session.
- `session_not_found`
- `session_stale`
- `project_no_directory`
- `unknown_provider`

### Server (exit 3)

Same codes, re-checked server-side.

## Output format

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

- `state` — `starting`, `assistant_turn`, `awaiting_user_input`, `user_turn`, or `dead` (`wait dead` only; default action exits 1 instead).
- `matched_state` — `wait` only. Which requested status matched.
- `id` — `null` in `wait dead` when no row exists.
- `session_title` — `null` if the session row doesn't exist yet.
- `pid` — `null` briefly after start, or in `wait dead` no-row case.

### Exit codes (default + stop)

- `0` — Success
- `1` — Local validation error
- `2` — TwiCC server not running
- `3` — Server rejected
- `4` — Server error
- `5` — Timeout
- `64` — Bad CLI usage

### Exit codes (wait)

- `0` — State matched
- `1` — Unknown session
- `2` — TwiCC not running or disappeared mid-wait
- `5` — Timeout
- `64` — Invalid arguments

## Examples

```bash
$TWICC process abc123-def456
$TWICC process abc123-def456 stop
$TWICC process --json abc123-def456 stop
# → {"status":"stopped","session_id":"...","provider":"claude_code","project_id":"...","request_uuid":"..."}
$TWICC process abc123 wait user_turn --timeout 600
$TWICC process abc123 wait user_turn dead --timeout 120
$TWICC process abc123 wait user_turn --transition --timeout 300
$TWICC process --json abc123 wait user_turn dead --timeout 600
```

## Related commands

- `$TWICC processes` — list all live processes; find session IDs. Skill: `twicc-processes`.
- `$TWICC session <session_id>` — session metadata (title, costs, etc.). Skill: `twicc-session`.
- `$TWICC update-session <session_id> archive` — archive + stop in one operation. Skill: `twicc-update-session`.

## How to present results

1. Lead with the session title and a human label for `state`:
   - `starting` → "spinning up"
   - `assistant_turn` → "currently working"
   - `awaiting_user_input` → "blocked — pending dialog in the UI"
   - `user_turn` → "waiting for the next message"
   - `dead` → "no live process" (only from `wait dead`)
2. If `assistant_turn` and `last_state_change_at` is several minutes old, flag it as potentially hung.
3. Surface `pid` only when the user asks about OS-level details.
4. You are in TwiCC — link to the session: `[link text](/project/{project_id}/session/{session_id})`.
5. On "no running process" error, suggest `$TWICC session <id>` (session may exist without a live process) or `$TWICC processes` (to see what's running).
