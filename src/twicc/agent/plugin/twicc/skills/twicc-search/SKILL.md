---
name: twicc-search
description: Search through TwiCC's session history using its full-text search index. Use when you or the user want to find past conversations, look up what was discussed, or locate specific content across sessions.
argument-hint: <query>
---

# TwiCC Search

Full-text search across all session history.

## When to use

- You or the user want to find something from a past session or conversation.
- You need to locate specific code, decisions, or discussions across sessions.

## How to invoke

TwiCC's executable varies by launch mode (uvx, dev, installed tool). ALWAYS USE THIS TO RESOLVE $TWICC AT THE START OF EACH BASH INVOCATION:

```bash
TWICC=${TWICC_BIN:-$(command -v twicc 2>/dev/null)}
[ -n "$TWICC" ] || { echo "TwiCC executable not found in this context" >&2; exit 1; }
```

Then run `$TWICC <args>` — **never quote `$TWICC`** (use `$TWICC args`, never `"$TWICC" args`): it may expand to multiple words, which quoting would break.

## Usage

```bash
$TWICC search '<query>' [OPTIONS]
```

### Options

- `--limit N` — max hits (default: 20).
- `--offset N` — skip first N for pagination (default: 0).
- `--include-hidden` — include hits from hidden sessions (excluded by default).
- `--only-hidden` — hits only from hidden sessions. Mutually exclusive with `--include-hidden`.
- `--spawned-by <ID|self|parent>` — filter hits to direct child sessions spawned by the given session ID. `self` is the current session (= my children); `parent` is the session that spawned the current one (= my siblings, myself included). Implies `--include-hidden`. Mutually exclusive with `--spawn-root` and `--descendants`.
- `--spawn-root <ID|self>` — filter hits to every session in a spawn tree (any depth), identified by the tree's root session ID. `self` means the current session's spawn-root tree (its own id when it is itself the root). `parent` is not accepted: my parent is always in the same tree as me, so `--spawn-root parent` is either redundant with `--spawn-root self` or empty. Implies `--include-hidden`. Mutually exclusive with `--spawned-by` and `--descendants`.
- `--descendants <ID|self|parent>` — filter hits to the proper descendants of the given session (every session transitively spawned by it, target excluded). `self` is the current session; `parent` is the current session's spawner (= my siblings, their subtrees, and my own subtree). Implies `--include-hidden`. Mutually exclusive with `--spawned-by` and `--spawn-root`. Use this when you want "everything under X" but not X itself.

### Query syntax

Default field is `body` (message content) — bare keywords search there automatically.

- Multiple terms (OR): `'websocket channels'`
- Phrase: `'"virtual scroll"'`
- Field-specific: `'body:websocket AND from_role:user'`
- Boolean: `AND`, `OR`, `NOT` (uppercase)

### Available fields

- `body` — message content (full-text, default).
- `from_role` — exact match: `user`, `assistant`, or `title`.
- `session_id` — exact match.
- `project_id` — exact match.
- `line_num` — integer, supports ranges: `line_num:[10 TO 50]`.
- `timestamp` — ISO 8601, supports ranges: `timestamp:[2025-01-01T00:00:00+00:00 TO *]`.
- `archived` — boolean: `archived:true`.

## Output format

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

## Examples

```bash
$TWICC search 'websocket'
$TWICC search 'body:websocket AND from_role:user'
$TWICC search 'project_id:-home-twidi-dev-a OR project_id:-home-twidi-dev-b'
$TWICC search 'websocket' --spawned-by self
$TWICC search 'websocket' --spawned-by parent
$TWICC search 'websocket' --spawn-root self
$TWICC search 'websocket' --descendants self
$TWICC search 'websocket' --descendants parent
$TWICC search 'websocket' --include-hidden --limit 50 --offset 20
```

## Related commands

- `$TWICC session <session_id> content <line_num>` — fetch the full item at a search result's `line_num`. Skill: `twicc-session`.
- `$TWICC session <session_id>` — full session metadata. Skill: `twicc-session`.
- `$TWICC topology <ID|self>` — discover the spawned-session tree before scoping search. Skill: `twicc-topology`.
- `$TWICC sessions --project <PROJECT>` — browse sessions in the same project. Skill: `twicc-sessions`.
- `$TWICC project <PROJECT>` — project details. Skill: `twicc-project`.

## How to present results

1. Summarize total hits (`total_hits`).
2. Show snippets stripped of HTML tags, with session ID and role for context.
3. If there are more results, offer to paginate with `--offset`.
4. You are in TwiCC — link to a session: `[link text](/project/{project_id}/session/{session_id})`.
