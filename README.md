# TWICC

> **T**he **W**eb **I**nterface for **C**laude **C**ode

A web UI to browse and interact with your [Claude Code](https://docs.anthropic.com/en/docs/claude-code) sessions. View projects, sessions, conversation history, costs, and run Claude agents — all from your browser.

![TWICC screenshot](https://raw.githubusercontent.com/twidi/twicc/main/frontend/public/screenshots/session-example.webp)

[![Crafted with love](https://img.shields.io/badge/crafted_with-love-red?style=social&logo=githubsponsors&logoColor=red)](https://github.com/sponsors/twidi)
[![PyPI version](https://img.shields.io/pypi/v/twicc?logo=pypi&logoColor=blue&style=social)](https://pypi.org/project/twicc/)
[![Python versions](https://img.shields.io/pypi/pyversions/twicc?logo=pypi&logoColor=blue&style=social)](https://pypi.org/project/twicc/)
[![GitHub license](https://img.shields.io/github/license/twidi/twicc?logo=github&style=social)](https://github.com/twidi/twicc/blob/main/LICENSE)
[![GitHub release](https://img.shields.io/github/v/release/twidi/twicc?logo=github&style=social)](https://github.com/twidi/twicc/releases)
[![Twitter](https://img.shields.io/badge/Twitter-Twidi-blue?style=social&logo=x&logoColor=blue)](https://x.com/twidi)

## Disclaimer

This is a personal project made by Twidi for his own needs. It is freely available and you are welcome to use it however you see fit.

That said, **no support is guaranteed**. I am open to suggestions, improvements, and contributions, but there is no commitment to address issues or review pull requests.

Note: The project was almost entirely vibe-coded, with general oversight from the author.

## Quick start

> **Note:** TWICC supports **Linux and macOS** only — see [Platform support](#platform-support).

```bash
uvx twicc@latest
```

Then open http://localhost:3500.

> **Don't have `uvx`?** It comes with [uv](https://docs.astral.sh/uv/), a fast Python package manager. Install it with:
> ```bash
> curl -LsSf https://astral.sh/uv/install.sh | sh
> ```
> If you prefer using `pip install twicc` in your own virtualenv, that works too.

### Permanent install

If you use TWICC regularly, you can install it as a persistent tool:

```bash
uv tool install twicc
```

Then simply run:

```bash
twicc
```

To update to the latest version:

```bash
uv tool upgrade twicc
```

## Features

- **Projects & sessions:** browse all your Claude Code projects and sessions
- **Conversation history:** view full history with tool use details
- **Interactive agents:** start and interact with Claude agents from the browser
- **Tool approvals:** handle tool approvals and answer Claude's questions interactively
- **Session control:** full control of model, context window (200K / 1M), effort, thinking and permissions
- **Command palette:** quick access via Ctrl+K (Cmd+K on Mac), slash commands (`/`), and file references (`@`)
- **Inline code comments:** click a line number in code blocks to annotate, then send formatted review comments to Claude — human review of AI-generated code, right from the browser
- **Message snippets:** reusable text snippets with placeholder support, scoped globally or per-project
- **Multiple terminals:** simultaneous terminal sessions with custom snippets, scoped globally or per-project
- **Costs & usage:** track costs and token usage per session
- **Activity & quotas:** daily activity heatmaps and graphs, and usage history graphs (utilization & burn rate for 5h and 7-day quotas)
- **Git integration:** log, diffs, file browser
- **Full-text search:** across all sessions (Ctrl+Shift+F) with in-session search (Ctrl+F)
- **Workspaces:** organize projects into named, color-coded groups to focus your workflow — scoped session list, search, snippets, aggregated activity stats, and auto-add projects via directory patterns
- **Self-aware:** TwiCC ships a Claude Code plugin (with skills) that lets Claude query your projects, sessions, costs, and search history — Claude knows about itself
- **CLI:** JSON-output subcommands for scripting (projects, sessions, search, usage…, run `twicc` with `--help` for details)
- **Cron job persistence:** scheduled tasks survive TwiCC restarts and are automatically renewed before expiry — unlike Claude Code's CLI where they are lost on restart and expire after 7 days
- **Status monitoring:** Claude Code status monitoring via status.claude.com
- **Password protection:** optional password protection
- **Real-time updates:** via WebSocket
- **Mobile-friendly:** fully responsive interface

## Remote access

The interface is fully usable on mobile. Combined with a tunnel service like [Cloudflare Tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/) or [ngrok](https://ngrok.com/), you can access TWICC from anywhere and interact with Claude Code on the go.

> **Important:** If you expose TWICC over the internet, make sure to enable password protection (see [Configuration](#configuration) below) and/or use a tunnel service proposing access control features. TWICC does not have built-in access control beyond optional password protection, so securing it properly is crucial when exposed to the internet.

## Mobile usage

The interface is designed to work well on mobile devices. The terminal includes touch-based text selection (drag to select, auto-copied to clipboard), scrollbar support, and several mobile-specific features:

- **Extra keys bar:** a tabbed bar (Essentials / More / F-keys) with modifier keys (tap for one-shot, double-tap to lock), arrow keys, special characters, paste, and function keys
- **Custom combos:** define your own key combinations and sequences for quick access
- **Custom snippets:** reusable text snippets (with placeholder support) for the terminal, scoped globally or per-project
- **Context-aware scroll:** smooth scrolling across all modes (normal, tmux, alternate screen)

> **Tip:** The author uses [Unexpected Keyboard](https://play.google.com/store/apps/details?id=juloo.keyboard2) on Android for an even better terminal experience — it natively supports Ctrl, Esc, Tab, and other keys that complement TWICC's built-in keys bar.

## How it works

TWICC reads the JSONL data files that Claude Code stores in `~/.claude/projects/` and indexes them into a local SQLite database (`~/.twicc/db/data.sqlite`). These data files remain the source of truth for everything displayed in the interface — TWICC never modifies them. Whether you use Claude Code directly from the terminal or interact through TWICC, everything shows up in the same place.

When you start a session or send messages through TWICC, it uses the [Claude Agent SDK](https://github.com/anthropics/claude-code-sdk-python) to run real Claude Code processes under the hood. This means it uses your existing Claude Code credentials and configuration — there is nothing extra to set up. The conversation data written by Claude Code is then picked up by TWICC's file watcher and displayed in real time.

On each startup, TWICC detects changes and updates its database accordingly. While running, it watches the filesystem for new sessions and updates in real time.

## Requirements

- Python 3.13+
- A `~/.claude/projects/` directory (created by Claude Code)

## Configuration

All configuration is via environment variables, set in `~/.twicc/.env`:

| Variable              | Default     | Description                                |
|-----------------------|-------------|--------------------------------------------|
| `TWICC_PORT`          | `3500`      | Server port                                |
| `TWICC_PASSWORD_HASH` | *(empty)*   | SHA-256 hash to enable password protection |
| `TWICC_DATA_DIR`      | `~/.twicc/` | Data directory (database, logs)            |

Generate a password hash:

```bash
python -c "import hashlib; print(hashlib.sha256(b'your_password').hexdigest())"
```

## Development

```bash
git clone https://github.com/twidi/twicc.git
cd twicc
cd frontend && npm install && cd ..
```

Two ways to run in development:

**With `devctl.py`** (recommended) — runs Vite dev server with hot-reload, no build step needed:

```bash
./devctl.py start
```

Run `./devctl.py --help` for all available commands and configuration options.

**With `run.py`** — runs the backend only, requires a frontend build first:

```bash
cd frontend && npm run build && cd ..
uv run run.py
```

Optionally, install dev tools (django-extensions, ipython):

```bash
uv sync --group dev
```

### Codex Python SDK — vendored

The Codex provider relies on OpenAI's Codex Python SDK (`codex_app_server`) plus the Codex CLI binary it drives over JSON-RPC. Neither is consumed from PyPI in a normal way:

- The SDK itself (`openai-codex-app-server-sdk`) is **not published on PyPI**. We vendor its source into `src/codex_app_server/`, extracted from the `openai/codex` GitHub repo at tag [`rust-v0.131.0-alpha.4`](https://github.com/openai/codex/releases/tag/rust-v0.131.0-alpha.4).
- The Codex CLI binary (`openai-codex-cli-bin`) **is** on PyPI, but the Linux wheels are tagged `musllinux_1_1_*` only — uv refuses those on glibc systems even though the binaries are static-pie ELFs that run anywhere. So we **fetch the wheel ourselves at build time** from the same GitHub release, extract just the binary, and bundle it into our wheel under a regular `linux_x86_64` (or `macosx_*` / `win_amd64`) platform tag.

Layout:

| Path                                              | Origin                                                    |
|---------------------------------------------------|-----------------------------------------------------------|
| `src/codex_app_server/`                           | Vendored SDK source. Treat as read-only — edits land upstream, then we re-sync. |
| `src/codex_app_server/_bundled/codex` (or `.exe`) | Fetched at build by `hatch_build.py`. Gitignored.         |
| `hatch_build.py`                                  | Hatch build hook: fetches the binary for the target platform and stamps the wheel platform-specific. |
| `scripts/build-release.sh`                        | Drives `hatch_build.py` once per target platform to produce all release wheels. |

**Updating to a newer Codex version** (assuming a local clone of `openai/codex` exists):

1. Pick a release tag matching the upstream version you want, e.g. `rust-v0.131.0-beta.1`. Verify it has both the matching `sdk/python/src/codex_app_server/` source and an `openai_codex_cli_bin-…whl` per target platform.
2. Update `CODEX_RELEASE_TAG` and `CODEX_BIN_VERSION` in `hatch_build.py`.
3. Re-vendor the SDK source:
   ```bash
   rm -rf src/codex_app_server
   mkdir -p src/codex_app_server
   ( cd <codex_repo> && \
     git archive <new_tag> sdk/python/src/codex_app_server ) | \
     tar -x --strip-components=4 -C src/codex_app_server
   ```
4. Diff the new SDK's `pyproject.toml` against ours — copy any new runtime dependency over (today only `pydantic>=2.12` is shared).
5. Run `./scripts/build-release.sh` and check each wheel installs and runs locally.

**Exit condition** — the day OpenAI publishes a `manylinux` wheel for `openai-codex-cli-bin`, every line of this vendoring goes away: depend on the two packages from PyPI as regular dependencies, drop `src/codex_app_server/`, drop the binary-fetching logic in `hatch_build.py`, and the wheel is back to a single platform-agnostic `py3-none-any`.

### Building and publishing

```bash
./scripts/build-release.sh     # Build sdist + one wheel per platform → dist/
uv publish                     # Publish all artifacts to PyPI
```

The release script runs `npm ci` + `npm run build` in `frontend/`, then iterates over the supported target platforms (`linux_x86_64`, `macosx_11_0_arm64`, `macosx_10_9_x86_64`, `win_amd64`) and produces one platform-tagged wheel each, plus a single platform-agnostic sdist. See the [Codex Python SDK section above](#codex-python-sdk--vendored) for why the wheel isn't `py3-none-any`.

## Tech stack

- **Backend:** Django 6, Uvicorn, Django Channels (WebSocket)
- **Frontend:** Vue.js 3, Vite
- **Database:** SQLite (with WAL mode)
- **Process management:** Claude Agent SDK

## Platform support

TWICC runs on **Linux and macOS**. There is no Windows support — the codebase relies on Unix-specific system APIs (PTY, process signals, process groups) that would require significant work to adapt, and the author does not have access to a Windows machine for development and testing.

**WSL (Windows Subsystem for Linux)** is the most realistic path for Windows users. TWICC should work unmodified under WSL2, though this has not been tested. If you are a Windows user and would like to help with testing or contributing WSL compatibility fixes, feel free to open an issue or a pull request.

## FAQ

**Can I use TWICC while Claude Code is running?**
Yes. TWICC only reads Claude Code's data files and never modifies them.

**Where is my data stored?**
By default in `~/.twicc/`. This includes the SQLite database and log files. Set `TWICC_DATA_DIR` to change the location.

**Where are the logs?**
In `~/.twicc/logs/backend.log`. This file is useful for troubleshooting startup or runtime issues.

**How do I reset the database?**
Delete `~/.twicc/db/data.sqlite*` and restart TWICC. It will rebuild from Claude Code's source files.

**Is this allowed by Anthropic?**
TWICC uses the [Claude Agent SDK](https://github.com/anthropics/claude-code-sdk-python) with the official Claude Code system prompt. As of April 2026, this is permitted by Anthropic's terms of service.

**How can I support this project?**
If you find TWICC useful, consider [sponsoring me on GitHub](https://github.com/sponsors/twidi) — it means a lot and helps keep the project going!

## License

MIT
