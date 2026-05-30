---
name: twicc-processes
description: List currently running processes of the live TwiCC backend, batch-look up the state of specific session_ids, batch-stop their live agents, or batch-wait until multiple session_ids reach a matching virtual state. Use when the user wants to see what's running, batch-check or wait on session states, batch-kill several agents at once, find a busy session, or locate an OS PID for external inspection.
---

# TwiCC Processes

List every process currently running inside the live TwiCC backend, with
its state (starting / user_turn / assistant_turn) and OS PID. Dead
processes are never returned — only rows that correspond to a live
worker the backend still has in memory.

## When to use

- The user asks what's currently running in TwiCC
- The user wants to know which sessions are busy (`assistant_turn`) vs idle (`user_turn`)
- The user wants to know which sessions need their attention right now (tool approval pending, `AskUserQuestion` open) — use `--state awaiting_user_input`
- The user needs an OS PID for external inspection
  (`ps`, `top`, attaching a debugger, reading `/proc/<pid>/…`)
- The user wants to wire an external notifier (desktop pop-up, Slack ping, …) that polls TwiCC for pending approvals — see "Polling for notifications" below
- The user (or a script the agent runs) needs the state of one or more *specific* session_ids — typically sessions the agent just spawned and wants to track — use `processes get <ID>...` instead of listing+filtering
- The user wants to stop the live agents of several sessions in one call — use `processes stop <ID>...` (idempotent: re-stopping a finished session still reports `stopped`; tolerant: unknown / subagent / stale IDs are reported with a per-id `skipped_*` status instead of failing the whole batch)
- The user (or a script) needs to block until several sessions reach a given state — e.g. wait for a batch of spawned tasks to all reach `user_turn`, or stop as soon as the first one needs attention — use `processes wait <SESSION_ID>... <STATUS>... --timeout N [--all | --first] [--transition]`

## How to invoke

TwiCC's executable varies by launch mode (uvx, dev, installed tool). Resolve it once at the start of each Bash invocation:

```bash
TWICC=${TWICC_BIN:-$(command -v twicc 2>/dev/null)}
[ -n "$TWICC" ] || { echo "TwiCC executable not found in this context" >&2; exit 1; }
```

Then run any subcommand via `$TWICC <args>` — **do NOT quote `$TWICC`** (use `$TWICC args`, never `"$TWICC" args`). The variable may expand to a multi-word command (e.g. `uv run --directory ... run.py`); Bash relies on word-splitting to parse it, and quoting it would treat the entire expansion as a single program name and fail with "No such file or directory". All bash examples below use the unquoted form.

## How to list processes

Run the `twicc processes` CLI command via the Bash tool:

```bash
$TWICC processes
```

The command reads the live TwiCC's PID from `<data_dir>/twicc.info.json`,
filters `ProcessRun` rows by that `twicc_pid`, and excludes any row whose
`state` is `dead`. If no TwiCC is running (info file missing or its PID
has died), the command exits with an error.

### Options

- `--provider PROV` — keep only processes from one backend (`claude_code`, `codex`)
- `--state STATE` — keep only processes in one state. Valid:
  - `starting` — booting up
  - `assistant_turn` — actively generating, NOT blocked
  - `awaiting_user_input` — blocked on a user click (tool approval, `AskUserQuestion`, Codex approval)
  - `user_turn` — turn finished, awaiting next user message

  `dead` is rejected (never returned anyway). The four values are **disjoint** — a row matches exactly one
- `--limit N` — max number of processes to return (default: 20)
- `--offset N` — skip first N processes for pagination (default: 0)
- `--include-hidden` — include processes whose bound session is hidden (default: false, hidden sessions' processes are excluded). Pass this flag to surface background processes running for hidden sessions.
- `--only-hidden` — return **only** processes whose bound session is hidden. Mutually exclusive with `--include-hidden`.
- `--spawned-by <ID|self>` — filter by spawner session ID. Only processes whose bound session has `spawned_by` set to the given ID are returned. The special value `self` resolves to the current session's own ID via PID ancestry (equivalent to `twicc whoami`) — useful for an agent tracking the background processes it spawned. **Implies `--include-hidden` by default**: a filiation query surfaces every matching child whatever its visibility (in practice spawned children are usually hidden). Combine with `--only-hidden` to narrow to hidden children.

### Examples

```bash
$TWICC processes                                    # All live processes (most recent first)
$TWICC processes --provider codex                   # Only Codex processes
$TWICC processes --provider claude_code             # Only Claude Code processes
$TWICC processes --state assistant_turn             # Actively generating (not blocked)
$TWICC processes --state awaiting_user_input        # Only processes that need a user click NOW
$TWICC processes --state user_turn                  # Turn finished, awaiting next user message
$TWICC processes --include-hidden                   # Include processes for hidden sessions alongside visible ones
$TWICC processes --only-hidden                      # Only processes whose session is hidden
$TWICC processes --spawned-by self                  # Processes for sessions spawned by the current session
$TWICC processes --spawned-by abc123-def456         # Processes for sessions spawned by a specific session
$TWICC processes --limit 50 --offset 20             # Paginate
```

### Polling for notifications

`--state awaiting_user_input` returns an array that is empty when nothing
needs the user's attention and non-empty otherwise — useful as the source
for an OS-level notification daemon:

```bash
# Run every N seconds, fire a notification when any process needs a click.
PENDING=$($TWICC processes --state awaiting_user_input)
COUNT=$(echo "$PENDING" | jq 'length')
[ "$COUNT" -gt 0 ] && notify-send "TwiCC" "$COUNT process(es) awaiting your input"
```

## How to look up specific session_ids

When you already know which sessions you care about, use the `get`
sub-command instead of listing + post-filtering. Each requested
session_id produces exactly one entry in the output, in the order you
passed them (duplicates collapsed, first occurrence wins):

```bash
$TWICC processes get <SESSION_ID> [<SESSION_ID>...]
```

Examples:

```bash
$TWICC processes get abc123-def456                       # Single session
$TWICC processes get abc123 def456 ghi789                # Batch lookup
```

Unlike `twicc processes`, `get` accepts **no filter flags** — when you
name the sessions you care about, layering `--provider` / `--state` on
top would only blur the meaning of the placeholder rows. Use `get` for
lookup, `processes` (no sub-command) for filtering.

### Output

A JSON array, one entry per session_id, in the order you passed them
(duplicates collapsed). Three shapes coexist:

```json
[
  {
    "id": 42,
    "provider": "claude_code",
    "session_id": "abc123-def456",
    "session_title": "Implement user authentication",
    "project_id": "-home-twidi-dev-myproject",
    "state": "user_turn",
    "session_known": true,
    "started_at": "2026-05-29T15:30:00+00:00",
    "last_state_change_at": "2026-05-29T15:45:12+00:00",
    "pid": 81287
  },
  {
    "id": null,
    "provider": "claude_code",
    "session_id": "def456-ghi789",
    "session_title": "Old session that finished",
    "project_id": "-home-twidi-dev-myproject",
    "state": "dead",
    "session_known": true,
    "started_at": null,
    "last_state_change_at": null,
    "pid": null
  },
  {
    "id": null,
    "provider": null,
    "session_id": "typo-or-unknown",
    "session_title": null,
    "project_id": null,
    "state": "dead",
    "session_known": false,
    "started_at": null,
    "last_state_change_at": null,
    "pid": null
  }
]
```

Reading the three shapes:

| `state`    | `session_known` | Meaning                                                                                                            |
|------------|-----------------|--------------------------------------------------------------------------------------------------------------------|
| live value | `true`          | Session is alive and its agent process is running — fields are the same as in the listing                          |
| `"dead"`   | `true`          | TwiCC knows the session (a `Session` row OR a prior `ProcessRun`) but no live process is currently attached        |
| `"dead"`   | `false`         | Typo, periodic cleanup, or a session_id that never existed in this TwiCC's view — `provider` / `session_title` null |

Because the output is 1-to-1 with the input order, callers can `zip` it
with the input list without any re-mapping:

```python
import json, subprocess
ids = ["abc...", "def...", "ghi..."]
out = json.loads(subprocess.check_output([twicc, "processes", "get", *ids]))
for sid, entry in zip(ids, out):
    if not entry["session_known"]:
        print(f"  WARN: {sid} unknown to TwiCC")
    elif entry["state"] == "dead":
        print(f"  {sid}: stopped")
    else:
        print(f"  {sid}: {entry['state']}")
```

## How to batch-stop sessions

When you want to kill the live agent of several sessions in one call,
use the `stop` sub-command:

```bash
$TWICC processes stop <SESSION_ID> [<SESSION_ID>...] [--timeout N]
```

Examples:

```bash
$TWICC processes stop abc123-def456                       # Stop one
$TWICC processes stop abc123 def456 ghi789                # Stop several
$TWICC processes stop abc123 def456 --timeout 60          # Custom global timeout
```

Each `session_id` becomes a `kind="process:stop"` drop request that the
server dispatches to the same code path as `twicc process <ID> stop` and
the UI's *Stop process* button (with `reason="manual"`), so the
operation is **fully idempotent** — re-stopping a session whose process
is already gone still reports `status="stopped"`.

Each `session_id` is also **pre-checked locally** before being submitted.
IDs that fail the pre-check (unknown, subagent, stale, no project
directory, unknown provider) get a `skipped_*` status with no drop
emitted — they don't consume any of the timeout budget. The server
processes valid drops in parallel, so `--timeout` (default 30 s) is a
wall-clock budget for the whole batch, not 30 s × N.

### Output

A JSON array, one entry per session_id, in the order you passed them
(duplicates collapsed, first occurrence wins). All entries share the
same shape:

```json
[
  {
    "session_id": "abc123-def456",
    "session_known": true,
    "status": "stopped",
    "request_uuid": "4a8352fb-1674-41c0-8a85-0a5a3e4e623a",
    "provider": "claude_code",
    "session_title": "Implement user authentication",
    "project_id": "-home-twidi-dev-myproject",
    "error": null
  },
  {
    "session_id": "typo-or-unknown",
    "session_known": false,
    "status": "skipped_unknown",
    "request_uuid": null,
    "provider": null,
    "session_title": null,
    "project_id": null,
    "error": "Session 'typo-or-unknown' not found"
  },
  {
    "session_id": "subagent-id",
    "session_known": true,
    "status": "skipped_subagent",
    "request_uuid": null,
    "provider": "claude_code",
    "session_title": "child task",
    "project_id": "-home-twidi-dev-myproject",
    "error": "Session 'subagent-id' is a subagent; subagents cannot be messaged or updated directly. Target the parent session instead."
  }
]
```

#### Possible `status` values

| Value                       | Meaning                                                                                              |
|-----------------------------|------------------------------------------------------------------------------------------------------|
| `stopped`                   | Server confirmed the kill (or no live process — idempotent)                                          |
| `rejected`                  | Server refused the request — see `error` for the codes / messages joined                             |
| `failed`                    | Server hit an unexpected error mid-flight — see `error`                                              |
| `timeout`                   | No final status received within `--timeout` seconds                                                  |
| `skipped_unknown`           | No `Session` row for this id — typo or never existed in this TwiCC's view                            |
| `skipped_subagent`          | The session is a subagent; subagents cannot be stopped directly — target the parent                  |
| `skipped_stale`             | The session's JSONL file is gone — stopping is meaningless                                           |
| `skipped_no_directory`      | The session's project has no directory configured                                                    |
| `skipped_unknown_provider`  | The session's provider value is no longer recognized                                                 |

#### Exit codes

- `0` — command ran to completion; inspect each entry's `status` for the per-id outcome
- `1` — local CLI validation error (e.g. `--timeout <= 0`)
- `2` — TwiCC server is not running
- `64` — bad CLI usage (handled by Typer)

#### Scripting

Because the output is 1-to-1 with the input order, callers can `zip`
the two and react per-id without re-mapping:

```python
import json, subprocess
ids = ["abc...", "def...", "ghi..."]
out = json.loads(subprocess.check_output([twicc, "processes", "stop", *ids]))
for sid, entry in zip(ids, out):
    if entry["status"] == "stopped":
        print(f"  ✓ {sid} stopped")
    elif entry["status"].startswith("skipped_"):
        print(f"  - {sid} skipped: {entry['status']}")
    else:
        print(f"  ✗ {sid}: {entry['status']} — {entry.get('error')}")
```

## How to wait for multiple sessions to reach a state

When you need to block until several sessions reach one of a set of
virtual states (e.g. "wait for these three tasks to all finish, or stop
as soon as one needs attention"), use the `wait` sub-command:

```bash
$TWICC processes wait <SESSION_ID> [<SESSION_ID>...] <STATUS> [<STATUS>...] \
    --timeout N [--all | --first] [--transition]
```

The items are **a single positional list, auto-discriminated by value**:
any item whose value is one of the five virtual states (`starting`,
`assistant_turn`, `awaiting_user_input`, `user_turn`, `dead`) is treated
as a status; everything else is a session_id. By convention pass
session_ids first and statuses last — a typo in a session_id then falls
through cleanly as a session_id (and becomes `skipped_unknown`) instead
of being silently absorbed as a status.

Flags:

- `--all` (default) — wait until **every** active session_id has matched
  at least one of the requested statuses.
- `--first` — stop as soon as **one** active session_id has matched.
- `--transition` — only evaluate a match after observing at least one
  state transition per session_id since the initial snapshot. Useful to
  wait for the NEXT change rather than the current value.
- `--timeout N` (**required**) — wall-clock budget for the whole batch.
  All session_ids are polled in parallel server-side, so this is not
  `N × number_of_ids`. Exit 5 on timeout.

Unknown session_ids (no `Session` row AND no `ProcessRun` for this
TwiCC) are silently dropped from the wait pool with
`wait_status="skipped_unknown"`. They do NOT participate in `--all` /
`--first`. If every session_id is unknown the wait pool is empty and
the command exits 0 immediately (vacuous truth — nothing to wait for).

Examples:

```bash
# Wait until both sessions finish their current turn (or die)
$TWICC processes wait abc123 def456 user_turn dead --timeout 300

# Stop as soon as one of three needs the user's attention
$TWICC processes wait abc123 def456 ghi789 awaiting_user_input --first --timeout 120

# Wait for both to transition INTO user_turn — ignore current state
$TWICC processes wait abc123 def456 user_turn --transition --timeout 600
```

### Output

A JSON array, one entry per session_id, in input order (duplicates
collapsed, first occurrence wins). All entries share the same shape:

```json
[
  {
    "session_id": "abc123-def456",
    "session_known": true,
    "wait_status": "matched",
    "matched_state": "user_turn",
    "current_state": "user_turn",
    "provider": "claude_code",
    "session_title": "Implement user authentication",
    "project_id": "-home-twidi-dev-myproject"
  },
  {
    "session_id": "def456-ghi789",
    "session_known": true,
    "wait_status": "pending",
    "matched_state": null,
    "current_state": "assistant_turn",
    "provider": "claude_code",
    "session_title": "Other ongoing work",
    "project_id": "-home-twidi-dev-myproject"
  },
  {
    "session_id": "typo-xyz",
    "session_known": false,
    "wait_status": "skipped_unknown",
    "matched_state": null,
    "current_state": "dead",
    "provider": null,
    "session_title": null,
    "project_id": null
  }
]
```

#### Possible `wait_status` values

| Value              | Meaning                                                                                                |
|--------------------|--------------------------------------------------------------------------------------------------------|
| `matched`          | This session_id reached one of the requested statuses (see `matched_state` for which one specifically) |
| `pending`          | Did not reach any requested status before we stopped (timeout OR `--first` triggered on another id)    |
| `skipped_unknown`  | No trace of this session_id in this TwiCC's view — typo, periodic cleanup, or never existed            |

Note: with `--first`, once one active session matches we stop polling
the others, so they keep their `wait_status="pending"` even though we
did not really "fail" — we just didn't bother to wait further.

#### Exit codes

- `0` — condition satisfied (`--all` met, `--first` triggered, or wait pool was empty)
- `1` — local CLI validation error (no statuses given, no session_ids given, `--timeout <= 0`)
- `2` — TwiCC server is not running
- `5` — timeout: at least one active session_id never matched (`--all`) or none matched (`--first`)
- `64` — bad CLI usage (handled by Typer)

## Output format

The command outputs a JSON array of process objects, ordered by `started_at`
(most recent first):

```json
[
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
]
```

### Key fields

- **`id`** — internal `ProcessRun` row primary key
- **`provider`** — backend that owns the process: `"claude_code"` or `"codex"`
- **`session_id`** — the TwiCC session the process is bound to (pass to `twicc process <session_id>` for a focused view, or `twicc session <session_id>` for the session metadata)
- **`session_title`** — title of the bound session, or `null` if the session row has not been created yet by the file watcher (brand-new session, no JSONL line yet)
- **`project_id`** — the session's project
- **`state`** — one of four disjoint values:
  - `"starting"` — booting up
  - `"assistant_turn"` — actively generating
  - `"awaiting_user_input"` — blocked on a user click (tool approval, `AskUserQuestion`, Codex approval); the UI shows a pending dialog
  - `"user_turn"` — turn finished, awaiting the next user message

  Internally, `awaiting_user_input` is a separate boolean on top of `assistant_turn`; the CLI flattens it into a single field because the four buckets are mutually exclusive in practice
- **`started_at`** — when the row was created (= when the process was registered with the manager)
- **`last_state_change_at`** — last time the underlying state column was updated
- **`pid`** — PID of the underlying provider subprocess (Claude Code SDK or Codex app-server). `null` only for the very brief window between row creation and the first state transition out of `starting`

## Related commands

- **Inspect a single running process (errors out if dead):** `twicc process <session_id>` — same data scoped to one session, with a clear error (exit 1) when the session has no live process. Use when "no process" should be treated as a failure.
- **Wait for a single process to reach a state:** `twicc process <session_id> wait <STATUS>... --timeout N` — block until the live process matches; see the `twicc-process` skill. For batch-waiting on multiple sessions in one call, see `processes wait` above.
- **Stop a single live process:** `twicc process <session_id> stop` — kill the agent attached to one session, with a clear final status (no skipped_*). For batch-stopping multiple sessions in one call, see `processes stop` above.
- **Read the session metadata:** `twicc session <session_id>` — title, costs, etc.
- **Browse all sessions:** `twicc sessions` — also includes stopped sessions, broader scope than running processes

## How to present results

1. Group by `provider` if multiple backends are running; one section per provider
2. For each process: show the session title (or session_id if title is null), the state, and how long ago `last_state_change_at` was
3. Map `state` to a human label when rendering:
   - `starting` → "spinning up"
   - `assistant_turn` → "currently working"
   - `awaiting_user_input` → "waiting for your approval / answer" (the user has a pending dialog in the UI)
   - `user_turn` → "idle — awaiting next user message"
4. Include `pid` when the user asked about OS-level details (debugging, attaching tools)
5. You are in TwiCC, so you can link to a session using a relative Markdown link so the user can click it: `[link text](/project/{project_id}/session/{session_id})`
6. If the list is empty, say "no processes are currently running in the live TwiCC backend" rather than implying TwiCC itself is down
7. For `processes get` output: scan for `session_known: false` entries (surface as "session X is unknown to TwiCC — typo or already cleaned up") and for `state: "dead"` with `session_known: true` (surface as "session X has no live process — it finished, was stopped, or crashed"). Live entries render like the listing
8. For `processes stop` output: bucket entries by `status` and surface the counts (e.g. "3 stopped, 1 skipped (unknown), 1 timeout"). For non-`stopped` entries, include the `error` field when present. Don't render `error` for `stopped` entries — it's always null. If every entry is `stopped`, a single line is enough ("stopped 3 sessions")
9. For `processes wait` output: surface (a) any `matched` entries with their `matched_state` (which status actually fired), (b) any `pending` entries with their `current_state` (so the user sees what they were stuck on), (c) any `skipped_unknown` entries grouped separately. If the command exited 5 (timeout), call that out explicitly; if exited 0 with all pending (only possible when `--first` triggered), explain which id triggered first
