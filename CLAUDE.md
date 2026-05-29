# CLAUDE.md

## Project Overview

**TwiCC** — *Twi* for Twidi, *CC* for Claude and Codex — The Web Interface for Claude and Codex. A standalone, self-contained web application that provides a rich UI for browsing and interacting with Claude Code and Codex sessions. Single process, zero external services, one command to launch.

**Quality approach:** We aim to implement everything to the best standards possible. The only shortcuts we allow: no tests and no linting.

**IMPORTANT — Development workflow:** Never start implementing code without being explicitly invited to do so. When the user explains requirements or shares thoughts, wait for them to finish and confirm before writing any code. Ask clarifying questions if needed, but do not assume that an explanation is an invitation to implement.

**IMPORTANT — Git rebase:** Never rebase on remote branches (e.g., `origin/main`) unless explicitly requested. Always rebase on local branches. If the local branch exists, use it.

## Stack

| Layer            | Technology                                                   |
|------------------|--------------------------------------------------------------|
| Package Manager  | uv (Python), npm (frontend)                                  |
| Backend          | Django 6 (ASGI) + Uvicorn, Python ≥ 3.13                     |
| WebSocket        | Django Channels + InMemoryChannelLayer                       |
| Database         | SQLite (WAL mode)                                            |
| File Watching    | watchfiles                                                   |
| Agent SDKs       | claude-agent-sdk for Claude Code; openai_codex for Codex     |
| Frontend         | Vue.js 3 (SFC, Composition API) + Vite 7                     |
| State Management | Pinia + VueUse                                               |
| UI Components    | Web Awesome 3+ (wa-* elements)                               |
| Code Editor      | CodeMirror 6 (bundled via npm)                               |
| Terminal         | xterm.js with PTY backend                                    |
| Markdown         | markdown-it + shiki + mermaid                                |

## Architecture

```
twicc (entry: run.py → cli.main())
├── Startup
│   ├── Initial sync — scans every provider's data root (~/.claude/projects/, ~/.codex/sessions/, ...), bulk-inserts raw SessionItems (no metadata)
│   └── Background compute (multiprocessing) — computes metadata for all sessions if
│       the owning provider's CURRENT_COMPUTE_VERSION (e.g. CLAUDE_CODE_COMPUTE_VERSION)
│       changed (display_level, kind, groups, costs, git info). Runs once at startup
│       for sessions needing it, then exits.
├── Django ASGI (Uvicorn)
│   ├── HTTP — REST API + SPA catch-all serving Vue frontend
│   └── WebSocket
│       ├── /ws/ — UpdatesConsumer (Channels): data sync, process control, title suggestions
│       └── /ws/terminal/<session_id>/ — Raw ASGI PTY terminal (optional tmux)
├── watchfiles (asyncio task)
│   └── JSONL file changed → incremental read → save to DB (with full metadata) → broadcast via WS
├── Periodic tasks
│   ├── Price sync from OpenRouter API (every 24h)
│   └── Usage quota fetch from provider APIs (every 5min where supported)
└── Agent managers (provider SDKs)
    └── Manage interactive Claude Code and Codex sessions → providers write JSONL → watcher picks up
```

**Startup flow:** Initial sync bulk-inserts raw JSONL lines (fast, no computation). Then background compute (separate process) fills in metadata for sessions whose `compute_version` is outdated. The watcher computes metadata inline for real-time accuracy during normal operation.

**Data flow:** Provider SDK/CLI writes JSONL → watchfiles detects change → incremental read from last offset → save to Django models (with metadata) → WebSocket broadcast → Pinia store updates → Vue UI re-renders.

**Agent flow:** User sends message via WS → provider agent manager creates/resumes the agent process/thread → provider writes JSONL → watcher picks up → broadcast back to frontend.

## Data Directory

All persistent data (database, logs, configuration) lives in a **data directory**:

| Priority | Condition                 | Data directory                        |
|----------|---------------------------|---------------------------------------|
| 1        | Running in a git worktree | Project/worktree root (always forced) |
| 2        | `$TWICC_DATA_DIR` is set  | `$TWICC_DATA_DIR`                     |
| 3        | Default                   | `~/.twicc/`                           |

```
<data_dir>/
├── .env                              # Infrastructure config (ports, password hash, etc.)
├── settings.json                     # User preferences synced across devices
├── workspaces.json                   # User-defined workspaces
├── terminal-config.json              # Terminal preferences
├── message-snippets.json             # Saved message snippets
├── seen-tips.json                    # Tip dismissal state
├── {provider}-settings-presets.json  # Agent settings presets per provider (e.g. claude_code-, codex-)
├── db/
│   └── data.sqlite (+shm, +wal)      # SQLite database
├── search-index/                     # Tantivy full-text search index
├── sessions-pending/                 # Pending session payloads picked up by the watcher
└── logs/
    ├── backend.log                   # Backend application logs
    ├── frontend.log                  # Frontend (Vite) process output
    └── sdk/                          # Raw SDK message logs, one subdirectory per provider
        ├── claude_code/{session_id}.jsonl
        └── codex/{session_id}.jsonl
```

Path resolution is centralized in `src/twicc/paths.py`. The `devctl.py` script has its own equivalent logic (since it doesn't depend on Django).

## Development Process Controller (devctl.py)

Script to manage frontend and backend dev servers as background processes. Use this when the user asks to start, stop, or restart the dev servers.

```bash
uv run ./devctl.py start [front|back|all]   # Start process(es)
uv run ./devctl.py stop [front|back|all]    # Stop process(es)
uv run ./devctl.py restart [front|back|all] # Restart process(es)
uv run ./devctl.py status                   # Check running status and port config
uv run ./devctl.py logs [front|back]        # Show recent logs (--lines=N)
```

**Default ports:** Frontend on 5173, Backend on 3500. The script verifies correct port binding after start.

**Log files:** `<data_dir>/logs/backend.log` and `<data_dir>/logs/frontend.log` — read these to debug issues.

**devctl-specific files** (always local to the project/worktree root):
- `.devctl/pids/` — PID files for running processes

When starting the backend, devctl passes `TWICC_DATA_DIR` to the backend process so it uses the correct data directory.

### Worktree Support

**IMPORTANT:** When working in a worktree, every Bash command must be prefixed with `cd <worktree-path> && ` — never rely on the Bash tool's persistent cwd to still be the worktree from a previous command. A wrong cwd on a destructive command (`devctl.py restart`, `devctl.py stop`, manual `migrate`, …) will hit the main project's servers / data dir instead of the worktree's, killing real work.

devctl automatically detects git worktrees (by comparing `git rev-parse --git-dir` vs `--git-common-dir`). When running in a worktree, it sets `TWICC_DATA_DIR` to the worktree root, so database, logs, and `.env` are all local to that worktree.

Each worktree has its own:
- instances of the backend and frontend servers
- `.env` file with port configuration (in the worktree root)
- `.devctl/` directory (PIDs)
- `db/data.sqlite*` database (in `<worktree>/db/`)
- `logs/` directory (backend.log, frontend.log, sdk/) in `<worktree>/logs/`

When starting dev servers in a worktree, just run `uv run ./devctl.py start`. devctl automatically:
- copies the database from `~/.twicc/db/` (if no local DB exists yet)
- finds available ports (incrementing from default+1: 3501 for backend, 5174 for frontend)
- saves the port configuration to the worktree's `.env` file

If the user explicitly asks to start with an empty/fresh database, use `uv run ./devctl.py start --empty-db`.

Always check your current working directory before starting the servers so you'll know if you are in a worktree or not.
When the user asks to start the servers in a worktree, give them the localhost urls for the frontend and backend servers based on the ports shown in devctl's output (e.g., `Frontend: http://localhost:5274`, `Backend: http://localhost:3501`).
When the user asks you to exit/kill/delete (etc...) a worktree, you MUST run the "stop all" command to kill the processes, even if you didn't start them yourself.

#### Running Python / Django code in a worktree without devctl

`paths.py` itself does **not** detect worktrees — only `devctl.py` does, by injecting `TWICC_DATA_DIR=<worktree>` into the backend processes it spawns. Any other Python invocation (one-liners, `manage.py`, Django shell, ad-hoc migrations, scripts) will silently fall back to `~/.twicc/` and hit the **production** data directory, even if you `cd` into the worktree first.

When working inside a worktree, **always** do both of:
1. `cd` into the worktree root (so the editable install resolves to that worktree's source code, including its migrations).
2. Set `TWICC_DATA_DIR=<worktree>` in the environment of the command (so `paths.py` returns the worktree's `db/`, `logs/`, etc.).

```bash
cd <worktree>
TWICC_DATA_DIR=$PWD uv run python -c "..."
TWICC_DATA_DIR=$PWD uv run python -m django <command> --settings=twicc.django.settings
```

Before running any read or write that depends on the data directory (especially migrations or DB writes), print the resolved DB path first as a sanity check:

```python
from django.conf import settings
print(settings.DATABASES['default']['NAME'])  # must point inside the worktree
```

Forgetting this is destructive: a manual `migrate` from the wrong cwd applies the worktree's branch-only migrations to the prod DB, leaving it in a state the `main` code can no longer write to.

## Operations Reserved to User

Claude never runs these operations on its own initiative. If the user explicitly asks you to run one of these operations, do it without asking for confirmation. Otherwise, notify the user at the end of a task or, if absolutely necessary during your work, pause the task and ask them the permission to do it or to do them manually:

- **Django migrations:** After modifying models (and having created the migration yourself), remind the user to run `migrate`
- **Dev server restart:** After backend changes, remind the user to restart via `devctl.py`
- **Package installation:** After adding dependencies, remind the user to run `npm install` or `uv add`

## Database Models

Key models in `src/twicc/core/models.py`:

| Model | Purpose |
|-------|---------|
| `Project` | Cross-provider working directory. ID derived from the directory path via `path_to_project_id`. Has `name`, `color`, `directory`, `git_root`, `total_cost`, `sessions_count`. |
| `Session` | One JSONL file per session. Tracks `last_offset`/`last_line` for incremental sync. Carries `provider`, `file_path`, `title`, costs (`self_cost`, `subagents_cost`, `total_cost`), `type` (session/subagent), `parent_session` (self FK), `model`, `archived`, `pinned`, plus the closed `AgentSettings` bundle (`selected_model`, `effort`, `thinking_enabled`, `permission_mode`, `context_max`, `claude_in_chrome`, `fast_mode`) and lifecycle timestamps (`last_started_at`, `last_updated_at`, `last_stopped_at`, etc.). |
| `SessionItem` | One row per JSONL line. Has `display_level` (ALWAYS/COLLAPSIBLE/DEBUG_ONLY), `kind` (user_message, assistant_message, etc.), `group_head`/`group_tail` for collapsible groups, `cost`, `timestamp`. |
| `ToolResultLink` | Links tool_use to tool_result items within a session (provider-agnostic). |
| `AgentLink` | Links a spawn-an-agent tool_use to its subagent session (provider-agnostic). |
| `ModelPrice` | Historical model pricing from OpenRouter API, scoped per provider. |
| `UsageSnapshot` | Per-provider usage quota snapshots (e.g. Anthropic OAuth 5h and 7-day quotas for Claude Code). |
| `WeeklyActivity` / `DailyActivity` | Pre-computed activity stats keyed by `(project, date, provider)`; `project=NULL` means global. |
| `ProcessRun` | Tracks a running agent process for cron lifecycle management and crash recovery. |
| `SessionCron` | Persisted cron jobs created by a Claude Code session via the CLI cron tool. |
| `Command` | Synced slash-command definitions (per provider). |

**Sync strategy:** On file change, compare `mtime` → `seek()` to `last_offset` → read new lines → insert to DB → update offset. Files are append-only.

## Code Quality

- **Language:** All code content (UI strings, comments, variable names) must be in English. Only documentation files (*.md) may contain French. **Important:** Even when the user speaks French, always write UI text and code in English.
- Python: ruff (line-length=120)
- Tests: pytest with pytest-django
- Vue components use Composition API with `<script setup>`

## Python Patterns

- **Immutable data containers:** Always use `NamedTuple` for simple immutable data structures (return values, decisions, configs). Works with all field types including lists. Prefer over `@dataclass` when mutability is not needed.
- **JSON parsing:** Use `orjson` instead of the standard `json` module for all JSON operations in the backend. It's ~6x faster and handles the high-volume JSONL file parsing efficiently.
- **Aliased imports (`from X import Y as Z`):** Use sparingly, only when strictly necessary. Two acceptable reasons: (1) **name collision** — the imported symbol would shadow another name in scope, or two imports would shadow each other (e.g. multiple `main` modules → `as foo_main`, `as bar_main`); (2) **disambiguating intent at the call site** for a noticeably less generic verb (e.g. `from .sdk_logger import patch_client as patch_client_for_logging`). Avoid the cosmetic prefix/suffix style (`as _foo`, `as django_settings`, `as ProcessRunModel`) when the bare name doesn't actually conflict — it adds noise and makes it harder to grep for the canonical symbol.

## Frontend Patterns

### Avoiding Circular Imports (HMR)

**CRITICAL:** Circular imports between frontend modules cause Vite HMR to fall back to full page reloads instead of hot updates. This has been a recurring issue.

**Rules to follow:**
- **Never** import `router.js` directly from utility files, composables, or stores. Use lazy `await import('../router')` if router access is needed (e.g., for redirects).
- **Never** create mutual static imports between stores (e.g., `settings.js ↔ data.js`) or between stores and composables (e.g., `data.js ↔ useWebSocket.js`). Use lazy `await import()` in the less-frequently-called direction.
- **Never** import Vue components statically from composables if those components import stores/composables that create a cycle. Use `defineAsyncComponent(() => import(...))` instead.
- **Common cycle patterns to avoid:**
  - `main.js → ... → someFile → main.js` (extract shared code to a utility file)
  - `router.js → views → components → composable/util → router.js` (lazy import router)
  - `store ↔ store` (lazy import in one direction)
  - `store ↔ composable` (lazy import in one direction)
  - `composable → component → store → composable` (use defineAsyncComponent)

### Draft System

Draft sessions, messages, and media attachments are persisted to IndexedDB (via `frontend/src/utils/draftStorage.js`). Hydrated on startup before Vue app mount.

### Virtual Scrolling

Large session item lists use a custom virtual scroller (`useVirtualScroll.js`, `VirtualScroller.vue`). Items go through a visual pipeline: raw items → `computeVisualItems()` (applies display mode, group expansion) → rendered in the scroller.

Visual items are stabilized across recomputes: when `recomputeVisualItems` runs, each new visual item is compared with the cached version (by `lineNum`). If all properties are identical, the old object reference is reused. This means Vue skips re-rendering for unchanged items, even though `computeVisualItems` creates new objects every time.

### Session Item Content Access

**IMPORTANT:** In the frontend, never access `item.content` (the raw JSON string) directly for parsing. Always use the helpers from `frontend/src/utils/parsedContent.js`:

- **`getParsedContent(item)`** — Returns the parsed content object. Parses lazily on first access and caches with `markRaw()`. Works on both session items and visual items.
- **`setParsedContent(item, parsed)`** — Sets parsed content explicitly on an item. Use for synthetic items (no raw content string) or to forward a cached result to a new object.
- **`clearParsedContent(item)`** — Invalidates the cached parsed content (e.g., when `item.content` changes).
- **`hasContent(item)`** — Returns `true` if the item has content available (raw string or set via `setParsedContent()`). Use this instead of `!!item.content` for placeholder detection, since synthetic items have parsed content but no `content` string.

Direct `JSON.parse(item.content)` is forbidden — it bypasses the cache and wastes CPU on repeated parsing. Direct access to the internal `_parsedContent` field is also forbidden — always use the functions above.

### Agent Settings — Closed Bundle

The seven per-session agent setting fields (`selected_model`, `effort`, `thinking_enabled`, `permission_mode`, `context_max`, `claude_in_chrome`, `fast_mode`) are a **closed bundle** shared across every provider. The bundle, the `Session` row, the WS payload, and the localStorage synced settings all carry the same shape regardless of which provider owns the session. Each provider declares which fields it actually uses via `getAgentSettingsCategories()` in `frontend/src/providers/baseHelpers.js` (and its overrides); fields not listed are silently ignored by that provider. New provider-specific session-level flags follow the same pattern: add the column to `Session`, classify it in the owning provider's categories — never split off into a per-provider side table. See the matching comment on `Session.claude_in_chrome` in `src/twicc/core/models.py` for the backend rationale.

## Web Awesome Components

**Version:** Web Awesome 3.3+. Since version 3, **native** browser events are no longer prefixed with `wa-` (e.g., `@click`, `@focus`, `@input`). However, **custom** Web Awesome events still use the `wa-` prefix (e.g., `@wa-show`, `@wa-hide`, `@wa-after-show`).

**IMPORTANT:** Each Web Awesome component used must be explicitly imported in `frontend/src/main.js`. Imports load both the component JS and its styles (via shadow DOM).

```javascript
// Example in main.js
import '@awesome.me/webawesome/dist/components/button/button.js'
import '@awesome.me/webawesome/dist/components/callout/callout.js'
```

If a `wa-*` component appears unstyled in production (but works in dev), it's likely missing its import in `main.js`.

**Slots for icons:** Web Awesome 3 uses `start` and `end` slots for icons inside buttons (not `prefix`/`suffix` which don't exist). For `wa-dropdown-item`, use the `icon` slot. Examples:

```html
<wa-button><wa-icon slot="start" name="check"></wa-icon> Save</wa-button>
<wa-button>Next <wa-icon slot="end" name="chevron-right"></wa-icon></wa-button>
<wa-dropdown-item><wa-icon slot="icon" name="plus"></wa-icon> New</wa-dropdown-item>
```

## Web Awesome Documentation

A nearly complete "one file" version of the docs is available at `frontend/node_modules/@awesome.me/webawesome/dist/llms.txt`
Full documentation is also at `./frontend/node_modules/@awesome.me/webawesome/dist/skills/webawesome/` (`references/components/`, `references/usage.md` and `references/frameworks/vue.md`)

## Release Process

When the user asks to make a new release, follow these steps in order:

1. **Check branch:** Verify you're on `main`. If not, stop and inform the user.

2. **Update version numbers:**
   - `pyproject.toml` → `[project]` → `version`
   - `uv.lock` → `[[package]]` → `version` (for the `twicc` package entry)

3. **Update CHANGELOG.md:** Set the version number on the `[Unreleased]` section (if not already done) and add the release date (`YYYY-MM-DD`).

4. **Build:** Run `./scripts/build-release.sh` (~1-2 min). This produces:
   - `dist/twicc-{version}.tar.gz` (sdist, platform-agnostic — both this and the wheel get published to PyPI in step 11)
   - `dist/twicc-{version}-py3-none-any.whl` (single platform-agnostic wheel)

   The Codex CLI binary comes from `openai-codex-cli-bin` on PyPI (manylinux/macOS/Windows wheels since 0.133.0), so TwiCC itself does not need per-platform wheels anymore. The sdist embeds the pre-built frontend assets so `pip install` from source does not need npm. See `hatch_build.py` and `docs/codex-vendoring.md`.

5. **User testing (mandatory):** Ask the user to test the build before continuing:
   ```
   uvx --from dist/twicc-{version}-py3-none-any.whl twicc
   ```
   Remind them to stop any running TwiCC instance first, then visit `http://localhost:3500` to test. **Do not run `uvx` yourself** — this requires user interaction.

6. **Wait for user confirmation.** Only proceed if they say it's OK.

7. **Commit:** Create a commit with message `release: v{version}`.

8. **Create annotated tag** with changelog content extracted from `CHANGELOG.md`:
   ```bash
   git tag -a v{version} -m "Release v{version}

   {changelog content for this version}"
   ```
   **Image URLs:** If the changelog contains relative image paths (e.g., `frontend/public/whats-new/...`), replace them with absolute URLs in the tag message by prefixing with `https://raw.githubusercontent.com/twidi/twicc/main/`. Do **not** modify `CHANGELOG.md` itself.

9. **Push** commit and tag:
   ```bash
   git push && git push --tags
   ```

10. **Create GitHub Release** using the same changelog content (with the same absolute image URLs as the tag):
    ```bash
    gh release create v{version} --title "v{version}" --notes "{changelog content}"
    ```

11. **Publish to PyPI (user action):** Give the user the command to publish both the wheel and the sdist:
    ```
    uvx uv-publish /home/twidi/dev/twicc-poc/dist/twicc-{version}*
    ```
    The glob picks up both `twicc-{version}-py3-none-any.whl` and `twicc-{version}.tar.gz`. The sdist is now safe to publish — it embeds the pre-built frontend assets, and the Codex CLI binary comes from the `openai-codex-cli-bin` PyPI dependency, so `pip install` from source needs no npm and no extra fetch. **Do not run `uv-publish` yourself** unless the user explicitly asks you to.

## Dialog Forms Pattern

When creating a form inside a `wa-dialog`, refer to `frontend/src/components/project/ProjectEditDialog.vue` as the reference implementation. Key patterns:

- **Form element:** Wrap content in a `<form>` with `@submit.prevent="handleSave"` and a unique `id`
- **Submit button outside form:** Use `type="submit"` and set the `form` attribute via `setAttribute()` in a sync function (wa-button doesn't expose `form` as a property)
- **Focus management:** Use `@wa-after-show` event (not `autofocus` attribute) to focus the first input after the dialog animation completes, and use `setSelectionRange(len, len)` to position cursor at end
- **Input validation:** Apply `trim()` on text inputs before validation and submission
- **Uniqueness checks:** Validate client-side first (from store data), backend enforces with unique constraint
- **Error display:** Use `wa-callout variant="danger"` for validation and API errors
- **Dialog width:** Use `--width: min(Xpx, calc(100vw - 2rem))` to be responsive
