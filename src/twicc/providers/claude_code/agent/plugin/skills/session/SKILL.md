---
name: session
description: Inspect a single Claude Code session — view details, read item content by line number, or list subagents. Use when the user wants to examine a specific session, read conversation content, or explore subagent activity.
argument-hint: <session_id> [content|agents]
---

# TwiCC Session

Inspect a single session: view its metadata, read conversation content by line number, or list its subagents.

## When to use

- The user wants details about a specific session
- The user wants to read the actual conversation content (messages, tool calls, etc.)
- The user wants to see which subagents were spawned by a session

## Commands

### Show session details

```bash
twicc session <SESSION_ID>
```

Returns the full session metadata as JSON. Works for both regular sessions and subagents. Only returns sessions that have a creation date and at least one user message.

#### Output format

```json
{
  "id": "abc123-def456",
  "project_id": "-home-twidi-dev-myproject",
  "parent_session_id": null,
  "last_line": 150,
  "mtime": 1741654800.0,
  "created_at": "2025-03-10T14:30:00+00:00",
  "last_started_at": "2025-03-10T14:30:00+00:00",
  "last_updated_at": "2025-03-10T15:45:00+00:00",
  "last_stopped_at": "2025-03-10T15:50:00+00:00",
  "stale": false,
  "title": "Implement user authentication",
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
  "claude_in_chrome": false
}
```

The `last_line` field tells you the total number of items in the session, which is useful to know the valid range for the `content` subcommand.

---

### Read session content

```bash
twicc session <SESSION_ID> content <LINE_OR_RANGE>
```

Fetch one or more session items by line number. Each item is a raw JSONL entry (user message, assistant message, tool call, tool result, etc.) parsed into a proper JSON object.

#### Arguments

- **Single line:** `twicc session <ID> content 5` — fetch item at line 5
- **Range:** `twicc session <ID> content 10-20` — fetch items from line 10 to 20 (inclusive)

#### Output format

Returns a JSON array of the raw JSONL objects, parsed into proper JSON:

```json
[
  {
    "type": "human",
    "message": {
      "role": "user",
      "content": [{"type": "text", "text": "Hello, can you help me?"}]
    },
    "timestamp": "2025-03-10T14:30:00.000Z"
  }
]
```

The structure of each object depends on its type (human message, assistant message, tool use, tool result, etc.). These are the raw Claude API message objects.

#### Tips

- Use `twicc session <ID>` first to check `last_line` and know the valid range
- Start with a small range to understand the session structure

---

### List subagents

```bash
twicc session <SESSION_ID> agents
```

List all subagents spawned by a session, ordered by most recently active.

#### Options

- `--limit N` — max number of subagents to return (default: 20)
- `--offset N` — skip first N subagents for pagination (default: 0)

#### Examples

```bash
twicc session abc123 agents                # List subagents
twicc session abc123 agents --limit 50     # List up to 50 subagents
```

Use `twicc session <subagent_id>` to inspect a specific subagent.

#### Constraints

- The session must be a **parent session** (not itself a subagent). If the session is a subagent, the command returns an error.

#### Output format

Returns a JSON array of session objects (same format as `twicc sessions` output), where each entry has `parent_session_id` set to the parent session ID.

## Related commands

- **Find session IDs:** `twicc sessions` — list sessions (optionally filtered by project)
- **Get project details:** `twicc project <project_id>` — get full details for the session's project
- **Find project IDs:** `twicc projects` — list all projects
- **Search for content:** `twicc search "<query>"` — full-text search returns `session_id` and `line_num`, which can be used with `twicc session <id> content <line_num>` to read the full item

## How to present results

1. For session details: summarize key info (title, date, model, branch)
2. For content: present messages in a readable format, distinguishing user vs assistant messages
3. For agents: show the list with titles, offer to provide more details on any agent if the user wants
4. You are in TwiCC, so you can link to a session using a relative Markdown link so the user can click it: `[link text](/project/{project_id}/session/{session_id})` or to a project : `[link text](/project/{project_id})`
5. Only include cost information if the user explicitly asks for it
