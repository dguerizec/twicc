---
name: twicc-update-workspace
description: Update an existing TwiCC workspace — rename, change color, add/remove projects, add/remove auto-add patterns, archive/unarchive. Flags are combinable: every operation is applied atomically in a single write. Use when the user wants to tweak a workspace from the CLI without going through the UI.
argument-hint: <WORKSPACE_ID> [--name X] [--color X|--unset-color] [--add-project PID]... [--remove-project PID]... [--add-pattern P]... [--remove-pattern P]... [--archive|--unarchive]
---

# TwiCC Update Workspace

Apply a patch to an existing workspace by dropping a request file the live TwiCC server picks up. The server validates the patch, applies every operation under the workspaces lock in a single atomic write, and broadcasts `workspaces_updated` to every connected UI client.

The workspace's id is **immutable** — `--name X` renames the display name but keeps the slug (`/workspace/{id}` URLs stay valid).

## When to use

- The user asks to "rename / recolor workspace X", "add project Y to workspace X", "archive workspace X", "drop a pattern from workspace X", or any combination.
- A script needs to update workspace membership programmatically (e.g. promote / demote projects based on activity).

## How to invoke

TwiCC's executable varies by launch mode (uvx, dev, installed tool). Resolve it once at the start of each Bash invocation:

```bash
TWICC=${TWICC_BIN:-$(command -v twicc 2>/dev/null)}
[ -n "$TWICC" ] || { echo "TwiCC executable not found in this context" >&2; exit 1; }
```

Then run any subcommand via `$TWICC <args>` — **do NOT quote `$TWICC`**. The variable may expand to a multi-word command (e.g. `uv run --directory ... run.py`); Bash relies on word-splitting to parse it.

## Prerequisite: the server must be running

Same as the other CLI commands. Heartbeat check fails fast (exit 2) if `<data_dir>/twicc.heartbeat` is missing or stale (> 15 s old). If the user gets that error, ask them to start the server (`twicc` in another terminal) and retry.

## Basic shape

```bash
$TWICC update-workspace '<WORKSPACE_ID>' [OPTIONS]
```

### Required argument

- **`WORKSPACE_ID`** — id of the existing workspace. You can get one from `twicc workspaces` or directly from the UI URL. The id is the slug; it does NOT change on rename.

### Patch flags (combinable, all optional, at least one required)

| Flag | Effect |
|------|--------|
| `--name NEW_NAME` | Rename the display name. Trimmed; non-empty; ≤ 20 characters; unique (case-insensitive) across other workspaces. The id stays unchanged. |
| `--color VALUE` | Set a CSS hex color (`#rgb`, `#rrggbb`, or `#rrggbbaa`). Mutually exclusive with `--unset-color`. |
| `--unset-color` | Clear the color (back to none). Mutually exclusive with `--color`. |
| `--add-project PID` (repeatable) | Add a project. Idempotent — silently skips if the project is already in the workspace. The project id must exist in TwiCC. |
| `--remove-project PID` (repeatable) | Remove a project. Idempotent — silently skips if the project isn't in the workspace. No DB check on the id (you can safely pass a stale id). |
| `--add-pattern PATTERN` (repeatable) | Add an auto-add directory pattern (using `*` as wildcard). Idempotent. |
| `--remove-pattern PATTERN` (repeatable) | Remove a pattern. Idempotent. |
| `--archive` | Mark as archived (excluded from default listings). Mutually exclusive with `--unarchive`. |
| `--unarchive` | Mark as not archived. Mutually exclusive with `--archive`. |

### Output controls

| Flag | |
|------|---|
| `--timeout SECONDS` | Seconds to wait for the server's final status (default 30). |
| `--json` | Emit a single JSON object on stdout (implies `--no-color`). |
| `--no-color` | Disable ANSI colors. |

## Atomic semantics

Every flag passed is applied in the **same** read-modify-write under the workspaces lock — there's no partial state visible mid-operation. If anything fails validation, nothing is applied.

Reordering of projects / patterns is **not** supported in the CLI (use the UI). `--add-project` appends to the existing list; `--remove-project` filters it.

## Rejections caught locally (exit 1)

- `conflicting_flags` — `--color` and `--unset-color` together, or `--archive` and `--unarchive` together.
- `no_op` — no patch flag was passed (nothing to update).
- `workspace_not_found` — the WORKSPACE_ID doesn't exist.
- `invalid_name` — `--name` empty (or whitespace only) after trim, or > 20 characters.
- `duplicate_name` — `--name` collides (case-insensitive) with another workspace.
- `invalid_color` — `--color` value isn't a valid hex color.
- `invalid_pattern` — an `--add-pattern` value is empty after trim.
- `project_not_found` — an `--add-project` id doesn't exist in TwiCC's DB.

## Rejections from the server (exit 3)

The server re-runs the same checks under the workspaces lock. Same error vocabulary; treat them like local rejections.

## Output format

### Text mode (default)

```
✓ Heartbeat OK (last seen 0.5s ago)
✓ Pre-flight validation passed
→ Request submitted (request_uuid: 4a8352fb...)
✓ Workspace updated: backend
```

### JSON mode (`--json`)

A single JSON object, one of:

```json
{"status":"updated","workspace_id":"backend","request_uuid":"..."}
{"status":"validation_error","errors":[{"field":"--name","code":"duplicate_name","message":"..."}]}
{"status":"rejected","errors":[{"field":"...","code":"...","message":"..."}],"request_uuid":"..."}
{"status":"failed","error":"...","request_uuid":"..."}
{"status":"timeout","received_seen":true,"message":"...","request_uuid":"..."}
```

### Exit codes

| Code | Meaning |
|------|---------|
| 0 | Update applied |
| 1 | CLI validation error |
| 2 | TwiCC server not running |
| 3 | Server rejected the request |
| 4 | Server hit an unexpected error |
| 5 | Timeout |
| 64 | Bad CLI usage (handled by Typer) |

## Examples

```bash
# Rename only.
$TWICC update-workspace backend --name 'Backend (legacy)'

# Recolor only.
$TWICC update-workspace backend --color '#ff9900'

# Clear the color.
$TWICC update-workspace backend --unset-color

# Add two projects at once.
$TWICC update-workspace backend \
    --add-project '-home-twidi-dev-api' \
    --add-project '-home-twidi-dev-workers'

# Replace one project with another (atomic).
$TWICC update-workspace backend \
    --remove-project '-home-twidi-dev-old-api' \
    --add-project '-home-twidi-dev-new-api'

# Add an auto-add pattern + archive in one shot.
$TWICC update-workspace scratch \
    --add-pattern '/home/twidi/scratch/*' \
    --archive

# Bring a workspace back from archive + recolor.
$TWICC update-workspace scratch --unarchive --color '#4a90d9'

# Machine-parseable output for scripts.
$TWICC --json update-workspace backend --name 'BE'
# → {"status":"updated","workspace_id":"backend","request_uuid":"..."}
```

## Related commands

- **Create a new workspace:** `twicc create-workspace <NAME>`.
- **Delete a workspace:** `twicc delete-workspace <ID>`.
- **Inspect / list workspaces:** `twicc workspace <ID>` / `twicc workspaces`.
- **List projects (to find ids to add):** `twicc projects` — id format matches what you pass to `--add-project` (leading dash included).

## How to present results

1. On success, restate which flags were applied (you have them in the call). Mention the change has been broadcast to any open UI client. Offer a clickable link: `[link text](/workspace/{workspace_id})`.
2. On validation error, summarise the failing fields with their codes — don't dump the raw output. For `conflicting_flags`, the message explains itself.
3. If the request was rejected (exit 3), it usually means the local snapshot was stale (race vs. a parallel UI write). Retry once; if it persists, fetch the current state via `twicc workspace <ID>` and reconcile.
