---
name: twicc-sessions
description: List sessions tracked by TwiCC, or batch-look up specific session_ids. Use when you or the user want to browse sessions, find a session ID, filter by project, or batch-fetch metadata for known ids.
---

# TwiCC Sessions

List sessions, or batch-look up specific ones with `sessions get`. Only returns valid sessions (with a creation date and at least one user message), unless you use `get` (which returns anything you name explicitly).

## When to use

- You or the user want to list or browse sessions.
- You need to find a session ID.
- You have a list of known session_ids and want to batch-fetch their metadata — use `sessions get <ID>...` (returns subagents, archived, and hidden too since you named them explicitly).

## How to invoke

TwiCC's executable varies by launch mode (uvx, dev, installed tool). ALWAYS USE THIS TO RESOLVE $TWICC AT THE START OF EACH BASH INVOCATION:

```bash
TWICC=${TWICC_BIN:-$(command -v twicc 2>/dev/null)}
[ -n "$TWICC" ] || { echo "TwiCC executable not found in this context" >&2; exit 1; }
```

Then run `$TWICC <args>` — **never quote `$TWICC`** (use `$TWICC args`, never `"$TWICC" args`): it may expand to multiple words, which quoting would break.

## Usage

### List

```bash
$TWICC sessions [OPTIONS]
```

Results are ordered by most recently active.

- `--project <PROJECT>` — filter by project (path or id; **drop the leading dash** on ids). Non-existent project returns empty, no error.
- `--workspace ID` — filter to sessions of projects in the given workspace.
- `--limit N` — max results (default: 20).
- `--offset N` — skip first N for pagination (default: 0).
- `--include-archived` — include archived sessions (excluded by default).
- `--include-hidden` — include hidden sessions (excluded by default).
- `--only-hidden` — only hidden sessions. Mutually exclusive with `--include-hidden`.
- `--spawned-by <ID|self>` — filter to direct child sessions spawned by the given session ID. `self` means the current session. Implies `--include-hidden`.

### Batch lookup

```bash
$TWICC sessions get <SESSION_ID> [<SESSION_ID>...]
```

Returns one entry per id in input order (duplicates collapsed). No filter flags — all session types returned.

## Output format

### Listing

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
    "compacted": false,
    "hidden": false,
    "spawned_by": null
  }
]
```

### Key fields

- `provider` — `"claude_code"` or `"codex"`. Determines item schema and supported settings.
- `slug` — provider short id (e.g. Codex subagent nickname), or `null`.
- `parent_session_id` — `null` for regular sessions, set for subagents.
- `model` — `{"raw": "...", "family": "...", "version": "..."}`.
- `context_max` / `context_usage` — max context window and current usage in tokens.
- `compacted` — whether the session has been compacted at least once.
- `last_new_content_at` — most recent item appended.
- `last_viewed_at` — when the user last opened the session in TwiCC.
- `hidden` — whether the session is hidden from all listings and broadcasts.
- `spawned_by` — session ID that spawned this session, or `null`.

### Batch lookup (`get`)

Same shape per entry, plus `known` boolean. When `known: false`, all other fields are `null`.

```json
[
  {"id": "abc123-def456", "title": "Implement user authentication", ..., "known": true},
  {"id": "typo-or-unknown", "title": null, ..., "known": false}
]
```

## Examples

```bash
$TWICC sessions
$TWICC sessions --project .
$TWICC sessions --project /home/twidi/dev/myproj
$TWICC sessions --workspace backend
$TWICC sessions --include-archived
$TWICC sessions --spawned-by self
$TWICC sessions --limit 50 --offset 20
$TWICC sessions get abc123-def456
$TWICC sessions get abc123 def456 ghi789
```

## Related commands

- `$TWICC session <session_id>` — full metadata for one session (exit 1 if missing). Skill: `twicc-session`.
- `$TWICC topology <ID|self>` — map a spawned-session tree. Skill: `twicc-topology`.
- `$TWICC project <PROJECT>` / `$TWICC projects` — project details or listing. Skill: `twicc-project` / `twicc-projects`.
- `$TWICC search "<query>"` — full-text search across sessions. Skill: `twicc-search`.

## How to present results

1. Show session title, date, and message count.
2. If there are more results, offer to paginate with `--offset`.
3. You are in TwiCC — link to a session: `[link text](/project/{project_id}/session/{session_id})`.
4. Only include cost and model info if explicitly asked.
5. For `get` output: flag `known: false` entries as unknown (typo or already cleaned up).
