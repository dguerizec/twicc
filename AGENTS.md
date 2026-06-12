# AGENTS.md

**TwiCC** (The Web Interface for Claude and Codex) — self-contained web UI for browsing and interacting with Claude Code and Codex sessions. Single process, zero external services, one command to launch.

## Working Rules

- **Local instructions:** at the start of work, if `AGENTS.local.md` exists at the repository root, read it after this file. It contains local, uncommitted instructions specific to the developer. Its instructions extend and may override this `AGENTS.md`; when the two files conflict, `AGENTS.local.md` takes precedence. Direct system, developer, and current user instructions still take precedence over both files.
- **Quality:** best standards everywhere. Only allowed shortcuts: no mandatory tests or linting.
- **Never implement without explicit invitation.** When the user explains requirements or shares thoughts, wait for confirmation before writing code. Ask clarifying questions, but an explanation is not an invitation.
- **Preserve existing user changes.** The current checkout may already contain uncommitted changes. Treat any change you did not make as user-owned: do not revert, overwrite, move, or clean it up unless the user explicitly asks.
- **Git rebase:** never on remote branches (`origin/main`, …) unless explicitly asked. Always rebase on the local branch; if it exists, use it.
- **Language:** all written artifacts — code, UI strings, comments, names, docs (incl. `docs/plans/`) — in English. French is reserved for live chat (and the dev's `AGENTS.local.md`). Even when the user speaks French, write UI/code/docs in English.

## Commit Conventions

- When creating commits, include a descriptive commit body that explains the change, not only a subject line.
- Add a `Co-Authored-By: Codex <codex@openai.com>` trailer for the agent that performed the work, matching the style used in recent commits.

## Operations Reserved to User

Never run these on your own initiative. If the user explicitly asks, do it without confirmation. Otherwise notify at task end (or pause and ask if truly necessary mid-task):

- **Django migrations:** after you modify models and create the migration, remind the user to `migrate` their own running instance. (Starting/restarting via `devctl.py` auto-applies migrations at backend startup — never `migrate` by hand to bring servers up.)
- **Dev server restart:** after backend changes, remind the user to restart via `devctl.py` (no need to do it on every message)
- **Package installation:** after adding deps, remind the user to run `npm install` or `uv add`. (`devctl.py start` already runs `npm ci` via the editable rebuild — never pre-run it yourself.)

## Stack

uv + npm · Django 6 ASGI (Uvicorn, Python ≥ 3.13) · Channels + InMemoryChannelLayer · SQLite (WAL) · watchfiles · claude-agent-sdk + openai_codex · Vue 3 (Composition API, `<script setup>`) + Vite 7 · Pinia + VueUse · Web Awesome 3+ (`wa-*`) · CodeMirror 6 · xterm.js (PTY) · markdown-it + shiki + mermaid.

Python: ruff (line-length=120). Tests: pytest + pytest-django.

## Architecture

Entry: `run.py → cli.main()`.

- **Startup:** *Initial sync* scans each provider data root (`~/.claude/projects/`, `~/.codex/sessions/`, …) and bulk-inserts raw `SessionItem`s (fast, no metadata). Then a separate *background compute* process fills metadata (display_level, kind, groups, costs, git) for sessions whose `compute_version` is below the provider's `CURRENT_COMPUTE_VERSION`, then exits.
- **Django ASGI:** HTTP (REST + SPA catch-all) and WebSocket — `/ws/` `UpdatesConsumer` (data sync, process control, title suggestions) and `/ws/terminal/<session_id>/` (raw ASGI PTY, optional tmux).
- **watchfiles task:** JSONL change → incremental read from `last_offset` → save to DB (full metadata, inline for real-time accuracy) → WS broadcast → Pinia → Vue.
- **Periodic:** price sync from OpenRouter (24h); usage quota fetch from provider APIs (5min where supported).
- **Agent managers:** provider SDKs drive interactive sessions → providers write JSONL → watcher picks up.

**Sync strategy:** JSONL files are append-only — on change, compare `mtime`, `seek(last_offset)`, read new lines, insert, update offset.

## Data Directory

All persistent data (db, logs, config) lives in one data dir, resolved (centralized in `src/twicc/paths.py`; `devctl.py` has equivalent standalone logic):

1. git worktree → worktree root (forced); 2. `$TWICC_DATA_DIR` if set; 3. default `~/.twicc/`.

Contents: `.env` (infra config: ports, password hash), synced user config (`settings.json`, `workspaces.json`, `terminal-config.json`, `message-snippets.json`, `seen-tips.json`, `{provider}-settings-presets.json`), `db/data.sqlite(+shm/+wal)`, `search-index/` (Tantivy), `drop-requests/` (CLI drop-files picked up by a watcher), `logs/` (`backend.log`, `frontend.log`, and in dev mode, `sdk/{provider}/{session_id}.jsonl`).

## devctl.py — Dev Servers

Use when the user asks to start/stop/restart dev servers.

```bash
uv run ./devctl.py start|stop|restart [front|back|all]
uv run ./devctl.py status
uv run ./devctl.py logs [front|back] [--lines=N]
```

Default ports: frontend 5173, backend 3500 (verified after start). `start --empty-db` for a fresh DB in worktrees on user request. Debug via `<data_dir>/logs/{backend,frontend}.log`. PIDs in `.devctl/pids/` (always local to project/worktree root).

**To start/restart, run the single `start` command and read the logs — devctl does everything:** it rebuilds the editable install (runs `npm ci`), auto-applies pending migrations at startup, on first setup copies db + search index + user config from `~/.twicc/` (never `.env`, `logs/`, `drop-requests/`), finds free ports (default+1: 3501/5174), writes them to `.env`.

**Never run `npm install`/`npm ci`, `migrate`, or touch `node_modules` yourself when starting servers** — wasted and harmful: a parallel `npm install` corrupts devctl's `npm ci` (`ENOTEMPTY`). The post-start port check can time out during initial sync — not a failure; confirm via `backend.log`.

When starting in a worktree, give the user the localhost URLs from devctl's output. When asked to exit/kill/delete a worktree, you MUST run `stop all` even if you didn't start the processes.

### Worktrees

devctl auto-detects worktrees and sets `TWICC_DATA_DIR=<worktree root>`, so each worktree has its own backend/frontend, `.env` (ports), `.devctl/`, `db/`, `logs/`, and json data files. Always check your cwd before starting so you know whether you're in a worktree.

**Prefix every Bash command with `cd <worktree> && `** — never trust the persistent cwd. A wrong cwd on a destructive command (`devctl restart/stop`, manual `migrate`) hits the main project's servers/data dir and kills real work.

**Running Python/Django without devctl:** `paths.py` does NOT detect worktrees (only devctl injects `TWICC_DATA_DIR`). Any other invocation (one-liners, `manage.py`, shell, ad-hoc migrations) silently falls back to `~/.twicc/` — the **prod** data dir — even after `cd`. So always do both: (1) `cd` into the worktree (editable install resolves to its source + migrations); (2) set `TWICC_DATA_DIR=$PWD` in the command's env.

```bash
cd <worktree>
TWICC_DATA_DIR=$PWD uv run python -m django <command> --settings=twicc.settings
```

Before any data-dir-dependent read/write (esp. migrations), sanity-check the resolved path:
```python
from django.conf import settings; print(settings.DATABASES['default']['NAME'])  # must be inside the worktree
```
Forgetting this is destructive: a `migrate` from the wrong cwd applies branch-only migrations to the prod DB, leaving it unwritable by `main`.

## Database Models

Key models in `src/twicc/core/models.py` (read the source for full field lists; non-obvious points only here):

- **`Project`** — cross-provider working dir, ID from path via `path_to_project_id`. `worktree_of` is an auto-detected self-FK to the main repo (never set manually; a worktree's sessions/cost/activity aggregate into it). Per-project agent defaults `default_provider` + `default_agent_settings` seed NEW sessions, inherited up the chain (worktree main repo, then path ancestors), resolved **at creation only** by `projectAgentDefaults.js` (UI) and its mirror `project_agent_defaults.py` (CLI) — never re-resolved for a running session.
- **`Session`** — one JSONL file; `last_offset`/`last_line` drive incremental sync. Carries costs, `type` (session/subagent), `parent_session` (self-FK), lifecycle timestamps, and the closed `AgentSettings` bundle (see below).
- **`SessionItem`** — one JSONL line; `display_level` (ALWAYS/COLLAPSIBLE/DEBUG_ONLY), `kind`, `group_head`/`group_tail` for collapsible groups.
- **`ToolResultLink`** / **`AgentLink`** — link tool_use ↔ tool_result / spawn-agent tool_use ↔ subagent session (both provider-agnostic).
- **`ModelPrice`** (OpenRouter pricing) · **`UsageSnapshot`** (per-provider quotas) · **`WeeklyActivity`/`DailyActivity`** (stats by `(project, date, provider)`, `project=NULL` = global) · **`ProcessRun`** (live process, cron lifecycle + crash recovery) · **`SessionCron`** (CLI-created crons) · **`Command`** (synced slash-commands) — all per provider where relevant.

### Agent Settings — Closed Bundle

The seven per-session fields (`selected_model`, `effort`, `thinking_enabled`, `permission_mode`, `context_max`, `claude_in_chrome`, `fast_mode`) are a **closed bundle** with one shape across all providers (on `Session`, the WS payload, synced localStorage). Each provider declares which fields it uses via `getAgentSettingsCategories()` (`frontend/src/providers/baseHelpers.js` + overrides); unlisted ones are ignored. New provider-specific flags follow the same pattern — add a `Session` column, classify it in the provider's categories, never a side table (rationale in the `Session.claude_in_chrome` comment).

**`permission_mode_if_untrusted` is NOT in the bundle** — a per-provider *default-shaping* field in global settings / presets / `default_agent_settings`, never on `Session`. At creation, project trust picks which one materializes into the stored `permission_mode` (trusted → `permission_mode`, else → `permission_mode_if_untrusted`, restricted to `UNTRUSTED_PERMISSION_MODES`, no `bypassPermissions`/`yolo`). A backend floor re-clamps at agent build regardless (`core/services/trust.py`), mirrored by the CLI (`cli/_drop_request/aliases.py`). **Trust is human-only** — never an agent-facing flag/skill. See `docs/plans/2026-06-09-project-trust-design.md` §13.

## Python Patterns

- **`NamedTuple`** for simple immutable data (return values, decisions, configs) — works with all field types incl. lists; prefer over `@dataclass` when mutability isn't needed.
- **`orjson`**, not stdlib `json`, for all backend JSON (~6× faster, handles high-volume JSONL).
- **Aliased imports (`as`)** only when strictly necessary: (1) name collision (e.g. multiple `main` → `as foo_main`); (2) disambiguating intent for a less generic verb (e.g. `patch_client as patch_client_for_logging`). Avoid cosmetic `as _foo`/`as django_settings` when there's no real conflict — it's noise and harms grep.

## Frontend Patterns

### Circular imports (HMR) — CRITICAL

Cycles make Vite HMR fall back to full reloads (recurring issue).
- Never import `router.js` from utils/composables/stores → use lazy `await import('../router')`.
- Never mutual static imports store↔store or store↔composable → lazy `await import()` in the less-frequent direction.
- Never statically import components from composables when those components close a cycle → `defineAsyncComponent(() => import(...))`.
- Common shapes: `main.js → … → main.js` (extract shared code), `router → views → components → util → router` (lazy router), `store ↔ store`, `store ↔ composable`, `composable → component → store → composable`.

### Drafts

Draft sessions/messages/media persisted to IndexedDB (`frontend/src/utils/draftStorage.js`), hydrated on startup before app mount.

### Virtual scrolling

Large item lists use a custom scroller (`useVirtualScroll.js`, `VirtualScroller.vue`): raw items → `computeVisualItems()` (display mode, group expansion) → rendered. Visual items are stabilized across recomputes — each new item is compared by `lineNum` to the cached one; identical → old reference reused, so Vue skips re-render even though `computeVisualItems` makes new objects.

### Session item content access — IMPORTANT

Never access `item.content` (raw JSON string) directly. Use `frontend/src/utils/parsedContent.js`:
- `getParsedContent(item)` — parsed object, lazy + `markRaw()` cached; works on session and visual items.
- `setParsedContent(item, parsed)` — set explicitly (synthetic items, or forwarding a cached result).
- `clearParsedContent(item)` — invalidate (e.g. when `item.content` changes).
- `hasContent(item)` — true if content available (raw or set); use instead of `!!item.content` for placeholders (synthetic items have parsed content but no string).

`JSON.parse(item.content)` and touching `_parsedContent` directly are forbidden.

### Dialog forms

When creating a form inside a `wa-dialog`, use `frontend/src/components/project/ProjectEditDialog.vue` as the reference implementation. Key patterns:

- **Form element:** wrap content in a `<form>` with `@submit.prevent="handleSave"` and a unique `id`.
- **Submit button outside form:** use `type="submit"` and set the `form` attribute via `setAttribute()` in a sync function (wa-button doesn't expose `form` as a property).
- **Focus management:** use the `@wa-after-show` event (not the `autofocus` attribute) to focus the first input after the dialog animation completes, and `setSelectionRange(len, len)` to put the cursor at the end.
- **Input validation:** apply `trim()` on text inputs before validation and submission.
- **Uniqueness checks:** validate client-side first (from store data); the backend enforces with a unique constraint.
- **Error display:** use `wa-callout variant="danger"` for validation and API errors.
- **Dialog width:** use `--width: min(Xpx, calc(100vw - 2rem))` to stay responsive.
- **Event propagation:** dialog `@wa-show`/`@wa-hide`/`@wa-after-*` handlers must guard against bubbling from nested `wa-*` children — see *Bubbling custom events* below. Failing to guard makes a nested `wa-select` opening/closing steal focus or close the whole dialog.

## Web Awesome (3.3+)

- Native events are **un**prefixed since v3 (`@click`, `@input`); custom WA events keep `wa-` (`@wa-show`, `@wa-after-show`).
- **Every component must be explicitly imported in `frontend/src/main.js`** (loads JS + shadow-DOM styles). Unstyled in prod but fine in dev → missing import.
- Icon slots: `start`/`end` inside buttons (not `prefix`/`suffix`), `icon` for `wa-dropdown-item`:
  ```html
  <wa-button><wa-icon slot="start" name="check"></wa-icon> Save</wa-button>
  <wa-dropdown-item><wa-icon slot="icon" name="plus"></wa-icon> New</wa-dropdown-item>
  ```
- Docs: one-file `frontend/node_modules/@awesome.me/webawesome/dist/llms.txt`; full set in same `dist` dir under `skills/webawesome/`.

### Bubbling custom events — recurring trap

WA custom events **bubble through the composed DOM**: a nested `wa-*` child fires the *same* event name the outer component listens for. Classic case — a `wa-select`/`wa-dropdown` inside a `wa-dialog` emits `wa-show`/`wa-hide`/`wa-after-show`/`wa-after-hide` on panel open/close; unguarded, the dialog handler re-runs focus logic (stealing focus from the dropdown) or treats it as the dialog closing (dismissing everything). Same family with `wa-switch`, nested `wa-details`, nested `wa-tab-group` (`wa-tab-show`/`wa-tab-hide`), per-row `wa-dropdown` `wa-select` reaching a parent selector, nested `wa-split-panel` `wa-reposition`.

**Always scope the handler to its own element** — equivalent idioms: a top guard `if (event.target !== ownRef/event.currentTarget) return`; the Vue `.self` modifier; or `.stop` when a nested control's event must never reach a same-named outer handler. When a parent must *veto* its own close while a child panel is open, combine the target guard with `event.preventDefault()` for the parent's own event only (see `ProjectView.vue` `onSelectorHide`).

## TwiCC Plugin (Agent Skills)

Skills live under `src/twicc/agent/plugin/twicc/skills/`, packaged as a versioned plugin (`.../twicc/.claude-plugin/plugin.json`).

**Any bundle change — add/edit/rename/remove a `SKILL.md` — REQUIRES bumping `version` in `plugin.json`**, or providers serve a stale cached copy. Bump: user-visible change → patch; new skill or new flags/options → minor; rename/removal → minor at least.

**Before creating/updating a skill, read `src/twicc/agent/plugin/README.md`** (structure, wording, anti-patterns) and a few existing skills to calibrate tone.

## Release Process

When the user asks to make a release, follow `docs/release-process.md`.
