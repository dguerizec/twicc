---
name: twicc-project
description: Show details of a single project by directory path or by ID. Use when you or the user want to inspect a specific project's metadata, cost, or directory.
argument-hint: <project_path_or_id>
---

# TwiCC Project

Show the details of a single project. Accepts a directory path or a project ID.

## When to use

- You or the user want details about a specific project.
- You have a project ID (from `$TWICC projects` or `$TWICC sessions` output) and want its metadata — prefer paths in all other cases.

## How to invoke

TwiCC's executable varies by launch mode (uvx, dev, installed tool). ALWAYS USE THIS TO RESOLVE $TWICC AT THE START OF EACH BASH INVOCATION:

```bash
TWICC=${TWICC_BIN:-$(command -v twicc 2>/dev/null)}
[ -n "$TWICC" ] || { echo "TwiCC executable not found in this context" >&2; exit 1; }
```

Then run `$TWICC <args>` — **never quote `$TWICC`** (use `$TWICC args`, never `"$TWICC" args`): it may expand to multiple words, which quoting would break.

## Usage

```bash
$TWICC project <PROJECT>
```

- `PROJECT` — directory path (absolute or relative) or project ID. **Drop the leading dash** on ids — the CLI re-adds it. Prefer paths.

## Output format

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
  "worktree_of": null,
  "trust": null,
  "trust_propagation": false,
  "default_provider": null,
  "default_agent_settings": {"claude_code": {"permission_mode": "acceptEdits", "effort": "high"}},
  "workspaces": ["backend", "home-side-projects"],
  "worktrees": ["-home-twidi-dev-myproject--worktrees-feature-x"]
}
```

### Fields

- `id` — derived from the directory path (non-alphanumeric chars replaced by dashes).
- `stale` — `true` if the project folder no longer exists on disk.
- `name` — may be `null`.
- `color` — may be `null`.
- `total_cost` — total cost in USD across all sessions (may be `null`).
- `worktree_of` — when this project is a git worktree, the project id of its main repository; `null` otherwise.
- `worktrees` — the project ids of this project's own git worktrees (the reverse of `worktree_of`); empty when it has none. A worktree's sessions, cost and activity count toward its main repository.
- `trust` — the project's own trust decision: `true` = trusted, `false` = untrusted, `null` = no own decision (inherits from its parent directory / git root). **Read-only here, and human-only to change** — agents never set trust. A session created in an untrusted (or `null` / undecided) project runs under a restricted permission set (`bypassPermissions` / `yolo` unavailable).
- `trust_propagation` — whether an explicit `trust` decision also covers the project's sub-paths and its git worktrees.
- `default_provider` — provider a NEW session in this project defaults to; `null` = inherit from the parent chain (worktree main repo / path ancestors), ultimately the global default. Editable via `$TWICC update-project <PROJECT> --default-provider X`.
- `default_agent_settings` — per-provider agent-settings defaults that seed NEW sessions created here (`{"<provider>": {"<field>": value}}` or `null`); a missing field inherits from the parent chain, then the global default. Includes the optional `permission_mode_if_untrusted` key (the permission mode seeded instead of `permission_mode` when the project resolves untrusted). Creation-time only: changing these never affects existing sessions. Editable via `$TWICC update-project <PROJECT> settings`.
- `workspaces` — workspace IDs this project belongs to (empty if none).

## Examples

```bash
$TWICC project .
$TWICC project /home/twidi/dev/myproject
$TWICC project 'home-twidi-dev-myproject-abc123'  # by id, dash dropped
```

## Related commands

- `$TWICC projects` — list all projects. Skill: `twicc-projects`.
- `$TWICC update-project <PROJECT>` — rename, recolor, archive/unarchive. Skill: `twicc-update-project`.
- `$TWICC create-project <DIRECTORY>` — create a new project. Skill: `twicc-create-project`.
- `$TWICC sessions --project <PROJECT>` — list sessions for this project. Skill: `twicc-sessions`.
- `$TWICC workspace <ID>` — inspect a workspace from the `workspaces` field. Skill: `twicc-workspace`.
- `$TWICC search "project_id:<id> AND <query>"` — search within this project. Skill: `twicc-search`.

## How to present results

1. Show the project name (or directory if no name) and session count.
2. You are in TwiCC — link to the project: `[link text](/project/{project_id})`.
3. Only include cost information if explicitly asked.
