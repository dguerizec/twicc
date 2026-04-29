---
name: projects
description: List all Claude Code projects tracked by TwiCC. Use when the user wants to see their projects, find a project ID, or get an overview of project activity and costs.
---

# TwiCC Projects

List all projects tracked by TwiCC, ordered by most recently active.

## When to use

- The user asks to list or browse their projects
- The user needs to find a project ID for use with other commands
- The user wants an overview of project activity or costs

## How to list projects

Run the `twicc projects` CLI command via the Bash tool:

```bash
twicc projects
```

### Options

- `--limit N` — max number of projects to return (default: 20)
- `--offset N` — skip first N projects for pagination (default: 0)
- `--include-archived` — include archived projects in the results (default: false, archived projects are excluded)

### Examples

```bash
twicc projects                    # List the 20 most recent projects
twicc projects --limit 50         # List up to 50 projects
twicc projects --offset 20        # Skip the first 20, show next 20
twicc projects --include-archived  # Include archived projects
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
    "total_cost": 12.345678
  }
]
```

### Fields

- **`id`** — project identifier (derived from the `~/.claude/projects/` folder name, starts with a dash). When passing this ID to other commands (`twicc project`, `twicc sessions --project`), **omit the leading dash** — it is added automatically
- **`directory`** — filesystem path of the project
- **`git_root`** — resolved git root directory
- **`sessions_count`** — total number of sessions in this project
- **`mtime`** — last modification timestamp (Unix epoch)
- **`stale`** — `true` if the project folder no longer exists on disk
- **`name`** — user-defined display name (may be `null`)
- **`color`** — CSS color value (may be `null`)
- **`archived`** — whether the project is archived
- **`total_cost`** — total cost across all sessions in USD (may be `null`)

## Related commands

- **Inspect a project:** `twicc project <project_id>` — get full details for one project (omit the leading dash from the project ID)
- **List sessions for a project:** `twicc sessions --project <project_id>` — use the `id` field from the output (omit the leading dash from the project ID)
- **Inspect a specific session:** `twicc session <session_id>` — get full details for one session
- **Search across sessions:** `twicc search "<query>"` — full-text search, can filter by project with `project_id:<id>` in the query

## How to present results

1. Show the project name (or directory if no name) and session count
2. If there are more results than shown, offer to paginate with `--offset`
3. You are in TwiCC, so you can link to a project using a relative Markdown link so the user can click it: `[link text](/project/{project_id})`
4. Only include cost information if the user explicitly asks for it
