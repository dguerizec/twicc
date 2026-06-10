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
- Code, comments, variable names, UI strings, and documentation — design/plan/spec files included — must be in English. French is only for live chat with the user (and the developer's personal `AGENTS.local.md`).
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

Default ports are frontend `5173` and backend `3500`. In an additional worktree, `devctl.py` auto-picks the next free ports (e.g. `5174`/`3501`) and copies the DB.

When asked to start/restart servers, run the **single** `devctl.py` command and read the logs — nothing else. devctl does everything: it rebuilds the editable install (running `npm ci` to install frontend `node_modules`), auto-applies pending Django migrations at backend startup, copies the DB, and picks ports. **Never run `npm install`/`npm ci`, `migrate`, or touch `node_modules` yourself to bring servers up** — a parallel `npm install` corrupts devctl's `npm ci` (`ENOTEMPTY`) and fails the build. devctl's post-start port check may time out during the backend's initial sync; that is not a failure — confirm via `logs/backend.log`.

Do not restart development servers unless the user asks. After backend changes, tell the user to restart with `devctl.py` if needed.

## Data Directory And Worktrees

In this section, `worktree` means an additional Git checkout created with
`git worktree` (typically under `.worktrees/`). Do not use this term for the
main repository checkout.

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

When running Python or Django code manually inside an additional Git worktree,
always set `TWICC_DATA_DIR` to that worktree root. `paths.py` does not detect
additional worktrees by itself; `devctl.py` injects the correct environment
only for processes it starts.

When running commands manually from the main repository checkout, do not set
`TWICC_DATA_DIR=$PWD` by default. Leave it unset to use the normal TwiCC data
directory unless the user explicitly wants checkout-local data.

Example:

```bash
cd <worktree>
TWICC_DATA_DIR=$PWD uv run python -m django <command> --settings=twicc.settings
```

Before running migrations or database-affecting commands, verify the resolved database path points inside the intended worktree.

If the user asks to exit, kill, delete, or otherwise leave a worktree, run `uv run ./devctl.py stop all` from that worktree first.

## Operations Reserved To The User

Do not do these on your own initiative:

- Run Django migrations after model changes. Create the migration if requested, then remind the user to run `migrate`. (This is about the user's own running instance; starting/restarting servers via `devctl.py` auto-applies migrations, so never run `migrate` by hand to bring servers up.)
- Restart development servers. Remind the user when a restart is needed.
- Install packages. If dependencies were added, remind the user to run `npm install`, `npm ci`, or `uv add` as appropriate. (Starting servers via `devctl.py` already runs `npm ci`; never pre-run it yourself to launch servers.)
- Run release-test `uvx` commands. For releases, the user must test the built wheel before commit/tag/publish steps continue.

If the user explicitly asks for any of these operations, perform them without asking for another confirmation.

## TwiCC Plugin (Agent Skills)

The agent-facing skills live under `src/twicc/agent/plugin/twicc/skills/`, packaged as a versioned plugin declared in `src/twicc/agent/plugin/twicc/.claude-plugin/plugin.json`.

Any change to the skill bundle — adding a new `SKILL.md`, editing the body of an existing one, renaming, or removing — REQUIRES bumping the `version` field in `plugin.json`. Without a bump, providers (Claude Code, Codex, ...) may keep an older copy cached and serve stale instructions to agents. The version is the signal "the bundle changed, refresh your local copy."

**Before creating or updating any skill, read `src/twicc/agent/plugin/README.md`.** It documents the established structure, wording rules, and anti-patterns for TwiCC skills. Also read a few existing skills to calibrate tone and level of detail.

Bump rule of thumb: any user-visible skill change → bump the patch (`0.10.0` → `0.10.1`); a new skill or an existing one with new flags/options → bump the minor (`0.10.0` → `0.11.0`); skill rename / removal → bump the minor at least.

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
- Agent settings are a closed cross-provider bundle: `selected_model`, `effort`, `thinking_enabled`, `permission_mode`, `context_max`, `claude_in_chrome`, `fast_mode`. New provider-specific session flags should follow the same shared-session-row pattern unless the user asks otherwise.
- Per-project agent defaults (`Project.default_provider` + `Project.default_agent_settings`) seed NEW sessions only, inherited up the chain (worktree main repo, then path ancestors). Resolution happens at creation time only — `frontend/src/utils/projectAgentDefaults.js` (UI drafts) and its mirror `src/twicc/project_agent_defaults.py` (CLI `create-session`); never wire it into per-turn settings resolution.
- `permission_mode_if_untrusted` is NOT in the bundle: it is a default-shaping field (global settings / presets / per-project defaults only, never on `Session`). In an untrusted (or unknown-trust) project the session's single `permission_mode` is resolved/clamped against the provider's `UNTRUSTED_PERMISSION_MODES` (no `bypassPermissions`/`yolo`) — enforced by the backend floor (`core/services/trust.py`) and mirrored by the CLI. Project trust is a human-only decision, never an agent-facing flag/skill. See `docs/plans/2026-06-09-project-trust-design.md` §13.

## Web Awesome

- Every used Web Awesome component must be imported explicitly in `frontend/src/main.js`.
- Web Awesome 3 native browser events are unprefixed, such as `@click`, `@focus`, and `@input`.
- Web Awesome custom events keep the `wa-` prefix, such as `@wa-show`, `@wa-hide`, and `@wa-after-show`.
- Use `slot="start"` and `slot="end"` for icons inside buttons. For `wa-dropdown-item`, use `slot="icon"`.
- Local docs are available at `frontend/node_modules/@awesome.me/webawesome/dist/llms.txt` and under `frontend/node_modules/@awesome.me/webawesome/dist/skills/webawesome/`.

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
4. Run `./scripts/build-release.sh`. It deletes `src/twicc/static/frontend/` before `uv build` so the frontend is always rebuilt from source — the `hatch_build.py` hook otherwise reuses a stale build sitting there and silently ships an outdated UI (how 1.7.1 went out broken). Never package a release without that clean rebuild.
5. Ask the user to test the built wheel with the appropriate `uvx --from dist/... twicc` command. Do not run this yourself.
6. Continue only after the user confirms testing passed.
7. Commit with `release: v{version}`.
8. Create an annotated tag with the changelog content. Convert relative changelog image URLs to `https://raw.githubusercontent.com/twidi/twicc/main/...` in the tag message only.
9. Push commit and tag.
10. Create the GitHub Release with the same changelog content.
11. Give the user the `uvx uv-publish /home/twidi/dev/twicc-poc/dist/twicc-{version}*` command (the glob covers both the wheel and the sdist). Do not publish unless explicitly asked. The sdist is now publishable — it embeds the pre-built frontend assets and the Codex CLI binary comes from the `openai-codex-cli-bin` PyPI dependency, so `pip install` from source needs neither npm nor an extra fetch.
