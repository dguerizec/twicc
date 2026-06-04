# TwiCC skills & CLI

> Drive TwiCC from a terminal or from inside an agent — the `twicc` command-line interface and the matching agent-skill plugin are two front doors to the same surface.

TwiCC ships a command-line interface (every `twicc <command>`) **and** a Claude Code / Codex plugin (auto-installed) whose skills wrap those same commands. Anything a skill lets an agent do, you can do from a shell with the exact same `twicc` command — the skills are guided wrappers around the CLI, not a separate capability set. That is why they are documented together here: each entry lists both its command and its **Skill**.

Two audiences, one surface:

- **You, in a terminal** — compose `twicc` into scripts; every list/inspect command prints JSON.
- **An agent, mid-session** — the plugin skills teach the agent the same commands, plus the `self` / `parent` keywords so it can act on its own session and its parent without knowing any id up front.

## Conventions

- **JSON output.** List and inspect commands print JSON on stdout. Write commands (create / update / send / stop / …) accept `--json` to emit a single JSON object instead of pretty text (`--json` implies `--no-color`).
- **Read vs write.** Read commands query TwiCC's database directly and work whether or not a backend is running (a few, like live process state, need the backend). Write commands drop a request file that the **running** TwiCC server picks up, then poll for the server's final status — they need a live backend and accept `--timeout` (default 30 s). If the deadline passes the request stays on disk and may still apply server-side.
- **Exit codes.** `0` success; non-zero on failure (typically `1` not-found / validation, `2` backend down, `5` timeout). Run `twicc <command> --help` for a command's exact codes.
- **Catalogues drift.** The model / effort / permission / preset lists shown below are the current built-ins; the live source of truth is always `twicc info` (see below).

## Resolving the executable (`$TWICC`)

The `twicc` executable's path depends on how TwiCC was launched (`uvx`, `uv tool install`, a dev `uv run`, an absolute path). From inside an agent's Bash tool, resolve it once at the start of each invocation:

```bash
TWICC=${TWICC_BIN:-$(command -v twicc 2>/dev/null)}
[ -n "$TWICC" ] || { echo "TwiCC executable not found in this context" >&2; exit 1; }
```

Then run `$TWICC <args>` — **never quote `$TWICC`** (it may expand to multiple words). `twicc whoami` also returns the canonical invocation under `twicc_executable`.

## Acting from inside a session: `self` and `parent`

Commands that target a session accept two keywords resolved via PID ancestry, so an agent never needs to know its own id:

- **`self`** — the current session. Accepted by `update-session`, `topology`, and the `--spawned-by` / `--spawn-tree` / `--descendants` filters.
- **`parent`** — the session that spawned the current one. Accepted by `send-message` and the filiation filters.

`twicc whoami` is the explicit way for an agent to discover its own `session_id`, working directories, settings, and live process row.

---

# Reference

## Discovery & self-knowledge

### `twicc info [SECTION...]`
Inspect TwiCC's per-provider catalogues. Sections: `presets`, `commands`, `models`, `agent-settings`, `all`. Output always carries `twicc_version` and `providers` (with enabled/disabled + default flags); each named section adds its key.
- `--provider TEXT` — filter every section by provider key (naming one bypasses the disabled-provider filter).
- `--project TEXT` — only for the `commands` section: also list commands scoped to that project.
- `--filter TEXT` — case-insensitive substring filter for `commands` (whitespace-separated tokens are ANDed).
- `--include-disabled-providers` — include disabled providers in the section payloads.
- Skill: [`twicc-info`](src/twicc/agent/plugin/twicc/skills/twicc-info/SKILL.md).

### `twicc status`
Report the live backend's state as JSON (running / starting / stale / dead_pid / not_running). Pure file reads, safe to call concurrently. Exit `0` only when fully running, so it works as a shell gate.
- Skill: [`twicc-status`](src/twicc/agent/plugin/twicc/skills/twicc-status/SKILL.md).

### `twicc usage`
Show the latest usage quota snapshot (quotas, burn rate, cost estimates) for every provider as JSON.
- Skill: [`twicc-usage`](src/twicc/agent/plugin/twicc/skills/twicc-usage/SKILL.md).

### `twicc whoami`
Print details of the session that owns the calling process — `session_id`, `title`, `project_id`, `project_directory`, `current_working_directory`, `artifacts_dir`, `scratch_dir`, `orchestration_scratch_dir` (when part of an orchestration), the resolved `agent_settings`, the full `session` payload, and the live `process` row. Exits `1` from a plain terminal (only meaningful inside a session).
- `--json` — emit a single JSON object.
- Skill: [`twicc-whoami`](src/twicc/agent/plugin/twicc/skills/twicc-whoami/SKILL.md).

## Projects

### `twicc projects` / `twicc projects get <PROJECT...>`
List projects, or batch-look up specific ones.
- Listing: `--limit` (default 20), `--offset`, `--include-archived`, `--workspace TEXT` (only projects in that workspace).
- `get`: takes one or more project ids or directory paths (no filter flags); each input yields one entry in input order, archived included, with `known: false` placeholders for misses.
- Skill: [`twicc-projects`](src/twicc/agent/plugin/twicc/skills/twicc-projects/SKILL.md).

### `twicc project <PROJECT_ID>`
Show a single project as JSON. Accepts a project id (with or without leading dash) or a directory path.
- Skill: [`twicc-project`](src/twicc/agent/plugin/twicc/skills/twicc-project/SKILL.md).

### `twicc create-project <DIRECTORY>`
Register a directory as a TwiCC project (id derived from the canonical realpath; one project per directory).
- `--name TEXT` (≤ 25 chars, globally unique), `--color TEXT` (CSS hex), `--create-directory` (mkdir if missing).
- Plus the write-command flags `--timeout`, `--no-color`, `--json`.
- Skill: [`twicc-create-project`](src/twicc/agent/plugin/twicc/skills/twicc-create-project/SKILL.md).

### `twicc update-project <PROJECT>`
Update a project's name, color, and/or archived state. The directory is immutable; there is no delete (projects are archived, never removed).
- `--name TEXT` / `--unset-name`, `--color TEXT` / `--unset-color`, `--archive` / `--unarchive` (each pair mutually exclusive).
- Plus `--timeout`, `--no-color`, `--json`.
- Skill: [`twicc-update-project`](src/twicc/agent/plugin/twicc/skills/twicc-update-project/SKILL.md).

## Workspaces

### `twicc workspaces` / `twicc workspaces get <WORKSPACE_ID...>`
List workspaces, or batch-look up specific ones.
- Listing: `--limit` (default 20), `--offset`, `--include-archived`.
- `get`: one or more ids (no filter flags), input order preserved, archived included, `known: false` for misses.
- Skill: [`twicc-workspaces`](src/twicc/agent/plugin/twicc/skills/twicc-workspaces/SKILL.md).

### `twicc workspace <WORKSPACE_ID>`
Show a single workspace as JSON.
- Skill: [`twicc-workspace`](src/twicc/agent/plugin/twicc/skills/twicc-workspace/SKILL.md).

### `twicc create-workspace <NAME>`
Create a workspace (name trimmed, ≤ 20 chars, unique; id slugified from the name).
- `--color TEXT`, `--add-project TEXT` (repeatable; id or path, must already exist), `--add-pattern TEXT` (repeatable auto-add directory glob), `--archived`.
- Plus `--timeout`, `--no-color`, `--json`.
- Skill: [`twicc-create-workspace`](src/twicc/agent/plugin/twicc/skills/twicc-create-workspace/SKILL.md).

### `twicc update-workspace <WORKSPACE_ID>`
Update a workspace. Flags combine into a single atomic edit.
- `--name TEXT`, `--color TEXT` / `--unset-color`, `--add-project` / `--remove-project` (repeatable; id or path), `--add-pattern` / `--remove-pattern` (repeatable), `--archive` / `--unarchive`.
- Plus `--timeout`, `--no-color`, `--json`.
- Skill: [`twicc-update-workspace`](src/twicc/agent/plugin/twicc/skills/twicc-update-workspace/SKILL.md).

### `twicc delete-workspace <WORKSPACE_ID>`
Delete a workspace by id. Projects are **not** deleted — only the grouping disappears.
- Plus `--timeout`, `--no-color`, `--json`.
- Skill: [`twicc-delete-workspace`](src/twicc/agent/plugin/twicc/skills/twicc-delete-workspace/SKILL.md).

## Sessions — browse & read

### `twicc sessions` / `twicc sessions get <SESSION_ID...>`
List sessions, or batch-look up specific ones.
- Listing: `--project TEXT`, `--workspace TEXT`, `--limit` (default 20), `--offset`, `--include-archived`, plus the shared filiation/visibility/annotation filters (see below).
- `get`: one or more ids (no filter flags), input order preserved; subagents, archived and hidden sessions all returned, `known: false` for misses.
- Skill: [`twicc-sessions`](src/twicc/agent/plugin/twicc/skills/twicc-sessions/SKILL.md).

### `twicc session <SESSION_ID> <SUBCOMMAND>`
Inspect a single session.
- `content <RANGE>` — raw item content by line number or range (e.g. `5`, `10-20`).
- `messages` — all user/assistant messages, cross-provider, uniform shape. Options: `--range`, `--role user|assistant`, `--limit`, `--offset`, `--tail N` (last N; mutually exclusive with `--limit`/`--offset`).
- `agents` — list subagents. Options: `--limit` (default 20), `--offset`.
- Skill: [`twicc-session`](src/twicc/agent/plugin/twicc/skills/twicc-session/SKILL.md).

### `twicc search "<QUERY>"`
Full-text search across all session history using Tantivy query syntax (e.g. `websocket`, `body:websocket AND from_role:user`).
- `--limit` (default 20), `--offset`, plus the shared filiation/visibility/annotation filters.
- Skill: [`twicc-search`](src/twicc/agent/plugin/twicc/skills/twicc-search/SKILL.md).

## Sessions — create & drive

### `twicc create-session <PROMPT>`
Create a session. `PROMPT` is text or a path to a file whose content is the prompt. With no flags it uses the default provider, the current directory as project, and the settings defaults.
- **Target:** `--project TEXT` (id or path; new directories auto-resolved), `--provider claude_code|codex`.
- **Settings:** `--preset NAME`, `--model`, `--effort`, `--permission-mode`, `--thinking/--no-thinking`, `--claude-in-chrome/--no-claude-in-chrome`, `--fast-mode/--no-fast-mode`, `--question-widget/--no-question-widget`, `--context-max` (`200k`/`1m`/`272k`). Per-flag options override a preset; unset fields fall back to the synced defaults. Run `twicc info agent-settings models presets` for the current valid values.
- **Visibility:** `--hidden` — create the session invisible to the UI (no list/search/broadcast/counter), still counted in cost aggregates. Requires a non-interactive permission mode (`bypassPermissions`/`dontAsk` for Claude Code; `yolo`/`strict` for Codex) and `question_widget=False`.
- **Metadata:** `--title TEXT` (≤ 200 chars), `--annotation KEY=VALUE` (repeatable), `--annotations-file PATH`, `--attach PATH` (repeatable; images/PDF/text up to 5 MB each, 100 files / 32 MB total).
- Plus `--timeout`, `--no-color`, `--json`.
- The spawning session is recorded automatically (`spawned_by`) when the command runs from inside a session.
- Skill: [`twicc-create-session`](src/twicc/agent/plugin/twicc/skills/twicc-create-session/SKILL.md).

### `twicc send-message <SESSION_ID|parent> <PROMPT>`
Send a message into an existing session (resurrects it if dead). Keeps the session's stored settings. `parent` targets the spawner of the calling session.
- `--attach PATH` (repeatable), plus `--timeout`, `--no-color`, `--json`.
- Skill: [`twicc-send-message`](src/twicc/agent/plugin/twicc/skills/twicc-send-message/SKILL.md).

### `twicc update-session <SESSION_ID|self> <SUBCOMMAND>`
Change a session without sending a message. `self` targets the current session. All subcommands accept `--timeout`, `--no-color`, `--json`.
- `settings` — change agent settings (patch by default; `--preset` switches to replace mode). Per-field flags mirror `create-session` (`--model`, `--effort`, `--permission-mode`, `--thinking`, `--claude-in-chrome`, `--fast-mode`, `--question-widget`, `--context-max`); `--unset <field>` resets one to the synced default. Startup settings restart the agent; live ones apply on the next turn.
- `title <NEW_TITLE>` — rename (trimmed, non-empty, ≤ 200 chars).
- `archive` / `unarchive` — archive kills any live agent, tears down its tmux terminal, and (under `autoUnpinOnArchive`) unpins.
- `pin <project|workspace|all>` / `unpin` — sidebar pin scope.
- `hide` / `unhide` — toggle hidden visibility (hide requires a non-interactive permission mode and `question_widget=False`).
- `annotations <OPERATION...>` — ordered ops: `clear`, `replace-file:PATH`, `merge-file:PATH`, `set:KEY=VALUE`, `unset:KEY`.
- Skill: [`twicc-update-session`](src/twicc/agent/plugin/twicc/skills/twicc-update-session/SKILL.md).

## Live processes

### `twicc processes` / `twicc processes <SUBCOMMAND>`
List or act on the live agent processes the backend currently runs. The CLI projects state onto four virtual values: `starting`, `assistant_turn` (generating), `awaiting_user_input` (blocked on a UI click), `user_turn` (idle).
- Listing: `--provider`, `--state <virtual>`, `--limit` (default 20), `--offset`, plus the shared filiation/visibility/annotation filters.
- `get <SESSION_ID...>` — live state per id (`state="dead"` placeholder for stopped; `session_known` flags typos).
- `stop <SESSION_ID...>` — batch-stop (idempotent, tolerant); `--timeout` is a wall-clock budget for the whole batch.
- `wait <ITEM...>` — block until session ids reach matching virtual states. Items mix ids and statuses, auto-discriminated. Required `--timeout FLOAT`; `--all` (default) / `--first`; `--transition` (require a state change first).
- Skill: [`twicc-processes`](src/twicc/agent/plugin/twicc/skills/twicc-processes/SKILL.md).

### `twicc process <SESSION_ID> <SUBCOMMAND>`
Inspect or control one session's live process. Bare `twicc process <id>` prints the current row.
- `stop` — kill the live agent (`reason="manual"`, like the UI's *Stop process*; idempotent). Options `--timeout`, `--no-color`, `--json`.
- `wait <STATUS...>` — block until the process reaches any listed virtual state (`starting`, `assistant_turn`, `awaiting_user_input`, `user_turn`, `dead`). Required `--timeout FLOAT`; `--transition`; `--no-color`, `--json`.
- Skill: [`twicc-process`](src/twicc/agent/plugin/twicc/skills/twicc-process/SKILL.md).

## Spawn tree & filiation

### `twicc topology <SESSION_ID|self>`
Show the spawned-session tree containing a session, rooted at its top-level ancestor: an id-only tree first, then per-node metadata, process state, and aggregate child/cost data. Any id in the tree resolves to the whole tree.
- `--processes/--no-processes` (default on) — include compact live process state.
- `--full-sessions/--no-full-sessions` (default off) — full `session` serialization per node vs. a slim subset.
- `--annotation` — filter (see below).
- Skill: [`twicc-topology`](src/twicc/agent/plugin/twicc/skills/twicc-topology/SKILL.md).

### Shared filiation, visibility & annotation filters

`sessions`, `processes`, `search`, and (for annotations) `topology` accept the same cross-cutting filters:

- `--spawned-by <ID|self|parent>` — direct children of a session.
- `--spawn-tree <ID|self>` — every session in the tree containing that id (any id resolves to its tree).
- `--descendants <ID|self|parent>` — every session transitively spawned by a session, target excluded.
- The three filiation filters are mutually exclusive and each implies `--include-hidden`.
- `--include-hidden` / `--only-hidden` — opt hidden sessions into (or restrict to) the results.
- `--annotation KEY[OP]VALUE` (repeatable, AND-combined) — operators `=`, `!=`, `:exists`, `:not-exists`, `:in:V1,V2`; `KEY` is a dotted path; values are typed (`true`/`false`/`null`/int/float/string).

## Run the provider CLIs directly

`twicc claude [...]` and `twicc codex [...]` run the Claude Code or Codex CLI bundled with TwiCC, using your existing credentials. These are passthrough utilities (no dedicated skill).

---

For multi-session coordination built on top of these commands — spawning a tree of cooperating sessions and aggregating their work — see [`ORCHESTRATION.md`](ORCHESTRATION.md).
