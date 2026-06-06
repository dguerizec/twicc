# TwiCC skills & CLI

> Drive TwiCC from a terminal or from inside an agent — the `twicc` command-line interface and the matching agent-skill plugin are two front doors to the same surface.

TwiCC ships a command-line interface (every `twicc <command>`) **and** a Claude Code / Codex plugin (auto-installed) whose skills wrap those same commands. Anything a skill lets an agent do, you can do from a shell with the exact same `twicc` command — the skills are guided wrappers around the CLI, not a separate capability set. That is why they are documented together here: each entry lists both its command and its **Skill**.

Two audiences, one surface:

- **You, in a terminal** — compose `twicc` into scripts; every structured command prints JSON.
- **An agent, mid-session** — the plugin skills teach the agent the same commands, plus the `self` / `parent` keywords so it can act on its own session and its parent without knowing any id up front.

## Conventions

- **JSON output.** Every structured command prints JSON on stdout — listings, inspections, and write commands (create / update / send / stop / …) alike. There is no text mode and no flag to pass: the CLI speaks JSON by default. The only exceptions are the interactive `password` commands, `token create` (which prints its one-time secret as plain text), and the `claude` / `codex` passthroughs, which stay text.
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

- **`self`** — the current session. Accepted by `update-session`, `update-sessions` / `send-messages` (as an explicit id), `topology`, and the `--spawned-by` / `--spawn-tree` / `--descendants` filters.
- **`parent`** — the session that spawned the current one. Accepted by `send-message` and the filiation filters.

`twicc whoami` is the explicit way for an agent to discover its own `session_id`, working directories, settings, and live process row.

## Driving a remote TwiCC (`--remote`)

Every command can run against a **remote** TwiCC instead of the local one: the CLI forwards it to the remote's `/rpc/` HTTP API and behaves as if it had run there. The full transport contract — endpoints, the `{exit_code, result, error}` envelope, the OpenAPI schema — lives in [`RPC-API.md`](RPC-API.md). The client-side essentials:

```bash
twicc --remote <url> [--remote-token <token>] <command> [args…]
```

- **Target.** `--remote <url>` (or `--remote=<url>`) points at the remote's base URL — a scheme is required (e.g. `http://box:3501`). A **bare** `--remote` falls back to `TWICC_REMOTE_URL`. The global `--remote` / `--remote-token` flags must come **before** the command.
- **Auth.** `--remote-token <token>` (or `TWICC_REMOTE_TOKEN`) is sent as a Bearer token; an explicit value wins over the environment. `/rpc/` is open only when neither a password nor any token is configured — otherwise a valid token is mandatory (mint one with `twicc token`, below).
- **Outcome.** The forwarder prints the remote command's `result` to stdout and `error` to stderr, and **exits with the remote command's exit code** — a script behaves the same locally or remote. A client-side misuse (a local-only command, `self` / `parent`, a malformed `remote:` path) exits `2`; a transport / remote-layer failure (unreachable host, rejected auth, HTTP error, timeout, malformed response) prints `twicc: remote error…` to stderr and exits `7`.
- **Local-only over remote.** `password`, `token`, `claude`, `codex`, `run`, `whoami` are host-bound and rejected client-side. The `self` / `parent` keywords are rejected too — they only mean something on the local host; pass an explicit session id.
- **Files.** `--attach <local file>` is read on the client and inlined as a base64 `data:` URI, so a local (even relative) path works without a shared filesystem. Path arguments (`--project`, `--directory`) are resolved on the server and must be absolute (or, for `--project`, an id).
- **`remote:` scheme.** To point at a file that already lives on the **server** instead of inlining the client's copy, prefix an **absolute** path with `remote:` (e.g. `remote:/srv/data/audit.md`) — supported on the prompt (`create-session` / `send-message`), `--message` (`send-messages`), and `--attach` (all three). Only valid with `--remote`; a relative path, or `remote:` without `--remote`, is an error.

### Authentication tokens: `twicc token <SUBCOMMAND>`

Host-only commands that manage the Bearer tokens gating `/rpc/` — never exposed over the API (and rejected over `--remote`).

- `token create --name <LABEL>` — mint a token; prints the secret **once** as plain text (only its digest is stored).
- `token list` — token metadata as JSON (id / name / timestamps), never the secret.
- `token revoke <TOKEN_ID>` — revoke a token by its id.

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
- Plus the write-command flag `--timeout`.
- Skill: [`twicc-create-project`](src/twicc/agent/plugin/twicc/skills/twicc-create-project/SKILL.md).

### `twicc update-project <PROJECT>`
Update a project's name, color, and/or archived state. The directory is immutable; there is no delete (projects are archived, never removed).
- `--name TEXT` / `--unset-name`, `--color TEXT` / `--unset-color`, `--archive` / `--unarchive` (each pair mutually exclusive).
- Plus `--timeout`.
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
- Plus `--timeout`.
- Skill: [`twicc-create-workspace`](src/twicc/agent/plugin/twicc/skills/twicc-create-workspace/SKILL.md).

### `twicc update-workspace <WORKSPACE_ID>`
Update a workspace. Flags combine into a single atomic edit.
- `--name TEXT`, `--color TEXT` / `--unset-color`, `--add-project` / `--remove-project` (repeatable; id or path), `--add-pattern` / `--remove-pattern` (repeatable), `--archive` / `--unarchive`.
- Plus `--timeout`.
- Skill: [`twicc-update-workspace`](src/twicc/agent/plugin/twicc/skills/twicc-update-workspace/SKILL.md).

### `twicc delete-workspace <WORKSPACE_ID>`
Delete a workspace by id. Projects are **not** deleted — only the grouping disappears.
- Plus `--timeout`.
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
Create a session. `PROMPT` is text or a path to a file whose content is the prompt. With no flags it uses the default provider, the current directory as project, and the settings defaults. Over `--remote`, a file-path prompt is read on the client; prefix an absolute path with `remote:` to read it on the remote server instead.
- **Target:** `--project TEXT` (id or path; new directories auto-resolved), `--provider claude_code|codex`.
- **Settings:** `--preset NAME`, `--model`, `--effort`, `--permission-mode`, `--thinking/--no-thinking`, `--claude-in-chrome/--no-claude-in-chrome`, `--fast-mode/--no-fast-mode`, `--question-widget/--no-question-widget`, `--context-max` (`200k`/`1m`/`272k`). Per-flag options override a preset; unset fields fall back to the synced defaults. Run `twicc info agent-settings models presets` for the current valid values.
- **Settings aliases (provider-agnostic).** Beyond each provider's literal values, the settings flags accept aliases resolved per provider: `--model max`/`strongest` → top family, `min`/`fastest`/`cheapest` → lightest; `--effort` / `--context-max` `min`/`max` → smallest/largest; `--permission-mode` `strict`/`safe` → most-locked (non-interactive), `open`/`full`/`yolo`/`bypass` → most permissive (non-interactive), `auto` → balanced (interactive). A flag the chosen provider doesn't support (e.g. `--thinking` on Codex) is silently ignored (a no-op, not an error), so one command works across a mix of providers. `twicc info agent-settings` carries the live alias tables.
- **Visibility:** `--hidden` — create the session invisible to the UI (no list/search/broadcast/counter), still counted in cost aggregates. Requires a non-interactive permission mode (`bypassPermissions`/`dontAsk` for Claude Code; `yolo`/`strict` for Codex) and `question_widget=False`.
- **Metadata:** `--title TEXT` (≤ 200 chars), `--annotation KEY=VALUE` (repeatable), `--annotations-file PATH`, `--attach PATH` (repeatable; images/PDF/text up to 5 MB each, 100 files / 32 MB total; over `--remote`, prefix an absolute path with `remote:` to read it on the server).
- Plus `--timeout`.
- The spawning session is recorded automatically (`spawned_by`) when the command runs from inside a session.
- Skill: [`twicc-create-session`](src/twicc/agent/plugin/twicc/skills/twicc-create-session/SKILL.md).

### `twicc send-message <SESSION_ID|parent> <PROMPT>`
Send a message into an existing session (resurrects it if dead). Keeps the session's stored settings. `parent` targets the spawner of the calling session. Over `--remote`, a file-path `PROMPT` is read on the client; prefix an absolute path with `remote:` to read it on the server instead.
- `--attach PATH` (repeatable; over `--remote`, prefix an absolute path with `remote:` to read it on the server), plus `--timeout`.
- Skill: [`twicc-send-message`](src/twicc/agent/plugin/twicc/skills/twicc-send-message/SKILL.md).

### `twicc send-messages [SESSION_ID...] --message <TEXT>`
Batch sibling of `send-message`: the same message to several sessions at once, selected with the same model as `update-sessions` (positional `SESSION_ID...` ∪ `--spawned-by <ID|self>` / `--descendants <ID|self>` / `--annotation`; no `parent`). `--attach` is validated per session against its provider (a file one provider rejects becomes a per-id error). Over `--remote`, a file-path `--message` and any `--attach` accept the `remote:` prefix on an absolute path to read it on the server instead of inlining the client's copy. Each send starts/resumes an agent — a batch can cold-start many stopped sessions; and `sent` ≠ done, so chain with `processes wait`. Output is keyed by session id (`{summary, results}`); a per-session failure never fails the batch (exit `0`), exit `6` when nothing was sent.
- Skill: [`twicc-send-messages`](src/twicc/agent/plugin/twicc/skills/twicc-send-messages/SKILL.md).

### `twicc update-session <SESSION_ID|self> <SUBCOMMAND>`
Change a session without sending a message. `self` targets the current session. All subcommands accept `--timeout`.
- `settings` — change agent settings (patch by default; `--preset` switches to replace mode). Per-field flags mirror `create-session` (`--model`, `--effort`, `--permission-mode`, `--thinking`, `--claude-in-chrome`, `--fast-mode`, `--question-widget`, `--context-max`), including the provider-agnostic aliases (`max`/`min`/`open`/`strict`/… — see `create-session`); `--unset <field>` resets one to the synced default. A field the session's provider doesn't support is silently ignored (no-op); when **every** touched field is a no-op the command returns status `noop` and exits `0`. Startup settings restart the agent; live ones apply on the next turn.
- `title <NEW_TITLE>` — rename (trimmed, non-empty, ≤ 200 chars).
- `archive` / `unarchive` — archive kills any live agent, tears down its tmux terminal, and (under `autoUnpinOnArchive`) unpins.
- `pin <project|workspace|all>` / `unpin` — sidebar pin scope.
- `hide` / `unhide` — toggle hidden visibility (hide requires a non-interactive permission mode and `question_widget=False`).
- `annotations <OPERATION...>` — ordered ops: `clear`, `replace-file:PATH`, `merge-file:PATH`, `set:KEY=VALUE`, `unset:KEY`.
- Skill: [`twicc-update-session`](src/twicc/agent/plugin/twicc/skills/twicc-update-session/SKILL.md).

### `twicc update-sessions <SUBCOMMAND> [SESSION_ID...]`
Apply the same update to several sessions at once — the batch sibling of `update-session`. Sub-commands: `archive` / `unarchive`, `pin --mode <project|workspace|all>` / `unpin`, `hide` / `unhide`, `annotations --op <OPERATION>` (each `--op` repeatable), and `settings` (same flags as the singular). No `title` (a shared title across sessions doesn't apply). Each sub-command takes a positional `SESSION_ID...` list merged (union) with the same scoped selection as `processes stop`: `--spawned-by <ID|self>` or `--descendants <ID|self>`, plus `--annotation` to narrow that scope. No `parent`, no `--spawn-tree`. `--timeout` is a wall-clock budget for the whole batch. `settings` resolves per session against its own provider — provider-agnostic aliases (`max`/`min`/`open`/`strict`/…) land on the right value per session, a field a session's provider doesn't support is a silent no-op for that session (per-id status `noop`, counted as success), and a genuinely invalid value on a supported field becomes a per-id error (the other sessions still update). Output is keyed by session id: `{summary: {total, succeeded, failed, all_succeeded}, results: {<id>: <per-id outcome>}}`. A per-session failure never fails the batch (exit `0`); exit `6` when no session was updated.
- Skill: [`twicc-update-sessions`](src/twicc/agent/plugin/twicc/skills/twicc-update-sessions/SKILL.md).

## Live processes

### `twicc processes` / `twicc processes <SUBCOMMAND>`
List or act on the live agent processes the backend currently runs. The CLI projects state onto four virtual values: `starting`, `assistant_turn` (generating), `awaiting_user_input` (blocked on a UI click), `user_turn` (idle).
- Listing: `--provider`, `--state <virtual>`, `--limit` (default 20), `--offset`, plus the shared filiation/visibility filters. `--annotation` requires a filiation scope here; use `--spawned-by self --annotation ...` for direct children, or `--spawn-tree self --annotation ...` only when you explicitly want the whole tree.
- `get <SESSION_ID...>` — live state per id (`state="dead"` placeholder for stopped; `session_known` flags typos).
- `stop [SESSION_ID...]` — batch-stop (idempotent, tolerant). Optional scoped selection: `--spawned-by <ID|self>` or `--descendants <ID|self>`, plus `--annotation` to narrow that scope. No `parent`, no `--spawn-tree`. `--timeout` is a wall-clock budget for the whole batch.
- `wait [SESSION_ID...] <STATUS...>` — block until session ids reach matching virtual states. Items mix ids and statuses, auto-discriminated. Optional scoped selection: `--spawned-by <ID|self>` or `--descendants <ID|self>`, plus `--annotation` to narrow that scope. Required `--timeout FLOAT`; `--all` (default) / `--first`; `--transition` (require a state change first).
- Skill: [`twicc-processes`](src/twicc/agent/plugin/twicc/skills/twicc-processes/SKILL.md).

### `twicc process <SESSION_ID> <SUBCOMMAND>`
Inspect or control one session's live process. Bare `twicc process <id>` prints the current row.
- `stop` — kill the live agent (`reason="manual"`, like the UI's *Stop process*; idempotent). Option `--timeout`.
- `wait <STATUS...>` — block until the process reaches any listed virtual state (`starting`, `assistant_turn`, `awaiting_user_input`, `user_turn`, `dead`). Required `--timeout FLOAT`; `--transition`.
- Skill: [`twicc-process`](src/twicc/agent/plugin/twicc/skills/twicc-process/SKILL.md).

## Spawn tree & filiation

### `twicc topology <SESSION_ID|self>`
Show the spawned-session tree containing a session, rooted at its top-level ancestor: an id-only tree first, then per-node metadata, process state, and aggregate child/cost data. Any id in the tree resolves to the whole tree.
- `--processes/--no-processes` (default on) — include compact live process state.
- `--full-sessions/--no-full-sessions` (default off) — full `session` serialization per node vs. a slim subset.
- `--annotation` — filter (see below).
- Skill: [`twicc-topology`](src/twicc/agent/plugin/twicc/skills/twicc-topology/SKILL.md).

### Shared filiation, visibility & annotation filters

`sessions`, `processes` listing, `search`, and (for annotations) `topology` accept these cross-cutting filters:

- `--spawned-by <ID|self|parent>` — direct children of a session.
- `--spawn-tree <ID|self>` — every session in the tree containing that id (any id resolves to its tree).
- `--descendants <ID|self|parent>` — every session transitively spawned by a session, target excluded.
- The three filiation filters are mutually exclusive and each implies `--include-hidden`.
- `--include-hidden` / `--only-hidden` — opt hidden sessions into (or restrict to) the results.
- `--annotation KEY[OP]VALUE` (repeatable, AND-combined) — operators `=`, `!=`, `:exists`, `:not-exists`, `:in:V1,V2`; `KEY` is a dotted path; values are typed (`true`/`false`/`null`/int/float/string).

Process-control subcommands are narrower on purpose: `processes stop` and `processes wait` accept `--spawned-by <ID|self>` or `--descendants <ID|self>` plus optional `--annotation`, but not `parent` and not `--spawn-tree`.

## Run the provider CLIs directly

`twicc claude [...]` and `twicc codex [...]` run the Claude Code or Codex CLI bundled with TwiCC, using your existing credentials. These are passthrough utilities (no dedicated skill).

---

For multi-session coordination built on top of these commands — spawning a tree of cooperating sessions and aggregating their work — see [`ORCHESTRATION.md`](ORCHESTRATION.md).
