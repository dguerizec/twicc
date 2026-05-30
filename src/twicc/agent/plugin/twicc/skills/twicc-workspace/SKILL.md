---
name: twicc-workspace
description: Show details of a single TwiCC workspace by ID. Use when the user wants to inspect a specific workspace's projects, color, archived state, or auto-add patterns.
argument-hint: <workspace_id>
---

# TwiCC Workspace

Show the full details of a single workspace by its ID. Returns the workspace even when archived.

## When to use

- The user wants details about a specific workspace
- The user has a workspace ID (from `twicc workspaces` output, or from the `workspaces` field of a project) and wants to see its content (projects, patterns, etc.)

## How to invoke

TwiCC's executable varies by launch mode (uvx, dev, installed tool). Resolve it once at the start of each Bash invocation:

```bash
TWICC=${TWICC_BIN:-$(command -v twicc 2>/dev/null)}
[ -n "$TWICC" ] || { echo "TwiCC executable not found in this context" >&2; exit 1; }
```

Then run any subcommand via `$TWICC <args>` — **do NOT quote `$TWICC`** (use `$TWICC args`, never `"$TWICC" args`). The variable may expand to a multi-word command (e.g. `uv run --directory ... run.py`); Bash relies on word-splitting to parse it, and quoting it would treat the entire expansion as a single program name and fail with "No such file or directory". All bash examples below use the unquoted form.

## How to inspect a workspace

Run the `twicc workspace` CLI command via the Bash tool:

```bash
$TWICC workspace <WORKSPACE_ID>
```

The `<WORKSPACE_ID>` is the slug-style identifier shown by `twicc workspaces` (e.g. `backend`, `home-side-projects`). **The ID does NOT change when the workspace is renamed**, so it may look unrelated to the current `name`.

### Examples

```bash
$TWICC workspace backend
$TWICC workspace home-side-projects
```

## Output format

The command outputs a single JSON workspace object:

```json
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
```

### Fields

- **`id`** — workspace identifier (slug derived from the original name at creation time; never updated on rename)
- **`name`** — user-facing workspace name (mutable)
- **`archived`** — whether the workspace is archived
- **`color`** — optional CSS color value (may be `null`)
- **`projectIds`** — list of project IDs that belong to this workspace. These match what `twicc projects` returns (leading dash included). **Omit the leading dash** when passing them to `twicc project <id>` or `twicc sessions --project <id>` — it is added automatically.
- **`autoProjectPatterns`** — optional list of directory patterns (with `*` wildcards) used to auto-add newly detected projects. A pattern without `*` is treated as a directory prefix.

## Related commands

- **List all workspaces:** `twicc workspaces` — find workspace IDs
- **Inspect a project in the workspace:** `twicc project <project_id>` — use any entry from `projectIds` (omit the leading dash)
- **List sessions for a project in the workspace:** `twicc sessions --project <project_id>` (omit the leading dash)
- **Update this workspace:** `twicc update-workspace <ID> [--name X] [--color X|--unset-color] [--add-project PID]... [--remove-project PID]... [--add-pattern P]... [--remove-pattern P]... [--archive|--unarchive]`
- **Delete this workspace:** `twicc delete-workspace <ID>` — non-destructive for the underlying projects
- **Create a new workspace:** `twicc create-workspace <NAME> [...]`

## How to present results

1. Show the workspace name and the count of projects (length of `projectIds`)
2. Optionally list the project IDs (or fetch each with `twicc project` for more context)
3. If `autoProjectPatterns` is set, mention that the workspace auto-grows for matching directories
4. You are in TwiCC, so you can link to the workspace using a relative Markdown link: `[link text](/workspace/{workspace_id})`
