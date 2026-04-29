---
name: project
description: Show details of a single Claude Code project by ID. Use when the user wants to inspect a specific project's metadata, cost, or directory.
argument-hint: <project_id>
---

# TwiCC Project

Show the details of a single project by its ID.

## When to use

- The user wants details about a specific project
- The user has a project ID (from `twicc projects` or `twicc sessions` output) and wants to see its metadata

## How to inspect a project

Run the `twicc project` CLI command via the Bash tool:

```bash
twicc project <PROJECT_ID>
```

**Note:** Project IDs start with a dash (e.g. `-home-twidi-dev-myproject`). The leading dash is automatically prepended if omitted, so **do not include it** when passing the ID on the command line.

### Examples

```bash
twicc project "home-twidi-dev-myproject-abc123"
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
  "total_cost": 12.345678
}
```

### Fields

- **`id`** — project identifier (derived from the `~/.claude/projects/` folder name)
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

- **Find project IDs:** `twicc projects` — list all projects
- **List sessions for this project:** `twicc sessions --project <project_id>` — browse sessions in this project (omit the leading dash from the project ID)
- **Inspect a session:** `twicc session <session_id>` — get full details for one session
- **Search within this project:** `twicc search "project_id:<project_id> AND <query>"` — full-text search filtered to this project

## How to present results

1. Show the project name (or directory if no name) and session count
2. You are in TwiCC, so you can link to the project using a relative Markdown link: `[link text](/project/{project_id})`
3. Only include cost information if the user explicitly asks for it
