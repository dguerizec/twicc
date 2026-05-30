---
name: twicc-project
description: Show details of a single project by directory path or by ID. Use when the user wants to inspect a specific project's metadata, cost, or directory.
argument-hint: <project_path_or_id>
---

# TwiCC Project

Show the details of a single project by its ID.

## When to use

- The user wants details about a specific project (most often by directory path — e.g. the current working directory)
- The user has a project ID (from `twicc projects` or `twicc sessions` output) and wants to see its metadata — useful when chaining commands; for any other case, prefer passing the path

## How to invoke

TwiCC's executable varies by launch mode (uvx, dev, installed tool). Resolve it once at the start of each Bash invocation:

```bash
TWICC=${TWICC_BIN:-$(command -v twicc 2>/dev/null)}
[ -n "$TWICC" ] || { echo "TwiCC executable not found in this context" >&2; exit 1; }
```

Then run any subcommand via `$TWICC <args>` — **do NOT quote `$TWICC`** (use `$TWICC args`, never `"$TWICC" args`). The variable may expand to a multi-word command (e.g. `uv run --directory ... run.py`); Bash relies on word-splitting to parse it, and quoting it would treat the entire expansion as a single program name and fail with "No such file or directory". All bash examples below use the unquoted form.

## How to inspect a project

Run the `twicc project` CLI command via the Bash tool:

```bash
$TWICC project <PROJECT>
```

`<PROJECT>` is either a directory path or a project ID. Prefer the path — it's what the user usually knows; the id form is mostly useful when you're chaining `twicc` commands that emit ids.

- **Directory path** — absolute or relative; resolved via `realpath` and converted to the canonical project ID.
- **Project ID** — the canonical id starts with a dash (e.g. `-home-twidi-dev-myproject`), but **you must drop the leading dash when passing it on the command line** (bash would otherwise parse `-home-...` as a flag and the call would fail with `No such option`). The CLI re-adds the dash internally.

### Examples

```bash
$TWICC project .                                   # by current directory
$TWICC project /home/twidi/dev/myproject           # by absolute path
$TWICC project 'home-twidi-dev-myproject-abc123'   # by id (dash omitted)
```

## Output format

The command outputs a single JSON project object:

```json
{
  "id": "-home-twidi-dev-myproject-abc123",
  "directory": "/home/twidi/dev/myproject",
  "git_root": "/home/twidi/dev/myproject",
  "sessions_count": 42,
  "mtime": 1741654800.0,
  "stale": false,
  "name": "My Project",
  "color": "#4a90d9",
  "archived": false,
  "total_cost": 12.345678,
  "workspaces": ["backend", "home-side-projects"]
}
```

### Fields

- **`id`** — project identifier (derived from the project's working directory path, with every non-alphanumeric character replaced by a dash)
- **`directory`** — filesystem path of the project
- **`git_root`** — resolved git root directory
- **`sessions_count`** — total number of sessions in this project
- **`mtime`** — last modification timestamp (Unix epoch)
- **`stale`** — `true` if the project folder no longer exists on disk
- **`name`** — user-defined display name (may be `null`)
- **`color`** — CSS color value (may be `null`)
- **`archived`** — whether the project is archived
- **`total_cost`** — total cost across all sessions in USD (may be `null`)
- **`workspaces`** — list of workspace IDs this project belongs to (empty when the project is not in any workspace). Pass any entry to `twicc workspace <id>` for details.

## Related commands

- **Find project IDs:** `twicc projects` — list all projects
- **List sessions for this project:** `twicc sessions --project <project_path_or_id>` — browse sessions in this project (pass the directory path, or the project id if you already have it; omit the leading dash if you do use the id)
- **Inspect a session:** `twicc session <session_id>` — get full details for one session
- **Inspect a workspace:** `twicc workspace <workspace_id>` — use any value from the `workspaces` field to see the workspace's name, color, and full project list
- **List all workspaces:** `twicc workspaces` — find workspace IDs
- **Search within this project:** `twicc search "project_id:<project_id> AND <query>"` — full-text search filtered to this project
- **Update this project:** `twicc update-project <PROJECT> [--name X|--unset-name] [--color X|--unset-color] [--archive|--unarchive]` — `<PROJECT>` accepts a directory path or an id (no `delete-project` — projects are archived, never deleted)
- **Create a new project:** `twicc create-project <DIRECTORY> [...]`

## How to present results

1. Show the project name (or directory if no name) and session count
2. You are in TwiCC, so you can link to the project using a relative Markdown link: `[link text](/project/{project_id})`
3. Only include cost information if the user explicitly asks for it
