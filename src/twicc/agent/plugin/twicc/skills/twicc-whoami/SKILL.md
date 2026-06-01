---
name: twicc-whoami
description: Return the details of the session that owns the calling process. Use to discover your own TwiCC session_id from inside a Bash tool, when you need to reference your own session.
---

# TwiCC Whoami

Identify the TwiCC session you are running in. Returns a JSON object with `session_id`, `title`, `project_id`, `project_directory`, `current_working_directory` (resolved from tool_use activity — may differ from `project_directory` when working in a worktree or another repo), the resolved `agent_settings`, the full `session` payload (same shape as `$TWICC session <ID>`), and the matching `process` row. Exits 1 if not running inside a TwiCC agent.

## When to use

- You need your own `session_id` and don't already have it in context.

## How to invoke

TwiCC's executable varies by launch mode (uvx, dev, installed tool). ALWAYS USE THIS TO RESOLVE $TWICC AT THE START OF EACH BASH INVOCATION:

```bash
TWICC=${TWICC_BIN:-$(command -v twicc 2>/dev/null)}
[ -n "$TWICC" ] || { echo "TwiCC executable not found in this context" >&2; exit 1; }
```

Then run `$TWICC <args>` — **never quote `$TWICC`** (use `$TWICC args`, never `"$TWICC" args`): it may expand to multiple words, which quoting would break.

## Usage

```bash
$TWICC whoami           # human-readable (indented JSON)
$TWICC whoami --json    # compact JSON, suitable for parsing
```

### Self-aware shortcuts

Commands that accept `self` resolve the calling session on their own. On `$TWICC sessions`, `$TWICC processes`, and `$TWICC search` use `--spawned-by self` (direct children), `--descendants self` (every descendant, you excluded), or `--spawn-tree self` (the whole spawn tree that contains you — any session id in the tree resolves to the same tree). `--spawned-by` and `--descendants` also accept `parent` (the session that spawned the current one), which pivots the same query around your parent — surfacing your siblings on `--spawned-by parent`, and your siblings + their subtrees + your own subtree on `--descendants parent`. `--spawn-tree parent` is intentionally not supported: parent is always in your tree, so it's either redundant or empty. Use `$TWICC topology self` when you need the surrounding spawned-session tree.

### Exit codes

- `0` — Session resolved.
- `1` — No session found in PID ancestry.

## Examples

```bash
MY_SESSION_ID=$($TWICC whoami --json | jq -r .session_id)
MY_MODEL=$($TWICC whoami --json | jq -r .agent_settings.selected_model)
MY_PID=$($TWICC whoami --json | jq -r .process.pid)
```

## Related commands

- `$TWICC sessions --spawned-by <self|parent>` / `--descendants <self|parent>` / `--spawn-tree self` — children/siblings, every descendant (self/parent excluded), or the full spawn tree. Skill: `twicc-sessions`.
- `$TWICC processes --spawned-by <self|parent>` / `--descendants <self|parent>` / `--spawn-tree self` — same for live processes. Skill: `twicc-processes`.
- `$TWICC search '<query>' --spawned-by <self|parent>` / `--descendants <self|parent>` / `--spawn-tree self` — same for full-text search. Skill: `twicc-search`.
- `$TWICC topology self` — map the spawned-session tree around you. Skill: `twicc-topology`.
