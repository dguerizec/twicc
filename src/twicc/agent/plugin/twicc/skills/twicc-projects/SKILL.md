---
name: twicc-projects
description: List all projects tracked by TwiCC, or batch-look up specific project_ids (with a `known: false` placeholder for any id that doesn't exist). Use when the user wants to see their projects, find a project ID, batch-fetch metadata for known ids, or get an overview of project activity and costs.
---

# TwiCC Projects

List all projects tracked by TwiCC, ordered by most recently active. A project corresponds to a working directory and is shared by every provider (Claude Code, Codex, ...) that has run sessions inside it.

## When to use

- The user asks to list or browse their projects
- The user needs to find a project ID for use with other commands
- The user wants an overview of project activity or costs
- The user (or a script) has a list of known project_ids and wants to batch-fetch their metadata — use `projects get <ID>...` (one entry per id, placeholder when missing; archived projects are returned too since you named them explicitly)

## How to invoke

TwiCC's executable varies by launch mode (uvx, dev, installed tool). Resolve it once at the start of each Bash invocation:

```bash
TWICC=${TWICC_BIN:-$(command -v twicc 2>/dev/null)}
[ -n "$TWICC" ] || { echo "TwiCC executable not found in this context" >&2; exit 1; }
```

Then run any subcommand via `$TWICC <args>` — **do NOT quote `$TWICC`** (use `$TWICC args`, never `"$TWICC" args`). The variable may expand to a multi-word command (e.g. `uv run --directory ... run.py`); Bash relies on word-splitting to parse it, and quoting it would treat the entire expansion as a single program name and fail with "No such file or directory". All bash examples below use the unquoted form.

## How to list projects

Run the `twicc projects` CLI command via the Bash tool:

```bash
$TWICC projects
```

### Options

- `--limit N` — max number of projects to return (default: 20)
- `--offset N` — skip first N projects for pagination (default: 0)
- `--include-archived` — include archived projects in the results (default: false, archived projects are excluded)
- `--workspace ID` — only return projects belonging to the given workspace (use `twicc workspaces` to find the ID). Fails if the workspace does not exist.

### Examples

```bash
$TWICC projects                    # List the 20 most recent projects
$TWICC projects --limit 50         # List up to 50 projects
$TWICC projects --offset 20        # Skip the first 20, show next 20
$TWICC projects --include-archived # Include archived projects
$TWICC projects --workspace backend # Only projects in the "backend" workspace
```

## How to look up specific project_ids

When you already know which projects you care about, use the `get`
sub-command instead of listing + post-filtering. Each requested
project_id produces exactly one entry in the output, in the order you
passed them (duplicates collapsed, first occurrence wins):

```bash
$TWICC projects get <PROJECT_ID> [<PROJECT_ID>...]
```

Examples:

```bash
$TWICC projects get home-twidi-dev-myproj            # Single project (leading dash auto-prepended)
$TWICC projects get -home-twidi-dev-myproj           # Also accepted (leading dash explicit)
$TWICC projects get home-twidi-dev-a home-twidi-dev-b  # Batch
```

Unlike `twicc projects`, `get` accepts **no filter flags** — when you
name the projects you care about, the archived-by-default filter
doesn't apply: archived projects are returned just like active ones
(mirrors the singular `twicc project <ID>` scope).

### Output

A JSON array, one entry per project_id, in the order you passed them
(duplicates collapsed). All entries share the same shape — full
project metadata when the id exists, the same shape with everything
nulled out when it doesn't, plus a `known: bool` flag:

```json
[
  {
    "id": "-home-twidi-dev-myproject-abc123",
    "directory": "/home/twidi/dev/myproject",
    "name": "My Project",
    ... (every field from the listing) ...,
    "workspaces": ["backend"],
    "known": true
  },
  {
    "id": "-typo-or-unknown",
    "directory": null,
    "name": null,
    ... (all other fields: null) ...,
    "workspaces": null,
    "known": false
  }
]
```

Because the output is 1-to-1 with the input order, callers can `zip`
it with the input list with no re-mapping:

```python
import json, subprocess
ids = ["-foo", "-bar", "-baz"]
out = json.loads(subprocess.check_output([twicc, "projects", "get", *ids]))
for pid, entry in zip(ids, out):
    if not entry["known"]:
        print(f"  WARN: {pid} unknown to TwiCC")
    else:
        print(f"  {pid}: {entry['name'] or entry['directory']}")
```

## Output format

The command outputs a JSON array of project objects:

```json
[
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
]
```

### Fields

- **`id`** — project identifier (derived from the project's working directory path, with every non-alphanumeric character replaced by a dash; therefore starts with a dash for absolute paths). When passing this ID to other commands (`twicc project`, `twicc sessions --project`), **omit the leading dash** — it is added automatically
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

- **Inspect a single project (errors out if missing):** `twicc project <project_id>` — get full details for one project (omit the leading dash from the project ID), exit 1 if not found. Use when "project not found" should be a hard failure. For batch lookup that tolerates missing ids, see `projects get` above
- **List sessions for a project:** `twicc sessions --project <project_id>` — use the `id` field from the output (omit the leading dash from the project ID)
- **Inspect a specific session:** `twicc session <session_id>` — get full details for one session
- **Inspect a workspace:** `twicc workspace <workspace_id>` — use any value from the `workspaces` field to see the workspace's name, color, and full project list
- **List all workspaces:** `twicc workspaces` — find workspace IDs
- **Search across sessions:** `twicc search "<query>"` — full-text search, can filter by project with `project_id:<id>` in the query
- **Create a new project:** `twicc create-project <DIRECTORY> [--name X] [--color X] [--create-directory]`
- **Update a project:** `twicc update-project <ID> [--name X|--unset-name] [--color X|--unset-color] [--archive|--unarchive]`

## How to present results

1. Show the project name (or directory if no name) and session count
2. If there are more results than shown, offer to paginate with `--offset`
3. You are in TwiCC, so you can link to a project using a relative Markdown link so the user can click it: `[link text](/project/{project_id})`
4. Only include cost information if the user explicitly asks for it
5. For `projects get` output: scan for `known: false` entries (surface as "project X is unknown to TwiCC — typo or never existed"). Known entries render like the listing
