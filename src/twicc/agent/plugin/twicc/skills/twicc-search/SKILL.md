---
name: twicc-search
description: Search through TwiCC's session history using its full-text search index. Spans every backend provider (Claude Code, Codex, ...). Use when the user wants to find past conversations, look up what was discussed, or locate specific content across sessions.
argument-hint: <query>
---

# TwiCC Search

Search across all session history (every backend provider TwiCC tracks) using its Tantivy-based full-text search index.

## When to use

- The user asks to find something from a past session or conversation
- The user wants to know if a topic was discussed before
- The user needs to locate specific code, decisions, or discussions across sessions

## How to invoke

TwiCC's executable varies by launch mode (uvx, dev, installed tool). Resolve it once at the start of each Bash invocation:

```bash
TWICC=${TWICC_BIN:-$(command -v twicc 2>/dev/null)}
[ -n "$TWICC" ] || { echo "TwiCC executable not found in this context" >&2; exit 1; }
```

Then run any subcommand via `$TWICC <args>` — **do NOT quote `$TWICC`** (use `$TWICC args`, never `"$TWICC" args`). The variable may expand to a multi-word command (e.g. `uv run --directory ... run.py`); Bash relies on word-splitting to parse it, and quoting it would treat the entire expansion as a single program name and fail with "No such file or directory". All bash examples below use the unquoted form.

## How to search

Run the `twicc search` CLI command via the Bash tool:

```bash
$TWICC search '<query>'
```

### Options

- `--limit N` — max number of hits (default: 20)
- `--offset N` — skip first N hits for pagination (default: 0)
- `--include-hidden` — include hits from hidden sessions (default: false, hidden sessions are excluded). The full-text index **does** index hidden sessions, so this flag genuinely finds content in them.
- `--only-hidden` — return hits **only** from hidden sessions. Mutually exclusive with `--include-hidden`.
- `--spawned-by <ID|self>` — filter hits to sessions whose `spawned_by` field matches the given ID. The special value `self` resolves to the current session's own ID via PID ancestry (equivalent to `twicc whoami`) — useful for an agent searching within the sub-sessions it spawned. **Implies `--include-hidden` by default**: a filiation query matches every spawned child whatever its visibility (in practice spawned children are usually hidden). Combine with `--only-hidden` to narrow to hidden children.

### Examples

```bash
$TWICC search 'websocket'                              # Simple keyword search
$TWICC search 'websocket' --include-hidden             # Also search within hidden sessions
$TWICC search 'websocket' --only-hidden                # Search only within hidden sessions
$TWICC search 'websocket' --spawned-by self            # Search only in child sessions spawned by this session
$TWICC search 'websocket' --spawned-by abc123-def456   # Search only in children of a specific session
$TWICC search 'websocket' --limit 50 --offset 20       # Paginate
```

### Query syntax

The search uses Tantivy query syntax with `body` as the default field:

- **Simple keyword:** `$TWICC search 'websocket'`
- **Multiple terms (OR):** `$TWICC search 'websocket channels'`
- **Phrase search:** `$TWICC search '\"virtual scroll\"'`
- **Field-specific:** `$TWICC search 'body:websocket AND from_role:user'` (only user messages)
- **Boolean operators:** `AND`, `OR`, `NOT` (must be uppercase)

### Available fields

- **`body`** (text, full-text) — message content. This is the default field, so bare keywords search here automatically.
- **`from_role`** (text, exact match) — message author. Values: `user`, `assistant`, or `title`. Example: `from_role:user`
- **`session_id`** (text, exact match) — session UUID. Example: `session_id:abc-123`
- **`project_id`** (text, exact match) — project UUID. Example: `project_id:def-456`
- **`line_num`** (unsigned integer) — line number within the session JSONL file. Supports range queries: `line_num:[10 TO 50]`
- **`timestamp`** (date) — message timestamp in ISO 8601 format `%Y-%m-%dT%H:%M:%S+00:00`. Supports range queries: `timestamp:[2025-01-01T00:00:00+00:00 TO 2025-02-01T00:00:00+00:00]`, or open-ended: `timestamp:[2025-06-01T00:00:00+00:00 TO *]`
- **`archived`** (boolean) — whether the session is archived. Example: `archived:true`

## Output format

The command outputs JSON with this structure:

```json
{
  "hits": [
    {
      "score": 12.34,
      "session_id": "abc-123",
      "project_id": "def-456",
      "line_num": 42,
      "from_role": "user",
      "timestamp": "2025-01-15T10:30:00Z",
      "archived": false,
      "snippet": "<b>highlighted</b> match text..."
    }
  ],
  "total_hits": 150,
  "query": "websocket",
  "limit": 20,
  "offset": 0
}
```

## Related commands

- **Read the full item:** `twicc session <session_id> content <line_num>` — use `session_id` and `line_num` from search results to fetch the complete content
- **Inspect the session:** `twicc session <session_id>` — get full session metadata (title, cost, model, branch, etc.)
- **List sessions for a project:** `twicc sessions --project <project_id>` — browse other sessions in the same project (omit the leading dash from the project ID)
- **Get project info:** `twicc project <project_id>` — get project details from the `project_id` in search results (omit the leading dash)

## How to present results

1. Summarize the total number of hits found
2. Present the most relevant results with their snippets (strip HTML tags from snippets for readability)
3. Mention the session ID and role for context
4. If there are more results than shown, offer to paginate with `--offset`
5. You are in TwiCC, so you can link to a session using a relative Markdown link so the user can click it: `[link text](/project/{project_id}/session/{session_id})` or to a project : `[link text](/project/{project_id})`
6. Only include cost information if the user explicitly asks for it
