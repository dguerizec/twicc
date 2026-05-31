---
name: twicc-session
description: Inspect a single session — view metadata, read raw item content by line number, read user/assistant messages with a uniform shape, or list subagents. Use when you or the user want to examine a session, read conversation content, or explore subagent activity.
argument-hint: <session_id> [content|messages|agents]
---

# TwiCC Session

Inspect a single session. Four sub-commands:

- Default — full session metadata.
- `content <LINE_OR_RANGE>` — raw JSONL items by line number (provider-specific schema).
- `messages` — user/assistant messages only, uniform shape across providers.
- `agents` — list subagents spawned by this session.

## When to use

- You or the user want details about a specific session.
- You want to read conversation content (raw items or clean messages).
- You want to see which subagents were spawned by a session.

## How to invoke

TwiCC's executable varies by launch mode (uvx, dev, installed tool). ALWAYS USE THIS TO RESOLVE $TWICC AT THE START OF EACH BASH INVOCATION:

```bash
TWICC=${TWICC_BIN:-$(command -v twicc 2>/dev/null)}
[ -n "$TWICC" ] || { echo "TwiCC executable not found in this context" >&2; exit 1; }
```

Then run `$TWICC <args>` — **never quote `$TWICC`** (use `$TWICC args`, never `"$TWICC" args`): it may expand to multiple words, which quoting would break.

## Usage

### Default — session metadata

```bash
$TWICC session <SESSION_ID>
```

Works for regular sessions and subagents.

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
  "hidden": false,
  "spawned_by": null,
}
```

#### Key fields

- `last_line` — total item count; use as the upper bound for `content` ranges.
- `provider` — `"claude_code"` or `"codex"`. Determines item schema for `content`.
- `slug` — provider short id (e.g. Codex subagent nickname), or `null`.
- `parent_session_id` — `null` for regular sessions, set for subagents.
- `model` — `{"raw": "...", "family": "...", "version": "..."}`.
- `context_max` / `context_usage` — max context window and current usage in tokens.
- `compacted` — whether the session has been compacted at least once.
- `last_new_content_at` — most recent item appended.
- `last_viewed_at` — when the user last opened the session in TwiCC.
- `hidden` — whether the session is hidden from all listings and broadcasts.
- `spawned_by` — session ID that spawned this session, or `null`.

### Content — raw items

```bash
$TWICC session <SESSION_ID> content <LINE_OR_RANGE>
```

- Single line: `content 5`
- Range: `content 10-20` (inclusive)

Returns a JSON array of raw JSONL objects. Schema depends on provider:
- `claude_code` — Claude API objects (human/assistant messages, tool_use, tool_result, …).
- `codex` — Codex schema (user/assistant messages, function_call, function_call_output, …).

```json
[
  {
    "type": "human",
    "message": {"role": "user", "content": [{"type": "text", "text": "Hello"}]},
    "timestamp": "2025-03-10T14:30:00.000Z"
  }
]
```

Check `last_line` from the default sub-command first to know the valid range.

### Messages — uniform transcript

```bash
$TWICC session <SESSION_ID> messages [OPTIONS]
```

User + assistant messages only, uniform shape across providers. No tool calls, no system noise.

- `--range N` or `--range N-M` — filter by JSONL line number (same numbering as `content`). Only user/assistant messages whose `line_num` falls within the range are returned — not the Nth message in the list.
- `--role user|assistant` — keep only one side.
- `--limit N` — cap results (default: no cap).
- `--offset N` — skip first N messages (default: 0).
- `--tail N` — return the last N messages. Mutually exclusive with `--limit`/`--offset`.

```json
[
  {"line_num": 3, "text": "Hello, can you help me?", "role": "user", "timestamp": "2025-03-10T14:30:00+00:00"},
  {"line_num": 4, "text": "Sure — what do you need?", "role": "assistant", "timestamp": "2025-03-10T14:30:02+00:00"}
]
```

Common patterns:
- Last agent reply: `messages --role assistant --tail 1`
- Last N exchanges: `messages --tail N`
- Focused window from search: `messages --range A-B`

### Agents — list subagents

```bash
$TWICC session <SESSION_ID> agents [--limit N] [--offset N]
```

Only valid on parent sessions (errors on subagents). Returns provider-internal subagents, not sessions created via `create-session`; use `$TWICC topology <ID|self>` for the `spawned_by` tree (skill: `twicc-topology`). Ordered by most recently active.

## Examples

```bash
$TWICC session abc123-def456
$TWICC session abc123 content 5
$TWICC session abc123 content 10-20
$TWICC session abc123 messages --tail 1
$TWICC session abc123 messages --role user
$TWICC session abc123 agents
$TWICC session abc123 agents --limit 50
```

## Related commands

- `$TWICC sessions` — find session IDs. Skill: `twicc-sessions`.
- `$TWICC process <session_id>` — live process state and PID. Skill: `twicc-process`.
- `$TWICC topology <ID|self>` — map spawned sessions around this node. Skill: `twicc-topology`.
- `$TWICC search "<query>"` — results include `session_id` + `line_num` for use with `content`. Skill: `twicc-search`.
- `$TWICC project <project_id>` — project details. Skill: `twicc-project`.

## How to present results

1. Default: summarize title, date, model, branch.
2. Content: show items in readable form, distinguishing user vs assistant vs tool.
3. Messages: render transcript in order, prefixing each entry with its role.
4. Agents: list with titles; offer to inspect any specific subagent.
5. You are in TwiCC — link to a session: `[link text](/project/{project_id}/session/{session_id})`.
6. Only include cost information if explicitly asked.
