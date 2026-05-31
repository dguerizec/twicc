---
name: twicc-update-project
description: Update an existing TwiCC project — rename, change color, archive/unarchive. The directory is immutable. No delete — projects are archived, never deleted. Use when you or the user want to tweak a project's metadata.
argument-hint: <project> [--name X|--unset-name] [--color X|--unset-color] [--archive|--unarchive]
---

# TwiCC Update Project

Patch an existing project. All flags are applied atomically. Only `name`, `color`, and `archived` are mutable — the directory (and therefore the id) is immutable. There is no delete: use `--archive` to hide a project from default listings instead.

## When to use

- You or the user want to rename, recolor, or archive/unarchive a project.
- A script needs to flip archive flags based on activity.

## How to invoke

TwiCC's executable varies by launch mode (uvx, dev, installed tool). ALWAYS USE THIS TO RESOLVE $TWICC AT THE START OF EACH BASH INVOCATION:

```bash
TWICC=${TWICC_BIN:-$(command -v twicc 2>/dev/null)}
[ -n "$TWICC" ] || { echo "TwiCC executable not found in this context" >&2; exit 1; }
```

Then run `$TWICC <args>` — **never quote `$TWICC`** (use `$TWICC args`, never `"$TWICC" args`): it may expand to multiple words, which quoting would break.

## Usage

```bash
$TWICC update-project '<PROJECT>' [OPTIONS]
```

### Arguments

- `PROJECT` — directory path (absolute or relative) or project ID. **Drop the leading dash** on ids — the CLI re-adds it. Prefer paths.

### Options

All patch flags are optional but at least one is required.

- `--name NEW_NAME` — Display name. Trimmed; ≤ 25 characters; globally unique. Mutually exclusive with `--unset-name`.
- `--unset-name` — Clear the display name (UI falls back to directory basename). Mutually exclusive with `--name`.
- `--color VALUE` — CSS hex color (`#rgb`, `#rrggbb`, or `#rrggbbaa`). Mutually exclusive with `--unset-color`.
- `--unset-color` — Clear the color. Mutually exclusive with `--color`.
- `--archive` — Mark as archived. Mutually exclusive with `--unarchive`.
- `--unarchive` — Mark as not archived. Mutually exclusive with `--archive`.
- `--timeout SECONDS` — Seconds to wait for the server's response (default 30).
- `--json` — Emit a single JSON object on stdout (implies `--no-color`).
- `--no-color` — Disable ANSI colors.

## Errors

### Local (exit 1)

- `conflicting_flags` — mutually exclusive flags passed together.
- `no_op` — no patch flag passed.
- `project_not_found` — PROJECT doesn't resolve to an existing project.
- `invalid_name` — name exceeds 25 characters after trim.
- `duplicate_name`
- `invalid_color`

### Server (exit 3)

Same codes, re-checked under the DB write lock.

## Output format

```json
{"status":"updated","project_id":"-home-twidi-dev-newproj","request_uuid":"..."}
{"status":"validation_error","errors":[{"field":"--name","code":"duplicate_name","message":"..."}]}
{"status":"rejected","errors":[{"field":"...","code":"...","message":"..."}],"request_uuid":"..."}
{"status":"failed","error":"...","request_uuid":"..."}
{"status":"timeout","received_seen":true,"message":"...","request_uuid":"..."}
```

### Exit codes

- `0` — Update applied
- `1` — Local validation error
- `2` — TwiCC server not running
- `3` — Server rejected
- `4` — Server error
- `5` — Timeout
- `64` — Bad CLI usage

## Examples

```bash
$TWICC update-project . --name 'My Project'
$TWICC update-project /home/twidi/dev/myproj --name 'My Project'
$TWICC update-project home-twidi-dev-myproj --name 'My Project'  # by id, dash dropped
$TWICC update-project /home/twidi/dev/myproj --unset-name
$TWICC update-project /home/twidi/dev/myproj --color '#ff9900'
$TWICC update-project /home/twidi/dev/myproj --unset-color
$TWICC update-project /home/twidi/dev/legacy --archive
$TWICC update-project /home/twidi/dev/legacy --unarchive --color '#4a90d9'
$TWICC update-project --json /home/twidi/dev/myproj --name 'Renamed'
# → {"status":"updated","project_id":"-home-twidi-dev-myproj","request_uuid":"..."}
```

## Related commands

- `$TWICC create-project <DIRECTORY>` — create a new project. Skill: `twicc-create-project`.
- `$TWICC project <PROJECT>` / `$TWICC projects` — inspect or list. Skill: `twicc-project` / `twicc-projects`.
- `$TWICC update-workspace <ID> --add-project <PROJECT>` / `--remove-project <PROJECT>` — add/remove from a workspace. Skill: `twicc-update-workspace`.

## How to present results

1. On success, give a clickable link: `[link text](/project/{project_id})`.
2. On `conflicting_flags` or `no_op`, the error is self-explanatory.
3. On `project_not_found`, suggest `$TWICC projects` to find the right id.
