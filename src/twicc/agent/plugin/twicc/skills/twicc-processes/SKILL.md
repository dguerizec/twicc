---
name: twicc-processes
description: List live TwiCC processes, batch-look up states for specific session_ids, batch-stop agents, or batch-wait until sessions reach a given state. Use when you or the user want to see what's running, track spawned sessions, batch-stop agents, or gate a script on multiple sessions finishing.
---

# TwiCC Processes

Four modes: list all live processes, batch-look up states (`get`), batch-stop agents (`stop`), or batch-wait on states (`wait`).

## When to use

- You or the user want to see what's currently running in TwiCC.
- You need to check which sessions are busy, idle, or blocked on user input.
- You have a set of session_ids and want to batch-fetch their process state — use `get`.
- You want to stop multiple agents in one call — use `stop` (idempotent, tolerant of unknown/subagent/stale ids).
- You want to block a script until several sessions finish or one needs attention — use `wait`.

## How to invoke

TwiCC's executable varies by launch mode (uvx, dev, installed tool). ALWAYS USE THIS TO RESOLVE $TWICC AT THE START OF EACH BASH INVOCATION:

```bash
TWICC=${TWICC_BIN:-$(command -v twicc 2>/dev/null)}
[ -n "$TWICC" ] || { echo "TwiCC executable not found in this context" >&2; exit 1; }
```

Then run `$TWICC <args>` — **never quote `$TWICC`** (use `$TWICC args`, never `"$TWICC" args`): it may expand to multiple words, which quoting would break.

## Usage

### List (default)

```bash
$TWICC processes [OPTIONS]
```

- `--provider claude_code|codex` — filter by provider.
- `--state STATE` — filter by state: `starting`, `assistant_turn`, `awaiting_user_input`, `user_turn`. (`dead` is rejected — never returned.)
- `--limit N` — max results (default: 20).
- `--offset N` — skip first N for pagination (default: 0).
- `--include-hidden` — include processes for hidden sessions (excluded by default).
- `--only-hidden` — only processes for hidden sessions. Mutually exclusive with `--include-hidden`.
- `--spawned-by <ID|self|parent>` — filter to direct child sessions spawned by the given session ID. `self` is the current session (= my children); `parent` is the session that spawned the current one (= my siblings, myself included). Implies `--include-hidden`. Mutually exclusive with `--spawn-root` and `--descendants`.
- `--spawn-root <ID|self>` — filter to every session in a spawn tree (any depth), identified by the tree's root session ID. `self` means the current session's spawn-root tree (its own id when it is itself the root). `parent` is not accepted: my parent is always in the same tree as me, so `--spawn-root parent` is either redundant with `--spawn-root self` or empty. Implies `--include-hidden`. Mutually exclusive with `--spawned-by` and `--descendants`.
- `--descendants <ID|self|parent>` — filter to processes of the proper descendants of the given session (every session transitively spawned by it, target excluded). `self` is the current session; `parent` is the current session's spawner (= my siblings, their subtrees, and my own subtree). Implies `--include-hidden`. Mutually exclusive with `--spawned-by` and `--spawn-root`. Use this when you want "everything under X" but not X itself.
- `--annotation KEY[OP]VALUE` — filter to processes whose session's `annotations` object matches the expression. Implemented as a session-id pre-filter: the backend first selects sessions matching all annotation predicates, then filters live processes to those session ids. Repeatable; multiple flags are AND-combined. Does **not** imply `--include-hidden` (orthogonal). Composes with filiation flags. Five operators:
  - `KEY=VALUE` — annotation key equals VALUE.
  - `KEY!=VALUE` — annotation key differs from VALUE (or key absent).
  - `KEY:exists` — annotation key is present (any value).
  - `KEY:not-exists` — annotation key is absent.
  - `KEY:in:V1,V2,...` — annotation key equals one of the listed values; escape a literal comma with `\,`.
  - Values are inferred as typed: `true`/`false` → boolean, `null` → null, integers and floats parsed numerically, everything else → string (same rules as `create-session --annotation`).
  - Example: `--spawn-root self --annotation role=implementer`

### Batch lookup (`get`)

```bash
$TWICC processes get <SESSION_ID> [<SESSION_ID>...]
```

Returns one entry per id in input order (duplicates collapsed). No filter flags.

### Batch stop (`stop`)

```bash
$TWICC processes stop <SESSION_ID> [<SESSION_ID>...] [--timeout N]
```

Idempotent. IDs that fail pre-check (unknown, subagent, stale, no directory, unknown provider) get a `skipped_*` status — they don't consume timeout budget. Valid drops are processed in parallel, so `--timeout` (default 30 s) is a wall-clock budget for the whole batch.

### Batch wait (`wait`)

```bash
$TWICC processes wait <SESSION_ID>... <STATUS>... --timeout N [--all|--first] [--transition]
```

Session_ids and statuses are a **single positional list, auto-discriminated by value** — anything matching a valid state (`starting`, `assistant_turn`, `awaiting_user_input`, `user_turn`, `dead`) is a status; everything else is a session_id. Pass session_ids first, statuses last.

- `--all` (default) — wait until every active session has matched.
- `--first` — stop as soon as one session matches.
- `--transition` — only match after at least one state change per session since the initial snapshot.
- `--timeout N` — **required**. Wall-clock budget for the whole batch. Exits 5 on timeout.

Unknown session_ids are dropped from the wait pool (`wait_status="skipped_unknown"`) and don't participate in `--all`/`--first`. An empty pool exits 0 immediately.

## Output format

### Listing

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

### Fields (listing)

- `state` — `starting`, `assistant_turn`, `awaiting_user_input`, or `user_turn`.
- `session_title` — `null` for brand-new sessions not yet picked up by the watcher.
- `pid` — `null` briefly after process start.

### Batch lookup (`get`)

Same shape per entry plus `session_known`. Three possible shapes:

- Live process: all fields filled, `session_known: true`.
- Session known, no live process: `state: "dead"`, process fields `null`, `session_known: true`.
- Unknown session_id: `state: "dead"`, `session_known: false`, most fields `null`.

```json
[
  {"id": 42, "session_id": "abc123", "state": "user_turn", "session_known": true, ...},
  {"id": null, "session_id": "def456", "state": "dead", "session_known": true, ...},
  {"id": null, "session_id": "typo", "state": "dead", "session_known": false, ...}
]
```

### Batch stop (`stop`)

```json
[
  {"session_id": "abc123", "session_known": true, "status": "stopped", "request_uuid": "...", "provider": "claude_code", "session_title": "...", "project_id": "...", "error": null},
  {"session_id": "typo", "session_known": false, "status": "skipped_unknown", "request_uuid": null, "provider": null, "session_title": null, "project_id": null, "error": "..."}
]
```

Possible `status` values:

- `stopped` — killed (or already not running — idempotent).
- `rejected` — server refused; see `error`.
- `failed` — unexpected server error; see `error`.
- `timeout` — no final status within `--timeout`.
- `skipped_unknown` — session_id not found.
- `skipped_subagent` — subagents cannot be stopped directly.
- `skipped_stale` — JSONL file gone.
- `skipped_no_directory` — project has no directory.
- `skipped_unknown_provider` — provider no longer recognized.

#### Exit codes (stop)

- `0` — ran to completion (check each entry's `status`)
- `1` — local validation error (e.g. `--timeout <= 0`)
- `2` — TwiCC server not running
- `64` — bad CLI usage

### Batch wait (`wait`)

```json
[
  {"session_id": "abc123", "session_known": true, "wait_status": "matched", "matched_state": "user_turn", "current_state": "user_turn", "provider": "claude_code", "session_title": "...", "project_id": "..."},
  {"session_id": "def456", "session_known": true, "wait_status": "pending", "matched_state": null, "current_state": "assistant_turn", ...},
  {"session_id": "typo", "session_known": false, "wait_status": "skipped_unknown", ...}
]
```

Possible `wait_status` values:

- `matched` — reached a requested state; see `matched_state`.
- `pending` — did not match before the wait ended (timeout or `--first` triggered elsewhere).
- `skipped_unknown` — session_id not found.

#### Exit codes (wait)

- `0` — condition satisfied (or wait pool was empty)
- `1` — local validation error
- `2` — TwiCC server not running
- `5` — timeout
- `64` — bad CLI usage

## Examples

```bash
$TWICC processes
$TWICC processes --state awaiting_user_input
$TWICC processes --spawned-by self
$TWICC processes --spawned-by parent
$TWICC processes --spawn-root self
$TWICC processes --descendants self
$TWICC processes --descendants parent
$TWICC processes --annotation role=implementer
$TWICC processes --spawn-root self --annotation role=implementer
$TWICC processes --annotation status:exists --annotation priority:in:high,critical
$TWICC processes get abc123 def456 ghi789
$TWICC processes stop abc123 def456
$TWICC processes stop abc123 def456 --timeout 60
$TWICC processes wait abc123 def456 user_turn dead --timeout 300
$TWICC processes wait abc123 def456 ghi789 awaiting_user_input --first --timeout 120
$TWICC processes wait abc123 def456 user_turn --transition --timeout 600
```

## Related commands

- `$TWICC process <session_id>` — inspect/stop/wait for a single session. Skill: `twicc-process`.
- `$TWICC session <session_id>` — session metadata. Skill: `twicc-session`.
- `$TWICC sessions` — browse all sessions (including stopped). Skill: `twicc-sessions`.
- `$TWICC topology <ID|self>` — show process states in tree context. Skill: `twicc-topology`.

## How to present results

1. For listing: group by provider; show title (or session_id), state, and time since `last_state_change_at`. Map states: `starting` → "spinning up", `assistant_turn` → "working", `awaiting_user_input` → "blocked — pending dialog", `user_turn` → "idle". If empty, say "no processes currently running".
2. For `get`: flag `session_known: false` as unknown; `state: "dead"` + `session_known: true` as "no live process".
3. For `stop`: bucket by `status` and show counts (e.g. "3 stopped, 1 skipped"). Include `error` for non-`stopped` entries.
4. For `wait`: surface `matched` entries with `matched_state`, `pending` with `current_state`, `skipped_unknown` separately. Call out timeout explicitly on exit 5.
5. You are in TwiCC — link to a session: `[link text](/project/{project_id}/session/{session_id})`.
