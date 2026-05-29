---
name: twicc-session
description: Inspect a single session — view details, read item content by line number, list all user/assistant messages, or list subagents. Works for any provider TwiCC tracks (Claude Code, Codex, ...). Use when the user wants to examine a specific session, read conversation content, or explore subagent activity.
argument-hint: <session_id> [content|messages|agents]
---

# TwiCC Session

Inspect a single session: view its metadata, read conversation content by line number, or list its subagents.

## When to use

- The user wants details about a specific session
- The user wants to read the actual conversation content (messages, tool calls, etc.)
- The user wants to read just the user/assistant messages (no tool calls, no system noise)
- The user wants to see which subagents were spawned by a session

## How to invoke

TwiCC's executable varies by launch mode (uvx, dev, installed tool). Resolve it once at the start of each Bash invocation:

```bash
TWICC=${TWICC_BIN:-$(command -v twicc 2>/dev/null)}
[ -n "$TWICC" ] || { echo "TwiCC executable not found in this context" >&2; exit 1; }
```

Then run any subcommand via `$TWICC <args>` — **do NOT quote `$TWICC`** (use `$TWICC args`, never `"$TWICC" args`). The variable may expand to a multi-word command (e.g. `uv run --directory ... run.py`); Bash relies on word-splitting to parse it, and quoting it would treat the entire expansion as a single program name and fail with "No such file or directory". All bash examples below use the unquoted form.

## Commands

### Show session details

```bash
$TWICC session <SESSION_ID>
```

Returns the full session metadata as JSON. Works for both regular sessions and subagents. Only returns sessions that have a creation date and at least one user message.

#### Output format

```json
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
```

The `last_line` field tells you the total number of items in the session, which is useful to know the valid range for the `content` subcommand. The `provider` field (`"claude_code"` or `"codex"`) tells you which backend wrote the session — this matters for the `content` subcommand since each provider has its own JSONL item schema.

---

### Read session content

```bash
$TWICC session <SESSION_ID> content <LINE_OR_RANGE>
```

Fetch one or more session items by line number. Each item is a raw JSONL entry (user message, assistant message, tool call, tool result, etc.) parsed into a proper JSON object.

#### Arguments

- **Single line:** `$TWICC session <ID> content 5` — fetch item at line 5
- **Range:** `$TWICC session <ID> content 10-20` — fetch items from line 10 to 20 (inclusive)

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

The structure of each object depends on the session's `provider` (use `twicc session <ID>` first to find out) and on the item's type within that provider's JSONL stream:
- **`claude_code`** sessions use raw Claude API message objects (human/assistant messages, tool_use, tool_result, …).
- **`codex`** sessions use Codex's own JSONL schema (user/assistant messages, function_call, function_call_output, …).

In both cases the object is the JSONL line written by the provider's CLI, parsed into JSON. Field names and shapes differ between providers.

#### Tips

- Use `twicc session <ID>` first to check `last_line` and know the valid range
- Start with a small range to understand the session structure

---

### Read session messages

```bash
$TWICC session <SESSION_ID> messages [--range N|N-M] [--role user|assistant] [--limit N] [--offset N] [--tail N]
```

Fetch only the chat messages (user + assistant) of a session, with a **uniform shape across providers** (unlike `content`, which exposes the raw JSONL of the originating provider). Useful when you want the plain conversation transcript without tool calls, reasoning, system noise, etc.

Internally this uses the same extraction the full-text search indexer uses, so the text you get back is exactly what would be matched by `twicc search`.

#### Options

- `--range N` or `--range N-M` — restrict to a single line or a line range (same syntax as `content`)
- `--role user|assistant` — keep only one side of the conversation
- `--limit N` — cap the number of returned messages (default: no cap)
- `--offset N` — skip the first N messages (default: 0)
- `--tail N` — return the **last** N messages instead of the first N (mutually exclusive with `--limit`/`--offset`)

#### Output format

Returns a JSON array, one entry per message:

```json
[
  {
    "line_num": 3,
    "text": "Hello, can you help me?",
    "role": "user",
    "timestamp": "2025-03-10T14:30:00+00:00"
  },
  {
    "line_num": 4,
    "text": "Sure — what do you need?",
    "role": "assistant",
    "timestamp": "2025-03-10T14:30:02+00:00"
  }
]
```

#### Tips

- Pair with `twicc search "<query>" --session <id>` first to know roughly where matches live, then `messages --range A-B` to pull a focused transcript window.
- Use `--role user` to get just the prompts (e.g. to summarize what the user asked across a long session).
- Use `--tail N` when you only care about the most recent exchanges — e.g. `--tail 1` to get the very last message, `--tail 10` for the last few turns. Combine with `--role` to scope to one side (e.g. `--role assistant --tail 1` for the latest agent reply).

---

### List subagents

```bash
$TWICC session <SESSION_ID> agents
```

List all subagents spawned by a session, ordered by most recently active.

#### Options

- `--limit N` — max number of subagents to return (default: 20)
- `--offset N` — skip first N subagents for pagination (default: 0)

#### Examples

```bash
$TWICC session abc123 agents               # List subagents
$TWICC session abc123 agents --limit 50    # List up to 50 subagents
```

Use `twicc session <subagent_id>` to inspect a specific subagent.

#### Constraints

- The session must be a **parent session** (not itself a subagent). If the session is a subagent, the command returns an error.

#### Output format

Returns a JSON array of session objects (same format as `twicc sessions` output), where each entry has `parent_session_id` set to the parent session ID.

## Related commands

- **Find session IDs:** `twicc sessions` — list sessions (optionally filtered by project)
- **Inspect the live process:** `twicc process <session_id>` — current state and OS PID of the running process attached to this session (errors out when the session has no live process)
- **Get project details:** `twicc project <project_id>` — get full details for the session's project
- **Find project IDs:** `twicc projects` — list all projects
- **Search for content:** `twicc search "<query>"` — full-text search returns `session_id` and `line_num`, which can be used with `twicc session <id> content <line_num>` to read the full item

## How to present results

1. For session details: summarize key info (title, date, model, branch)
2. For content: present messages in a readable format, distinguishing user vs assistant messages
3. For messages: render the transcript in chronological order, prefixing each entry with its role (and optionally line number when the user might want to drill into a specific item via `content`)
4. For agents: show the list with titles, offer to provide more details on any agent if the user wants
5. You are in TwiCC, so you can link to a session using a relative Markdown link so the user can click it: `[link text](/project/{project_id}/session/{session_id})` or to a project : `[link text](/project/{project_id})`
6. Only include cost information if the user explicitly asks for it
