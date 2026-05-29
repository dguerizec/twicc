---
name: twicc-status
description: Report the live TwiCC backend's status (running / starting / stale / dead_pid / not_running) based on the sidecar info file and the heartbeat file. Use when the user wants to know whether TwiCC is up, why a CLI command fails with "TwiCC server is not running", or to grab the live PID/port from a shell script.
---

# TwiCC Status

Inspect the two sidecar files written by the live TwiCC backend
(`twicc.info.json` + `.server-heartbeat`) and the kernel process table
to classify the backend into one of five distinct states. No Django,
no lock acquisition — pure file reads, safe to call concurrently with
a live TwiCC.

## When to use

- The user asks whether TwiCC is running
- A `twicc <subcommand>` failed with "TwiCC is not running" and you want to confirm before suggesting a restart
- The user wants the live PID or port (e.g. to attach a debugger, hit the HTTP endpoint, check `lsof`)
- The user reports the UI is unresponsive and you want to distinguish "process dead" from "server hung" from "still booting"
- A shell script needs to gate work on TwiCC being up (exit code 0 means fully running)

## How to invoke

TwiCC's executable varies by launch mode (uvx, dev, installed tool). Resolve it once at the start of each Bash invocation:

```bash
TWICC=${TWICC_BIN:-$(command -v twicc 2>/dev/null)}
[ -n "$TWICC" ] || { echo "TwiCC executable not found in this context" >&2; exit 1; }
```

Then run any subcommand via `$TWICC <args>` — **do NOT quote `$TWICC`** (use `$TWICC args`, never `"$TWICC" args`). The variable may expand to a multi-word command (e.g. `uv run --directory ... run.py`); Bash relies on word-splitting to parse it, and quoting it would treat the entire expansion as a single program name and fail with "No such file or directory". All bash examples below use the unquoted form.

## How to check status

```bash
$TWICC status
```

No options. The command always prints a JSON object to stdout — even on failure — so you can parse it the same way regardless of the outcome.

## Output format

```json
{
  "status": "running",
  "data_dir": "/home/twidi/.twicc",
  "lock_path": "/home/twidi/.twicc/twicc.lock",
  "info_path": "/home/twidi/.twicc/twicc.info.json",
  "heartbeat_path": "/home/twidi/.twicc/.server-heartbeat",
  "pid": 12345,
  "port": 3500,
  "started_at": "2026-05-29T15:30:00+00:00",
  "heartbeat_age_seconds": 2.3,
  "heartbeat_stale_after_seconds": 15
}
```

### Fields

- **`status`** — one of `"running"`, `"starting"`, `"stale"`, `"dead_pid"`, `"not_running"` (see next section)
- **`data_dir`** — resolved data directory (respects `TWICC_DATA_DIR` and worktree overrides)
- **`lock_path`** / **`info_path`** / **`heartbeat_path`** — absolute paths to the three state files, useful for `stat`, `ls -la`, manual cleanup
- **`pid`** — backend process PID, or `null` when `status == "not_running"` or the recorded PID isn't an integer
- **`port`** — HTTP port the backend bound to, or `null` when unknown
- **`started_at`** — ISO 8601 timestamp the lock was acquired, or `null`
- **`heartbeat_age_seconds`** — seconds since the last heartbeat touch, or `null` if the heartbeat file is missing
- **`heartbeat_stale_after_seconds`** — staleness threshold (currently 15 s, ≈ 3× the period)

## Status values

| Status | Meaning | Typical cause |
|---|---|---|
| **`running`** | Lock holder is alive, heartbeat fresh — backend is fully ready to serve. | Healthy. |
| **`starting`** | `twicc.info.json` is written and the PID is alive, but no heartbeat file yet. | Backend is mid-boot (running migrations, initial sync) — the heartbeat task only starts after `migrate`. |
| **`stale`** | Heartbeat file exists but its mtime is older than the staleness threshold. | Backend is hung (GC pause, deadlock, kernel-level pressure) — process alive but loop blocked. |
| **`dead_pid`** | `twicc.info.json` exists, the recorded PID is *not* alive. | Crash / SIGKILL without graceful shutdown — the info file was orphaned (the lock itself is automatically released by the kernel). |
| **`not_running`** | `twicc.info.json` is missing. | Backend was never started in this data directory, or shut down cleanly. |

## Exit codes

- `0` — `status == "running"` (and only then)
- `1` — any other status (use the `status` field to disambiguate)

This makes shell gating straightforward:

```bash
$TWICC status >/dev/null && echo "TwiCC is up" || echo "TwiCC is down"
```

## Related commands

- **List running processes:** `twicc processes` — what's running *inside* the backend (only meaningful when status is `running`)
- **Inspect a session's process:** `twicc process <session_id>`
- **Trigger a session creation:** `twicc create-session ...` — fails fast when status is not `running`

## How to present results

1. Lead with the single most relevant fact: "TwiCC is running" / "TwiCC is starting up" / "TwiCC is not running"
2. For `running`: mention port + PID + how long the backend has been up (compute from `started_at`)
3. For `starting`: tell the user to wait a few seconds and re-check — booting usually takes < 10 s but initial sync on a large `~/.claude/projects/` can stretch that
4. For `stale`: warn that the backend looks hung and suggest looking at `<data_dir>/logs/backend.log`
5. For `dead_pid`: explain the backend crashed; suggest the user removes the orphaned `twicc.info.json` (path is in the output) and restarts. The lockfile itself does not need cleanup — the kernel released the flock when the process died
6. For `not_running`: tell the user to run `twicc` in another terminal
7. Surface the `heartbeat_age_seconds` only when it's meaningful (i.e. for `stale`, to quantify the hang)
