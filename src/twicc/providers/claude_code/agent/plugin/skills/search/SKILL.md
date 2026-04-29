---
name: search
description: Search through Claude Code session history using TwiCC's full-text search index. Use when the user wants to find past conversations, look up what was discussed, or locate specific content across sessions.
argument-hint: <query>
---

# TwiCC Search

Search across all Claude Code session history using TwiCC's Tantivy-based full-text search index.

## When to use

- The user asks to find something from a past session or conversation
- The user wants to know if a topic was discussed before
- The user needs to locate specific code, decisions, or discussions across sessions

## How to search

Run the `twicc search` CLI command via the Bash tool:

```bash
twicc search "<query>"
```

### Options

- `--limit N` — max number of hits (default: 20)
- `--offset N` — skip first N hits for pagination (default: 0)

### Query syntax

The search uses Tantivy query syntax with `body` as the default field:

- **Simple keyword:** `twicc search "websocket"`
- **Multiple terms (OR):** `twicc search "websocket channels"`
- **Phrase search:** `twicc search '"virtual scroll"'`
- **Field-specific:** `twicc search "body:websocket AND from_role:user"` (only user messages)
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
