---
name: twicc-sessions
description: List sessions tracked by TwiCC across every backend provider (Claude Code, Codex, ...), or batch-look up specific session_ids (with a `known: false` placeholder for any id that doesn't exist). Use when the user wants to browse sessions, find a session ID, filter by project, batch-fetch metadata for a known set of ids, or see session activity and costs.
---

# TwiCC Sessions

List sessions tracked by TwiCC, ordered by most recently active. Only returns valid sessions (with a creation date and at least one user message).

## When to use

- The user asks to list or browse their sessions
- The user needs to find a session ID
- The user wants to see sessions for a specific project
- The user wants to see archived sessions
- The user (or a script) has a list of known session_ids and wants to batch-fetch their metadata — use `sessions get <ID>...` (one entry per id, placeholder when missing; returns subagents / archived / hidden too, since you named them explicitly)

## How to invoke

TwiCC's executable varies by launch mode (uvx, dev, installed tool). Resolve it once at the start of each Bash invocation:

```bash
TWICC=${TWICC_BIN:-$(command -v twicc 2>/dev/null)}
[ -n "$TWICC" ] || { echo "TwiCC executable not found in this context" >&2; exit 1; }
```

Then run any subcommand via `$TWICC <args>` — **do NOT quote `$TWICC`** (use `$TWICC args`, never `"$TWICC" args`). The variable may expand to a multi-word command (e.g. `uv run --directory ... run.py`); Bash relies on word-splitting to parse it, and quoting it would treat the entire expansion as a single program name and fail with "No such file or directory". All bash examples below use the unquoted form.

## How to list sessions

Run the `twicc sessions` CLI command via the Bash tool:

```bash
$TWICC sessions
```

### Options

- `--project <PROJECT>` — filter by project. Either a directory path (absolute or relative; resolved via `realpath` and converted to the canonical id) or a project ID. **When passing an id on the command line, drop the leading dash** (bash would otherwise parse `-home-...` as a flag and the call would fail); the CLI re-adds the dash internally. Prefer paths — that's what the user usually knows; ids are mostly useful when chaining commands. When the value resolves to a project that doesn't exist, the result is empty (no error). Default: all projects.
- `--workspace ID` — filter to sessions of projects belonging to the given workspace. Fails if the workspace does not exist. Can be combined with `--project` (intersection: the project must be inside the workspace, otherwise the result is empty).
- `--limit N` — max number of sessions to return (default: 20)
- `--offset N` — skip first N sessions for pagination (default: 0)
- `--include-archived` — include archived sessions in the results (default: false, archived sessions are excluded)
- `--include-hidden` — include hidden sessions in the results (default: false, hidden sessions are excluded). Hidden sessions are invisible in normal listings; pass this flag to surface them explicitly.
- `--only-hidden` — return **only** hidden sessions. Mutually exclusive with `--include-hidden`.
- `--spawned-by <ID|self>` — filter by spawner session ID. Only sessions whose `spawned_by` field matches the given ID are returned. The special value `self` resolves to the current session's own ID via PID ancestry (equivalent to `twicc whoami`) — useful for an agent that wants to list child sessions it spawned without knowing its own session ID. **Implies `--include-hidden` by default**: a filiation query surfaces every matching child whatever its visibility (in practice spawned children are usually hidden). Combine with `--only-hidden` to narrow to hidden children, or post-filter the JSON output's `hidden` field for visible-only.

### Examples

```bash
$TWICC sessions                                    # List the 20 most recent sessions
$TWICC sessions --project .                        # Sessions for the current directory
$TWICC sessions --project /home/twidi/dev/myproj   # Sessions for a specific project (by path)
$TWICC sessions --project 'home-twidi-dev-myproj'  # Sessions for a specific project (by id)
$TWICC sessions --workspace backend                # Sessions across every project in the "backend" workspace
$TWICC sessions --include-archived                 # Include archived sessions
$TWICC sessions --include-hidden                   # Include hidden sessions alongside visible ones
$TWICC sessions --only-hidden                      # Only hidden sessions
$TWICC sessions --spawned-by self                  # Sessions spawned by the current session (resolved via PID ancestry)
$TWICC sessions --spawned-by abc123-def456         # Sessions spawned by a specific session ID
$TWICC sessions --limit 50 --offset 20             # Paginate
```

## How to look up specific session_ids

When you already know which sessions you care about, use the `get`
sub-command instead of listing + post-filtering. Each requested
session_id produces exactly one entry in the output, in the order you
passed them (duplicates collapsed, first occurrence wins):

```bash
$TWICC sessions get <SESSION_ID> [<SESSION_ID>...]
```

Examples:

```bash
$TWICC sessions get abc123-def456                       # Single session
$TWICC sessions get abc123 def456 ghi789                # Batch lookup
```

Unlike `twicc sessions`, `get` accepts **no filter flags** — when you
name the sessions you care about, the listing filters don't apply:
subagents, archived sessions and hidden sessions are returned just
like regular ones (the singular `twicc session <ID>` has the same
permissive scope).

### Output

A JSON array, one entry per session_id, in the order you passed them
(duplicates collapsed). All entries share the same shape — full
session metadata when the id exists, the same shape with everything
nulled out when it doesn't, plus a `known: bool` flag:

```json
[
  {
    "id": "abc123-def456",
    "project_id": "-home-twidi-dev-myproject",
    "provider": "claude_code",
    "title": "Implement user authentication",
    ... (every field from the listing) ...,
    "known": true
  },
  {
    "id": "typo-or-unknown",
    "project_id": null,
    "provider": null,
    "title": null,
    ... (all other fields: null) ...,
    "known": false
  }
]
```

Because the output is 1-to-1 with the input order, callers can `zip`
it with the input list with no re-mapping:

```python
import json, subprocess
ids = ["abc...", "def...", "ghi..."]
out = json.loads(subprocess.check_output([twicc, "sessions", "get", *ids]))
for sid, entry in zip(ids, out):
    if not entry["known"]:
        print(f"  WARN: {sid} unknown to TwiCC")
    else:
        print(f"  {sid}: {entry['title']}")
```

## Output format

The command outputs a JSON array of session objects:

```json
[
  {
    "id": "abc123-def456",
    "project_id": "-home-twidi-dev-myproject",
    "provider": "claude_code",
    "parent_session_id": null,
    "last_line": 150,
    "mtime": 1741654800.0,
    "created_at": "2025-03-10T14:30:00+00:00",
    "last_started_at": "2025-03-10T14:30:00+00:00",
    "last_updated_at": "2025-03-10T15:45:00+00:00",
    "last_stopped_at": "2025-03-10T15:50:00+00:00",
    "last_new_content_at": "2025-03-10T15:45:00+00:00",
    "last_viewed_at": "2025-03-10T16:00:00+00:00",
    "stale": false,
    "title": "Implement user authentication",
    "slug": null,
    "user_message_count": 12,
    "compute_version_up_to_date": true,
    "context_usage": 85000,
    "self_cost": 1.234,
    "subagents_cost": 0.567,
    "total_cost": 1.801,
    "cwd": "/home/twidi/dev/myproject",
    "git_branch": "feature/auth",
    "git_directory": "/home/twidi/dev/myproject",
    "model": {"raw": "claude-opus-4-20250514", "family": "opus", "version": "4"},
    "archived": false,
    "pinned": null,
    "permission_mode": "default",
    "selected_model": null,
    "effort": null,
    "thinking_enabled": null,
    "claude_in_chrome": false,
    "fast_mode": false,
    "context_max": 200000,
    "compacted": false
  }
]
```

### Key fields

- **`id`** — session UUID
- **`project_id`** — parent project identifier
- **`provider`** — backend that owns the session: `"claude_code"` or `"codex"`. Determines the JSONL schema for items, supported settings, etc.
- **`title`** — session title (from first user message or custom title)
- **`slug`** — provider-supplied short identifier (e.g. Codex subagent nickname like `"Bohr"`), or `null`
- **`user_message_count`** — number of user message turns
- **`total_cost`** — total cost in USD (own + subagents)
- **`model`** — model info with `raw`, `family`, and `version`
- **`git_branch`** — git branch at time of session
- **`parent_session_id`** — `null` for regular sessions, set for subagents
- **`context_max`** — maximum context window in tokens (`200000` or `1000000`)
- **`compacted`** — whether the session has been compacted at least once
- **`last_new_content_at`** — most recent timestamp of new session content (item appended)
- **`last_viewed_at`** — when the user last opened the session in the TwiCC UI

## Related commands

- **Get project details:** `twicc project <project_path_or_id>` — get full details for one project (path preferred; id works too)
- **Find project IDs:** `twicc projects` — list all projects (only needed when you want a canonical id; otherwise pass the directory path directly to `--project`)
- **Inspect a single session (errors out if missing):** `twicc session <session_id>` — get full metadata for one session, exit 1 if not found. Use when "session not found" should be a hard failure. For batch lookup that tolerates missing ids, see `sessions get` above
- **Read session content:** `twicc session <session_id> content <line_or_range>` — read the actual conversation items
- **List subagents:** `twicc session <session_id> agents` — see subagents spawned by a session
- **Search across sessions:** `twicc search "<query>"` — full-text search across all sessions

## How to present results

1. Show the session title, date, and message count
2. Offer to provide more details on any session if the user wants
3. If there are more results than shown, offer to paginate with `--offset`
4. You are in TwiCC, so you can link to a session using a relative Markdown link so the user can click it: `[link text](/project/{project_id}/session/{session_id})` or to a project : `[link text](/project/{project_id})`
5. Only include cost and model information if the user explicitly asks for it
6. For `sessions get` output: scan for `known: false` entries (surface as "session X is unknown to TwiCC — typo or already cleaned up"). Known entries render like the listing
