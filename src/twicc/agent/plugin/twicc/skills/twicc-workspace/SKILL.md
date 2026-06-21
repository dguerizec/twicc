---
name: twicc-workspace
description: Show details of a single TwiCC workspace by ID. Use when you or the user want to inspect a workspace's projects, color, archived state, or auto-add patterns.
argument-hint: <workspace_id>
---

# TwiCC Workspace

Show the full details of a single workspace by its ID, including when archived.

## When to use

- You or the user want details about a specific workspace.
- You have a workspace ID (from `$TWICC workspaces` or from a project's `workspaces` field) and want to see its content.

## How to invoke

TwiCC's executable varies by launch mode (uvx, dev, installed tool). ALWAYS USE THIS TO RESOLVE $TWICC AT THE START OF EACH BASH INVOCATION:

```bash
TWICC=${TWICC_BIN:-$(command -v twicc 2>/dev/null)}
[ -n "$TWICC" ] || { echo "TwiCC executable not found in this context" >&2; exit 1; }
```

Then run `$TWICC <args>` — **never quote `$TWICC`** (use `$TWICC args`, never `"$TWICC" args`): it may expand to multiple words, which quoting would break.

## Usage

```bash
$TWICC workspace <WORKSPACE_ID>
```

The `WORKSPACE_ID` is the slug shown by `$TWICC workspaces`. **The ID does NOT change when the workspace is renamed**, so it may look unrelated to the current name.

## Output format

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

- `id` — slug derived from the name at creation time; never changes on rename.
- `name` — display name (mutable).
- `color` — may be `null`.
- `projectIds` — project IDs (leading dash included). **Drop the leading dash** when passing on the command line; prefer directory paths when possible.
- `autoProjectPatterns` — auto-add patterns (`*` wildcard). A pattern without `*` is treated as a directory prefix.

## Related commands

- `$TWICC workspaces` — list all workspaces. Skill: `twicc-workspaces`.
- `$TWICC update-workspace <ID>` — rename, recolor, add/remove projects or patterns, archive/unarchive. Skill: `twicc-update-workspace`.
- `$TWICC delete-workspace <ID>` — delete a workspace. Skill: `twicc-delete-workspace`.
- `$TWICC create-workspace <NAME>` — create a new workspace. Skill: `twicc-create-workspace`.
- `$TWICC project <PROJECT>` — inspect a project from `projectIds`. Skill: `twicc-project`.
- `$TWICC sessions --project <PROJECT>` — list sessions for a project. Skill: `twicc-sessions`.
- `$TWICC sessions --workspace <ID>` — list sessions for a workspace.

## How to present results

1. Show the workspace name and project count (length of `projectIds`).
2. If `autoProjectPatterns` is set, mention that the workspace auto-grows for matching directories.
3. You are in TwiCC — link to the workspace: `[link text](/projects?workspace={workspace_id})`.
