---
name: twicc-create-workspace
description: Create a new TwiCC workspace — a user-defined group of projects, optionally with a color, auto-add directory patterns, and an initial project list. Use when the user wants to spawn a new workspace from the CLI or from a script, or organise existing projects into a new group.
argument-hint: <NAME> [--color X] [--add-project PROJECT]... [--add-pattern P]... [--archived]
---

# TwiCC Create Workspace

Create a new workspace by dropping a request file the live TwiCC server picks up. The server validates the name, generates the id (slug + `-2`/`-3` on collision), checks every `--add-project` exists in DB, writes `workspaces.json` atomically, and broadcasts `workspaces_updated` to every connected UI client.

## When to use

- The user asks to "create a workspace" / "make a new workspace called X" / "regroup these projects into a workspace".
- A script needs to scaffold workspaces (e.g. one per team, one per topic) before populating them.

## How to invoke

TwiCC's executable varies by launch mode (uvx, dev, installed tool). Resolve it once at the start of each Bash invocation:

```bash
TWICC=${TWICC_BIN:-$(command -v twicc 2>/dev/null)}
[ -n "$TWICC" ] || { echo "TwiCC executable not found in this context" >&2; exit 1; }
```

Then run any subcommand via `$TWICC <args>` — **do NOT quote `$TWICC`** (use `$TWICC args`, never `"$TWICC" args`). The variable may expand to a multi-word command (e.g. `uv run --directory ... run.py`); Bash relies on word-splitting to parse it, and quoting it would treat the entire expansion as a single program name and fail with "No such file or directory".

## Prerequisite: the server must be running

The command communicates with the live TwiCC server through the data directory (no network, no auth). It checks `<data_dir>/twicc.heartbeat` first and fails fast (exit code 2) if it's missing or stale (> 15 s old). If the user gets that error, ask them to start the server (`twicc` in another terminal) and retry.

## Basic shape

```bash
$TWICC create-workspace '<NAME>' [OPTIONS]
```

### Required argument

- **`NAME`** — display name. Trimmed; must be non-empty, ≤ 20 characters, unique (case-insensitive) across existing workspaces (including archived ones). The id is generated server-side by slugifying the name (lowercased, non-alphanumeric → `-`, repeated `-` collapsed, leading/trailing `-` stripped) and appending `-2`, `-3`, … in case of slug collision.

### Options

| Flag | |
|------|---|
| `--color VALUE` | Optional CSS hex color (`#rgb`, `#rrggbb`, or `#rrggbbaa`). Other CSS forms (named colors, `rgb(...)`, etc.) are rejected from the CLI for simplicity. |
| `--add-project PROJECT` (repeatable) | Add a project to the workspace. The value is either a directory path (absolute or relative; resolved via `realpath` and converted to the canonical id) or a project ID. **When passing an id, drop the leading dash** (bash would otherwise parse `-home-...` as a flag and the call would fail); the CLI re-adds the dash internally. Prefer paths — that's what you usually know; ids are mostly useful when chaining with another `twicc` command's output. The resolved project must already exist in TwiCC (run `twicc projects` to list). Duplicates are silently deduplicated. |
| `--add-pattern PATTERN` (repeatable) | Add a directory auto-add pattern (using `*` as wildcard). Newly detected projects whose directory matches a pattern are added to the workspace automatically (see the matching rule in `twicc-workspace`). |
| `--archived` | Create the workspace already in the archived state (excluded from default listings). |
| `--timeout SECONDS` | Seconds to wait for the server's final status (default 30). |
| `--json` | Emit a single JSON object on stdout (implies `--no-color`). |
| `--no-color` | Disable ANSI colors. |

## Rejections caught locally (exit 1)

- `invalid_name` — name empty (or whitespace only) after trim, or > 20 characters.
- `duplicate_name` — another workspace already uses this name (case-insensitive).
- `invalid_color` — `--color` value isn't a valid hex color (`#rgb`, `#rrggbb`, `#rrggbbaa`).
- `invalid_pattern` — an `--add-pattern` value is empty after trim.
- `project_not_found` — an `--add-project` value doesn't resolve to an existing project (typo on the id, or a path that no known project points to). One error per missing project is emitted (the user sees the full list of bad values at once).

## Rejections from the server (exit 3)

The server re-runs the same checks under the workspaces lock (defence in depth in case the local snapshot was stale). Same error vocabulary: `invalid_name`, `duplicate_name`, `invalid_color`, `invalid_pattern`, `project_not_found`. Treat them the same way as local rejections.

## Output format

### Text mode (default)

```
✓ Heartbeat OK (last seen 0.4s ago)
✓ Pre-flight validation passed
→ Request submitted (request_uuid: 4a8352fb...)
✓ Workspace created: backend-2
```

### JSON mode (`--json`)

A single JSON object, one of:

```json
{"status":"created","workspace_id":"backend-2","request_uuid":"..."}
{"status":"validation_error","errors":[{"field":"NAME","code":"duplicate_name","message":"..."}]}
{"status":"rejected","errors":[{"field":"...","code":"...","message":"..."}],"request_uuid":"..."}
{"status":"failed","error":"...","request_uuid":"..."}
{"status":"timeout","received_seen":true,"message":"...","request_uuid":"..."}
```

### Exit codes

| Code | Meaning |
|------|---------|
| 0 | Workspace created (the id is in the success output) |
| 1 | CLI validation error (bad name, bad color, missing project id, etc.) |
| 2 | TwiCC server not running (heartbeat missing or stale) |
| 3 | Server rejected the request — see `errors[].code` |
| 4 | Server hit an unexpected error mid-flight |
| 5 | Timeout waiting for the server's final status |
| 64 | Bad CLI usage (handled by Typer) |

## Examples

```bash
# Minimal: just a name.
$TWICC create-workspace 'Backend'

# Pre-populate with projects + a color (paths first, id only when chaining).
$TWICC create-workspace 'Frontend' --color '#4a90d9' \
    --add-project /home/twidi/dev/app-front \
    --add-project /home/twidi/dev/design-system \
    --add-project 'home-twidi-dev-shared'   # by id (dash dropped), e.g. piped from another command

# Auto-grow pattern: any project under /home/twidi/dev/sparkup/* will be added
# automatically the next time it's detected by the watcher.
$TWICC create-workspace 'Sparkup' \
    --add-pattern '/home/twidi/dev/sparkup/*'

# Machine-parseable output for scripts.
$TWICC --json create-workspace 'Scratch' --archived
# → {"status":"created","workspace_id":"scratch","request_uuid":"..."}
```

## Related commands

- **Update an existing workspace:** `twicc update-workspace <ID>` — rename, recolor, add/remove projects, add/remove patterns, archive/unarchive.
- **Delete a workspace:** `twicc delete-workspace <ID>`.
- **List workspaces / inspect one:** `twicc workspaces` and `twicc workspace <ID>`.
- **List projects (only needed when you want to look up an id):** `twicc projects` — `--add-project` accepts the path directly (e.g. `--add-project .`), so you only need to list when you're chasing a specific id.

## How to present results

1. On success, restate the workspace name + id (from the success output). Offer a clickable link: `[link text](/workspace/{workspace_id})`.
2. If the user added projects or patterns, mention what was attached. Confirm the change has been broadcast to any open UI client.
3. On validation error, summarise the failing fields with their codes — don't dump the raw output.
