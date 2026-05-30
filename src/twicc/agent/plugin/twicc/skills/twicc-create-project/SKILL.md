---
name: twicc-create-project
description: Create a new TwiCC project from a directory path — optionally with a display name and a color. The project id is derived from the directory; the directory must be unique (one project per realpath). Use when the user wants to register a directory as a TwiCC project from the CLI before any session has been started in it.
argument-hint: <DIRECTORY> [--name X] [--color X] [--create-directory]
---

# TwiCC Create Project

Create a project by dropping a request file the live TwiCC server picks up. The server normalises the directory via `os.path.realpath`, derives the project id, validates everything, calls the single creation entry point (`register_project`) which fires `project_added` and runs workspace auto-add.

A project corresponds 1-to-1 with a working directory (`id = path_to_project_id(realpath(directory))`). You cannot create two projects for the same directory.

## When to use

- The user asks to "register a new project at /path/...", "create a project for this folder", or wants to scaffold a project before its first session.
- A script preps several projects in batch (e.g. mass-import a directory tree as TwiCC projects).

## How to invoke

TwiCC's executable varies by launch mode (uvx, dev, installed tool). Resolve it once at the start of each Bash invocation:

```bash
TWICC=${TWICC_BIN:-$(command -v twicc 2>/dev/null)}
[ -n "$TWICC" ] || { echo "TwiCC executable not found in this context" >&2; exit 1; }
```

Then run `$TWICC <args>` — **do NOT quote `$TWICC`** (use `$TWICC args`, never `"$TWICC" args`).

## Prerequisite: the server must be running

Heartbeat check fails fast (exit 2) if `<data_dir>/twicc.heartbeat` is missing or stale (> 15 s old).

## Basic shape

```bash
$TWICC create-project '<DIRECTORY>' [OPTIONS]
```

### Required argument

- **`DIRECTORY`** — path of the project's working directory. Normalised via `os.path.realpath` server-side, so symlinks resolve to their canonical target. Must be absolute (or resolvable to an absolute path). The project id is derived deterministically from this canonical path via `path_to_project_id` (every non-alphanumeric char becomes `-`).

### Options

| Flag | |
|------|---|
| `--name VALUE` | Optional display name. Trimmed; ≤ 25 characters; globally unique across all projects (collision → `duplicate_name`). If omitted, the UI falls back to the directory's basename. |
| `--color VALUE` | Optional CSS hex color (`#rgb`, `#rrggbb`, or `#rrggbbaa`). |
| `--create-directory` | If the directory does not exist on disk, create it (and any missing parents) before registering. Without this flag, a missing directory is rejected with `directory_not_found`. |
| `--timeout SECONDS` | Seconds to wait for the server's final status (default 30). |
| `--json` | Emit a single JSON object on stdout (implies `--no-color`). |
| `--no-color` | Disable ANSI colors. |

## Rejections caught locally (exit 1)

- `invalid_directory` — directory not absolute, or path exists but is not a directory.
- `directory_not_found` — directory does not exist and `--create-directory` was not passed.
- `project_already_exists` — a project already exists for this directory (same canonical path).
- `invalid_name` — name > 25 characters after trim.
- `duplicate_name` — another project already uses this name (case-sensitive).
- `invalid_color` — `--color` value is not a valid hex color.

## Rejections from the server (exit 3)

The server re-runs every check (the drop-file is a trust boundary). Same vocabulary plus:
- `directory_creation_failed` — `--create-directory` was set but `os.makedirs` failed (permission denied, read-only fs, etc.).

## Output format

### Text mode (default)

```
✓ Heartbeat OK (last seen 0.4s ago)
✓ Pre-flight validation passed
→ Request submitted (request_uuid: 4a8352fb...)
✓ Project created: -home-twidi-dev-newproj
```

### JSON mode (`--json`)

```json
{"status":"created","project_id":"-home-twidi-dev-newproj","request_uuid":"..."}
{"status":"validation_error","errors":[{"field":"DIRECTORY","code":"project_already_exists","message":"..."}]}
{"status":"rejected","errors":[{"field":"...","code":"...","message":"..."}],"request_uuid":"..."}
{"status":"failed","error":"...","request_uuid":"..."}
{"status":"timeout","received_seen":true,"message":"...","request_uuid":"..."}
```

### Exit codes

| Code | Meaning |
|------|---------|
| 0 | Project created |
| 1 | CLI validation error |
| 2 | TwiCC server not running |
| 3 | Server rejected (race-detected duplicate, mkdir failed, ...) |
| 4 | Server hit an unexpected error |
| 5 | Timeout |
| 64 | Bad CLI usage (handled by Typer) |

## Examples

```bash
# Minimal: register an existing directory.
$TWICC create-project /home/twidi/dev/newproj

# With name + color.
$TWICC create-project /home/twidi/dev/newproj --name 'New Project' --color '#4a90d9'

# Scaffold a brand-new directory and project in one shot.
$TWICC create-project /home/twidi/dev/scratch --create-directory

# Machine-parseable output for scripts.
$TWICC create-project --json /home/twidi/dev/newproj
# → {"status":"created","project_id":"-home-twidi-dev-newproj","request_uuid":"..."}
```

## Related commands

- **Update an existing project:** `twicc update-project <ID> [--name X | --unset-name] [--color X | --unset-color] [--archive | --unarchive]`.
- **List / inspect projects:** `twicc projects` (with `--include-archived` / `--workspace` filters) and `twicc project <ID>`.
- **Add the new project to a workspace right after creation:** `twicc update-workspace <WORKSPACE_ID> --add-project <PROJECT_ID>` (workspace auto-add patterns may already cover this).

## How to present results

1. On success, restate the project_id and any custom name set. Offer a clickable link: `[link text](/project/{project_id})`.
2. Mention the change is broadcast — open UI clients see the project appear without reload.
3. If the project is added to one or more workspaces via auto-add patterns, the server's `workspaces_updated` broadcast will also fire (no additional user action needed).
4. On `project_already_exists`, surface the existing project id from the error message — the user usually wants to update / inspect it instead.
