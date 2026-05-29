---
name: twicc-sessions
description: List sessions tracked by TwiCC across every backend provider (Claude Code, Codex, ...). Use when the user wants to browse sessions, find a session ID, filter by project, or see session activity and costs.
---

# TwiCC Sessions

List sessions tracked by TwiCC, ordered by most recently active. Only returns valid sessions (with a creation date and at least one user message).

## When to use

- The user asks to list or browse their sessions
- The user needs to find a session ID
- The user wants to see sessions for a specific project
- The user wants to see archived sessions

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

- `--project ID` — filter by project ID, omit the leading dash (default: all projects)
- `--workspace ID` — filter to sessions of projects belonging to the given workspace. Fails if the workspace does not exist. Can be combined with `--project` (intersection: the project must be inside the workspace, otherwise the result is empty).
- `--limit N` — max number of sessions to return (default: 20)
- `--offset N` — skip first N sessions for pagination (default: 0)
- `--include-archived` — include archived sessions in the results (default: false, archived sessions are excluded)

### Examples

```bash
$TWICC sessions                                    # List the 20 most recent sessions
$TWICC sessions --project 'home-twidi-dev-myproj'  # Sessions for a specific project
$TWICC sessions --workspace backend                # Sessions across every project in the "backend" workspace
$TWICC sessions --include-archived                 # Include archived sessions
$TWICC sessions --limit 50 --offset 20             # Paginate
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

- **Get project details:** `twicc project <project_id>` — get full details for one project
- **Find project IDs:** `twicc projects` — list all projects to find IDs for `--project`
- **Inspect a session:** `twicc session <session_id>` — get full metadata for one session
- **Read session content:** `twicc session <session_id> content <line_or_range>` — read the actual conversation items
- **List subagents:** `twicc session <session_id> agents` — see subagents spawned by a session
- **Search across sessions:** `twicc search "<query>"` — full-text search across all sessions

## How to present results

1. Show the session title, date, and message count
2. Offer to provide more details on any session if the user wants
3. If there are more results than shown, offer to paginate with `--offset`
4. You are in TwiCC, so you can link to a session using a relative Markdown link so the user can click it: `[link text](/project/{project_id}/session/{session_id})` or to a project : `[link text](/project/{project_id})`
5. Only include cost and model information if the user explicitly asks for it
