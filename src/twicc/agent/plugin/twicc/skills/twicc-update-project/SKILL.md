---
name: twicc-update-project
description: Update an existing TwiCC project — rename, change color, archive/unarchive. The directory is immutable (the project id is derived from it). There is no delete-project counterpart by design (projects are archived, never deleted). Use when the user wants to tweak a project's metadata from the CLI.
argument-hint: <PROJECT> [--name X|--unset-name] [--color X|--unset-color] [--archive|--unarchive]
---

# TwiCC Update Project

Apply a patch to an existing project by dropping a request file the live TwiCC server picks up. The server validates the patch, applies it under the DB write lock in a single transaction, and broadcasts `project_updated` to every connected UI client.

**Immutable fields:** `directory` (the project id is derived from it via `path_to_project_id`), `git_root`, `sessions_count`, `mtime`, `stale`, `total_cost` — these are server-managed and not touched by this command. Only `name`, `color`, and `archived` are mutable.

**No delete:** projects are bound to their sessions (cost aggregates, workspace memberships, history). Use `--archive` to hide a project from default listings instead.

## When to use

- The user asks to "rename project X", "recolor project X", "archive / unarchive project X", or any combination.
- A script needs to flip archive flags based on activity.

## How to invoke

TwiCC's executable varies by launch mode (uvx, dev, installed tool). Resolve it once:

```bash
TWICC=${TWICC_BIN:-$(command -v twicc 2>/dev/null)}
[ -n "$TWICC" ] || { echo "TwiCC executable not found in this context" >&2; exit 1; }
```

Then run `$TWICC <args>` — **do NOT quote `$TWICC`**.

## Prerequisite: the server must be running

Heartbeat check fails fast (exit 2) if the server is down.

## Basic shape

```bash
$TWICC update-project '<PROJECT>' [OPTIONS]
```

### Required argument

- **`PROJECT`** — the project to update. Either a directory path (absolute or relative; resolved via `realpath` and converted to the canonical id) or a project ID (slug form — get one from `twicc projects`). **When passing an id on the command line, drop the leading dash** (bash would otherwise parse `-home-...` as a flag and the call would fail); the CLI re-adds the dash internally. Prefer the path — that's what the user usually knows; ids are mostly useful when chaining commands.

### Patch flags (combinable, all optional, at least one required)

| Flag | Effect |
|------|--------|
| `--name NEW_NAME` | Set a new display name. Trimmed; ≤ 25 characters; globally unique. Mutually exclusive with `--unset-name`. |
| `--unset-name` | Clear the custom display name (the UI falls back to the directory's basename). Mutually exclusive with `--name`. |
| `--color VALUE` | Set a CSS hex color (`#rgb`, `#rrggbb`, or `#rrggbbaa`). Mutually exclusive with `--unset-color`. |
| `--unset-color` | Clear the project's color. Mutually exclusive with `--color`. |
| `--archive` | Mark as archived. Mutually exclusive with `--unarchive`. |
| `--unarchive` | Mark as not archived. Mutually exclusive with `--archive`. |

### Output controls

| Flag | |
|------|---|
| `--timeout SECONDS` | Seconds to wait for the server's final status (default 30). |
| `--json` | Emit a single JSON object on stdout (implies `--no-color`). |
| `--no-color` | Disable ANSI colors. |

## Atomic semantics

Every flag passed is applied in the **same** `save()` under the DB write lock — no partial state visible mid-operation. If any validation fails, nothing is written.

## Rejections caught locally (exit 1)

- `conflicting_flags` — `--name` and `--unset-name` together, or `--color` and `--unset-color` together, or `--archive` and `--unarchive` together.
- `no_op` — no patch flag was passed (nothing to update).
- `project_not_found` — the PROJECT value doesn't resolve to an existing project (typo on the id, or a path that no known project points to).
- `invalid_name` — `--name` > 25 characters after trim.
- `duplicate_name` — `--name` value is already used by another project.
- `invalid_color` — `--color` value is not a valid hex color.

## Rejections from the server (exit 3)

Same vocabulary. The server re-runs every check under the DB write lock, so a `duplicate_name` race may be reported here even if the local pre-check passed.

## Output format

### Text mode (default)

```
✓ Heartbeat OK (last seen 0.5s ago)
✓ Pre-flight validation passed
→ Request submitted (request_uuid: 4a8352fb...)
✓ Project updated: -home-twidi-dev-newproj
```

### JSON mode (`--json`)

```json
{"status":"updated","project_id":"-home-twidi-dev-newproj","request_uuid":"..."}
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
| 64 | Bad CLI usage |

## Examples

```bash
# Rename only (using the current directory).
$TWICC update-project . --name 'My Project'

# Rename only (by absolute directory path).
$TWICC update-project /home/twidi/dev/myproj --name 'My Project'

# Rename only (by id — handy when chaining commands; dash dropped).
$TWICC update-project home-twidi-dev-myproj --name 'My Project'

# Clear the name (back to basename).
$TWICC update-project /home/twidi/dev/myproj --unset-name

# Recolor only.
$TWICC update-project /home/twidi/dev/myproj --color '#ff9900'

# Clear the color.
$TWICC update-project /home/twidi/dev/myproj --unset-color

# Archive a project — hides it from default listings.
$TWICC update-project /home/twidi/dev/legacy --archive

# Bring it back + recolor in one atomic write.
$TWICC update-project /home/twidi/dev/legacy --unarchive --color '#4a90d9'

# Machine-parseable output for scripts.
$TWICC update-project --json /home/twidi/dev/myproj --name 'Renamed'
# → {"status":"updated","project_id":"-home-twidi-dev-myproj","request_uuid":"..."}
```

## Related commands

- **Create a project:** `twicc create-project <DIRECTORY>` (the `directory` argument is the only way to mint a new project — the id cannot be set directly).
- **Inspect / list projects:** `twicc project <ID>` / `twicc projects` (with `--include-archived`).
- **Add to / remove from a workspace:** `twicc update-workspace <WS_ID> --add-project <PROJECT>` / `--remove-project <PROJECT>` (both accept directory paths or ids — prefer paths).
- **No `delete-project`** — see the design note above. Use `--archive` instead.

## How to present results

1. On success, restate which flags were applied. Mention the change is broadcast — open UI clients update without reload. Offer a clickable link: `[link text](/project/{project_id})`.
2. On `conflicting_flags` / `no_op`, the message is self-explanatory — surface it as-is.
3. On `duplicate_name`, suggest a different name; on `project_not_found`, suggest `twicc projects` to find the right id (the user may have confused the display name with the slug id).
