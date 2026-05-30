---
name: twicc-workspaces
description: List all TwiCC workspaces — user-defined groups of projects — or batch-look up specific workspace_ids (with a `known: false` placeholder for any id that doesn't exist). Use when the user wants to browse their workspaces, find a workspace ID, batch-fetch metadata for known ids, or see which workspaces a project belongs to.
---

# TwiCC Workspaces

List all workspaces tracked by TwiCC. A workspace is a user-defined group of projects, optionally with auto-add patterns that match new project directories.

## When to use

- The user asks to list or browse their workspaces
- The user needs to find a workspace ID for use with other commands
- The user wants to see which projects are grouped into which workspaces
- The user (or a script) has a list of known workspace_ids and wants to batch-fetch their definitions — use `workspaces get <ID>...` (one entry per id, placeholder when missing; archived workspaces are returned too)

## How to invoke

TwiCC's executable varies by launch mode (uvx, dev, installed tool). Resolve it once at the start of each Bash invocation:

```bash
TWICC=${TWICC_BIN:-$(command -v twicc 2>/dev/null)}
[ -n "$TWICC" ] || { echo "TwiCC executable not found in this context" >&2; exit 1; }
```

Then run any subcommand via `$TWICC <args>` — **do NOT quote `$TWICC`** (use `$TWICC args`, never `"$TWICC" args`). The variable may expand to a multi-word command (e.g. `uv run --directory ... run.py`); Bash relies on word-splitting to parse it, and quoting it would treat the entire expansion as a single program name and fail with "No such file or directory". All bash examples below use the unquoted form.

## How to list workspaces

Run the `twicc workspaces` CLI command via the Bash tool:

```bash
$TWICC workspaces
```

### Options

- `--limit N` — max number of workspaces to return (default: 20)
- `--offset N` — skip first N workspaces for pagination (default: 0)
- `--include-archived` — include archived workspaces in the results (default: false, archived workspaces are excluded)

### Examples

```bash
$TWICC workspaces                    # List up to 20 non-archived workspaces
$TWICC workspaces --limit 50         # List up to 50
$TWICC workspaces --include-archived # Include archived workspaces
```

## How to look up specific workspace_ids

When you already know which workspaces you care about, use the `get`
sub-command instead of listing + post-filtering. Each requested
workspace_id produces exactly one entry in the output, in the order
you passed them (duplicates collapsed, first occurrence wins):

```bash
$TWICC workspaces get <WORKSPACE_ID> [<WORKSPACE_ID>...]
```

Examples:

```bash
$TWICC workspaces get backend                       # Single workspace
$TWICC workspaces get backend frontend devops       # Batch lookup
```

Unlike `twicc workspaces`, `get` accepts **no filter flags** — when
you name the workspaces you care about, the archived-by-default
filter doesn't apply: archived workspaces are returned just like
active ones (mirrors the singular `twicc workspace <ID>` scope).

### Output

A JSON array, one entry per workspace_id, in the order you passed
them (duplicates collapsed). All entries share the same shape — full
workspace definition when the id exists, the same shape with
everything nulled out when it doesn't, plus a `known: bool` flag:

```json
[
  {
    "id": "backend",
    "name": "Backend",
    "archived": false,
    "color": "#4a90d9",
    "projectIds": ["-home-twidi-dev-api"],
    "autoProjectPatterns": ["/home/twidi/dev/api*"],
    "known": true
  },
  {
    "id": "typo-or-unknown",
    "name": null,
    "archived": null,
    "color": null,
    "projectIds": null,
    "autoProjectPatterns": null,
    "known": false
  }
]
```

Because the output is 1-to-1 with the input order, callers can `zip`
it with the input list with no re-mapping:

```python
import json, subprocess
ids = ["backend", "frontend", "typo"]
out = json.loads(subprocess.check_output([twicc, "workspaces", "get", *ids]))
for wid, entry in zip(ids, out):
    if not entry["known"]:
        print(f"  WARN: {wid} unknown to TwiCC")
    else:
        print(f"  {wid}: {entry['name']}")
```

## Output format

The command outputs a JSON array of workspace objects (in their stored order):

```json
[
  {
    "id": "backend",
    "name": "Backend",
    "archived": false,
    "color": "#4a90d9",
    "projectIds": [
      "-home-twidi-dev-api",
      "-home-twidi-dev-workers"
    ],
    "autoProjectPatterns": [
      "/home/twidi/dev/api*"
    ]
  }
]
```

### Fields

- **`id`** — workspace identifier (slug derived from the workspace name at creation time: lowercased, non-alphanumeric chars replaced with dashes, duplicates suffixed `-2`, `-3`, ...). **The ID does NOT change when the workspace is renamed**, so it may look unrelated to the current name.
- **`name`** — user-facing workspace name (mutable, the source of the original slug)
- **`archived`** — whether the workspace is archived (defaults to `false`; excluded from default listing)
- **`color`** — optional CSS color value (may be `null`)
- **`projectIds`** — list of project IDs that belong to this workspace. These are the same IDs returned by `twicc projects` (with their leading dash). **Drop the leading dash when passing them on the command line** (bash would otherwise parse `-home-...` as a flag and reject the call); the CLI re-adds the dash internally. For `twicc project`, `twicc sessions --project`, `twicc update-project`, and `--add-project` / `--remove-project` on workspace commands, you can also pass a directory path directly — usually simpler than chasing the id.
- **`autoProjectPatterns`** — optional list of directory patterns (with `*` wildcards) used to auto-add newly detected projects to the workspace. A pattern without `*` is treated as a directory prefix.

## Related commands

- **Inspect a single workspace (errors out if missing):** `twicc workspace <workspace_id>` — get full details for one workspace, exit 1 if not found. Use when "workspace not found" should be a hard failure. For batch lookup that tolerates missing ids, see `workspaces get` above
- **List projects in a workspace:** for each `projectIds` entry, use `twicc sessions --project <project_path_or_id>` or `twicc project <project_path_or_id>` for details — both accept a directory path; if you pass the id, drop the leading dash
- **List all projects (with workspace memberships):** `twicc projects` — each project's `workspaces` field lists the workspace IDs it belongs to
- **Create a new workspace:** `twicc create-workspace <NAME> [--color X] [--add-project PROJECT]... [--add-pattern P]...` — `--add-project` accepts a directory path or an id (drop the leading dash on ids)
- **Update a workspace:** `twicc update-workspace <ID> [--name X] [--color X|--unset-color] [--add-project PROJECT]... [--remove-project PROJECT]... [--add-pattern P]... [--remove-pattern P]... [--archive|--unarchive]` — `--add-project` / `--remove-project` accept a directory path or an id (drop the leading dash on ids)
- **Delete a workspace:** `twicc delete-workspace <ID>`

## How to present results

1. Show the workspace name and number of projects (length of `projectIds`)
2. If a workspace has `autoProjectPatterns`, mention it (it auto-grows when new matching projects appear)
3. You are in TwiCC, so you can link to a workspace using a relative Markdown link so the user can click it: `[link text](/workspace/{workspace_id})`
4. If there are more results than shown, offer to paginate with `--offset`
5. For `workspaces get` output: scan for `known: false` entries (surface as "workspace X is unknown to TwiCC — typo or never existed"). Known entries render like the listing
