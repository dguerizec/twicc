# AGENTS.md

## Project

TwiCC is a single-process web application for browsing and interacting with Claude Code and Codex sessions.

Main stack:
- Backend: Django 6 ASGI, Uvicorn, Channels, SQLite, Python 3.13+
- Frontend: Vue 3 SFCs with Composition API, Vite, Pinia, VueUse
- UI: Web Awesome `wa-*` components
- Package managers: `uv` for Python, `npm` for frontend

## Working Agreement

- At the start of work, if `AGENTS.local.md` exists at the repository root, read it after this file. It contains local, uncommitted instructions specific to the developer. Its instructions extend and may override this `AGENTS.md`; when the two files conflict, `AGENTS.local.md` takes precedence. Direct system, developer, and current user instructions still take precedence over both files.
- Do not implement code unless the user explicitly asks you to do so. If the user is explaining requirements or thinking aloud, wait, ask clarifying questions if needed, and do not treat it as permission to edit.
- Preserve existing work. The worktree may already be dirty; never revert or overwrite changes you did not make unless the user explicitly asks.
- Never rebase on remote branches such as `origin/main` unless explicitly requested. If rebasing is requested and a local branch exists, rebase on the local branch.
- Code, comments, variable names, and UI strings must be in English. Documentation files may contain French.
- The project prefers high-quality implementation. The only accepted shortcuts called out by the project are no mandatory tests and no mandatory linting.

## Commit Conventions

- When creating commits, include a descriptive commit body that explains the change, not only a subject line.
- Add a `Co-Authored-By: Codex <codex@openai.com>` trailer for the agent that performed the work, matching the style used in recent commits.

## Development Commands

Use `devctl.py` for development servers:

```bash
uv run ./devctl.py start [front|back|all]
uv run ./devctl.py stop [front|back|all]
uv run ./devctl.py restart [front|back|all]
uv run ./devctl.py status
uv run ./devctl.py logs [front|back]
```

Default ports are frontend `5173` and backend `3500`.

Do not restart development servers unless the user asks. After backend changes, tell the user to restart with `devctl.py` if needed.

## Data Directory And Worktrees

Persistent data lives in the TwiCC data directory:

```text
<data_dir>/
├── .env
├── db/data.sqlite
└── logs/
```

Data directory priority:
1. Git worktree root, when launched through `devctl.py`
2. `$TWICC_DATA_DIR`
3. `~/.twicc/`

When running Python or Django code manually inside a worktree, always set `TWICC_DATA_DIR` to the worktree root. `paths.py` does not detect worktrees by itself; `devctl.py` injects the correct environment only for processes it starts.

Example:

```bash
cd <worktree>
TWICC_DATA_DIR=$PWD uv run python -m django <command> --settings=twicc.django.settings
```

Before running migrations or database-affecting commands, verify the resolved database path points inside the intended worktree.

If the user asks to exit, kill, delete, or otherwise leave a worktree, run `uv run ./devctl.py stop all` from that worktree first.

## Operations Reserved To The User

Do not do these on your own initiative:

- Run Django migrations after model changes. Create the migration if requested, then remind the user to run `migrate`.
- Restart development servers. Remind the user when a restart is needed.
- Install packages. If dependencies were added, remind the user to run `npm install`, `npm ci`, or `uv add` as appropriate.
- Run release-test `uvx` commands. For releases, the user must test the built wheel before commit/tag/publish steps continue.

If the user explicitly asks for any of these operations, perform them without asking for another confirmation.

## Backend Patterns

- Use `orjson` for backend JSON work instead of the standard `json` module.
- Use `NamedTuple` for simple immutable data containers instead of dataclasses when mutability is not needed.
- Use aliased imports (`from X import Y as Z`) sparingly, only when strictly necessary: either to avoid a name collision in scope, or to disambiguate intent at the call site for a noticeably less generic verb (e.g. `patch_client as patch_client_for_logging`). Avoid the cosmetic prefix/suffix style (`as _foo`, `as django_settings`, `as ProcessRunModel`) when the bare name doesn't actually conflict — it adds noise and makes it harder to grep for the canonical symbol.
- Key models live in `src/twicc/core/models.py`: `Project`, `Session`, `SessionItem`, `ToolResultLink`, `AgentLink`, `ModelPrice`, `UsageSnapshot`, `WeeklyActivity`, and `DailyActivity`.
- JSONL files are append-only. Sync logic uses offsets and line numbers to incrementally ingest new lines.

## Frontend Patterns

- Use Vue Composition API with `<script setup>`.
- Avoid circular imports that break Vite HMR:
  - Do not import `router.js` statically from utilities, composables, or stores. Use lazy `await import(...)` if needed.
  - Avoid mutual static imports between stores or between stores and composables. Use lazy imports in the less frequent direction.
  - Do not statically import Vue components from composables when that creates cycles; use `defineAsyncComponent`.
- Never parse `item.content` directly. Use helpers from `frontend/src/utils/parsedContent.js`:
  - `getParsedContent(item)`
  - `setParsedContent(item, parsed)`
  - `clearParsedContent(item)`
  - `hasContent(item)`
- Agent settings are a closed cross-provider bundle: `selected_model`, `effort`, `thinking_enabled`, `permission_mode`, `context_max`, `claude_in_chrome`. New provider-specific session flags should follow the same shared-session-row pattern unless the user asks otherwise.

## Web Awesome

- Every used Web Awesome component must be imported explicitly in `frontend/src/main.js`.
- Web Awesome 3 native browser events are unprefixed, such as `@click`, `@focus`, and `@input`.
- Web Awesome custom events keep the `wa-` prefix, such as `@wa-show`, `@wa-hide`, and `@wa-after-show`.
- Use `slot="start"` and `slot="end"` for icons inside buttons. For `wa-dropdown-item`, use `slot="icon"`.
- Local docs are available at `frontend/node_modules/@awesome.me/webawesome/dist/llms.txt` and under `frontend/node_modules/@awesome.me/webawesome/dist/skills/`.

## Dialog Forms

For forms inside `wa-dialog`, use `frontend/src/components/ProjectEditDialog.vue` as the reference pattern:

- Wrap fields in a `<form>` with `@submit.prevent`.
- Put submit buttons outside the form when needed and set the `form` attribute via `setAttribute()`.
- Use `@wa-after-show` for focus management.
- Trim text inputs before validation and submission.
- Use client-side uniqueness validation where possible, with backend constraints as enforcement.
- Use `wa-callout variant="danger"` for validation and API errors.
- Set responsive dialog width with `--width: min(Xpx, calc(100vw - 2rem))`.

## Release Process

When the user asks for a release:

1. Verify the branch is `main`; stop if it is not.
2. Update the version in `pyproject.toml` and the `twicc` package entry in `uv.lock`.
3. Update `CHANGELOG.md`: convert `[Unreleased]` to the release version and date.
4. Run `./scripts/build-release.sh`.
5. Ask the user to test the built wheel with the appropriate `uvx --from dist/... twicc` command. Do not run this yourself.
6. Continue only after the user confirms testing passed.
7. Commit with `release: v{version}`.
8. Create an annotated tag with the changelog content. Convert relative changelog image URLs to `https://raw.githubusercontent.com/twidi/twicc/main/...` in the tag message only.
9. Push commit and tag.
10. Create the GitHub Release with the same changelog content.
11. Give the user the `uvx uv-publish /home/twidi/dev/twicc-poc/dist/twicc-{version}*.whl` command. Do not publish unless explicitly asked. The sdist is intentionally not published (it does not embed the Codex binary nor the built frontend assets — `pip install` from the sdist would trigger a fragile local build).
