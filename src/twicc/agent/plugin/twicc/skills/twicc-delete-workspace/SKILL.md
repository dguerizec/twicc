---
name: twicc-delete-workspace
description: Delete a TwiCC workspace by id. Direct, no confirmation prompt — the CLI is meant to be scripted. Projects in the workspace are NOT deleted; only the workspace grouping disappears. Use when the user wants to drop a workspace from the CLI.
argument-hint: <WORKSPACE_ID>
---

# TwiCC Delete Workspace

Remove a workspace by dropping a request file the live TwiCC server picks up. The server removes the entry from `workspaces.json` atomically under the workspaces lock and broadcasts `workspaces_updated` to every connected UI client.

**Projects referenced by the workspace are not affected** — they remain in TwiCC's DB and in any other workspace that lists them. Only the grouping disappears.

## When to use

- The user asks to "delete / remove / drop workspace X".
- A script needs to clean up stale workspaces.

If the user is unsure and asks "are you sure I should delete this?", remind them that the operation is **immediate** (no undo) but **non-destructive for the underlying projects**. The workspace can always be recreated with `twicc create-workspace`.

## How to invoke

TwiCC's executable varies by launch mode (uvx, dev, installed tool). Resolve it once at the start of each Bash invocation:

```bash
TWICC=${TWICC_BIN:-$(command -v twicc 2>/dev/null)}
[ -n "$TWICC" ] || { echo "TwiCC executable not found in this context" >&2; exit 1; }
```

Then run `$TWICC <args>` — **do NOT quote `$TWICC`** (use `$TWICC args`, never `"$TWICC" args`).

## Prerequisite: the server must be running

Heartbeat check fails fast (exit 2) if `<data_dir>/twicc.heartbeat` is missing or stale (> 15 s old). Ask the user to start the server (`twicc` in another terminal) and retry.

## Basic shape

```bash
$TWICC delete-workspace '<WORKSPACE_ID>'
```

### Required argument

- **`WORKSPACE_ID`** — id of the workspace to delete. You can get one from `twicc workspaces` or directly from the UI URL. The id is the slug; it does NOT match the display name verbatim.

### Options

| Flag | |
|------|---|
| `--timeout SECONDS` | Seconds to wait for the server's final status (default 30). |
| `--json` | Emit a single JSON object on stdout (implies `--no-color`). |
| `--no-color` | Disable ANSI colors. |

## Rejections caught locally (exit 1)

- `workspace_not_found` — the WORKSPACE_ID doesn't exist in the current snapshot.

## Rejections from the server (exit 3)

- `workspace_not_found` — race vs. the local pre-check (the workspace was deleted between lookup and write).

## Output format

### Text mode (default)

```
✓ Heartbeat OK (last seen 0.5s ago)
✓ Pre-flight validation passed
→ Request submitted (request_uuid: 4a8352fb...)
✓ Workspace deleted: backend
```

### JSON mode (`--json`)

A single JSON object, one of:

```json
{"status":"deleted","workspace_id":"backend","request_uuid":"..."}
{"status":"validation_error","errors":[{"field":"WORKSPACE_ID","code":"workspace_not_found","message":"..."}]}
{"status":"rejected","errors":[{"field":"...","code":"...","message":"..."}],"request_uuid":"..."}
{"status":"failed","error":"...","request_uuid":"..."}
{"status":"timeout","received_seen":true,"message":"...","request_uuid":"..."}
```

### Exit codes

| Code | Meaning |
|------|---------|
| 0 | Workspace deleted |
| 1 | CLI validation error (workspace not found locally) |
| 2 | TwiCC server not running |
| 3 | Server rejected (race-detected workspace not found) |
| 4 | Server hit an unexpected error |
| 5 | Timeout |
| 64 | Bad CLI usage |

## Examples

```bash
# Direct delete.
$TWICC delete-workspace scratch

# Machine-parseable output for scripts.
$TWICC --json delete-workspace scratch
# → {"status":"deleted","workspace_id":"scratch","request_uuid":"..."}
```

## Related commands

- **Inspect / list workspaces:** `twicc workspace <ID>` / `twicc workspaces`.
- **Recreate a workspace:** `twicc create-workspace <NAME>` (the id will be re-slugified — collision suffixes `-2`/`-3` apply).
- **Just archive instead of deleting:** `twicc update-workspace <ID> --archive` — the workspace stays in storage, excluded from default listings. Easier to revert.

## How to present results

1. On success, confirm the deletion by name + id. Mention the change has been broadcast to any open UI client.
2. Remind the user (when relevant) that the projects that were in the workspace are still in TwiCC; only the grouping is gone.
3. On validation error, surface `workspace_not_found` clearly — the user likely typed the wrong id (or the display name instead of the slug).
