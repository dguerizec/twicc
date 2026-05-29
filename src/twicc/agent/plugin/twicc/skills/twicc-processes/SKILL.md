---
name: twicc-processes
description: List currently running processes of the live TwiCC backend, with their OS PIDs and state. Use when the user wants to see what's running right now, find a busy session, or locate an OS PID for external inspection (ps, attach a debugger, etc.).
---

# TwiCC Processes

List every process currently running inside the live TwiCC backend, with
its state (starting / user_turn / assistant_turn) and OS PID. Dead
processes are never returned — only rows that correspond to a live
worker the backend still has in memory.

## When to use

- The user asks what's currently running in TwiCC
- The user wants to know which sessions are busy (`assistant_turn`) vs idle (`user_turn`)
- The user needs an OS PID for external inspection
  (`ps`, `top`, attaching a debugger, reading `/proc/<pid>/…`)

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
- `--limit N` — max number of processes to return (default: 20)
- `--offset N` — skip first N processes for pagination (default: 0)

### Examples

```bash
$TWICC processes                              # All live processes (most recent first)
$TWICC processes --provider codex             # Only Codex processes
$TWICC processes --provider claude_code       # Only Claude Code processes
$TWICC processes --limit 50 --offset 20       # Paginate
```

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
    "state": "assistant_turn",
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
- **`state`** — runtime state, one of `"starting"`, `"assistant_turn"`, `"user_turn"`. Never `"dead"` here by construction
- **`started_at`** — when the row was created (= when the process was registered with the manager)
- **`last_state_change_at`** — last time the state column was updated
- **`pid`** — PID of the underlying provider subprocess (Claude Code SDK or Codex app-server). `null` only for the very brief window between row creation and the first state transition out of `starting`

## Related commands

- **Inspect a single running process:** `twicc process <session_id>` — same data scoped to one session, with a clear error when the session has no live process
- **Read the session metadata:** `twicc session <session_id>` — title, costs, etc.
- **Browse all sessions:** `twicc sessions` — also includes stopped sessions, broader scope than running processes

## How to present results

1. Group by `provider` if multiple backends are running; one section per provider
2. For each process: show the session title (or session_id if title is null), the state, and how long ago `last_state_change_at` was
3. Include `pid` when the user asked about OS-level details (debugging, attaching tools)
4. You are in TwiCC, so you can link to a session using a relative Markdown link so the user can click it: `[link text](/project/{project_id}/session/{session_id})`
5. If the list is empty, say "no processes are currently running in the live TwiCC backend" rather than implying TwiCC itself is down
