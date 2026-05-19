# TwiCC

> **T**he **W**eb **I**nterface for **C**laude and **C**odex

A single self-hosted web interface for both [Claude Code](https://docs.anthropic.com/en/docs/claude-code) (Anthropic) and [Codex](https://openai.com/codex/) (OpenAI). Browse projects and sessions, run agents, follow them in real time, track costs and quotas, and stay in control of your AI coding work — from desktop or mobile, in your browser.

![TwiCC screenshot](https://raw.githubusercontent.com/twidi/twicc/main/frontend/public/screenshots/session-example.webp)

[![Crafted with love](https://img.shields.io/badge/crafted_with-love-red?style=social&logo=githubsponsors&logoColor=red)](https://github.com/sponsors/twidi)
[![PyPI version](https://img.shields.io/pypi/v/twicc?logo=pypi&logoColor=blue&style=social)](https://pypi.org/project/twicc/)
[![Python versions](https://img.shields.io/pypi/pyversions/twicc?logo=pypi&logoColor=blue&style=social)](https://pypi.org/project/twicc/)
[![GitHub license](https://img.shields.io/github/license/twidi/twicc?logo=github&style=social)](https://github.com/twidi/twicc/blob/main/LICENSE)
[![GitHub release](https://img.shields.io/github/v/release/twidi/twicc?logo=github&style=social)](https://github.com/twidi/twicc/releases)
[![Twitter](https://img.shields.io/badge/Twitter-Twidi-blue?style=social&logo=x&logoColor=blue)](https://x.com/twidi)

## Disclaimer

This is a personal project made by Twidi for his own needs. It is freely available and you are welcome to use it however you see fit.

That said, **no support is guaranteed**. Suggestions, issues, and pull requests are welcome, but there is no commitment to address them.

Note: the project was almost entirely vibe-coded, with general oversight from the author.

## Quick start

```bash
uvx twicc@latest
```

Then open <http://localhost:3500>.

> **Don't have `uvx`?** It comes with [uv](https://docs.astral.sh/uv/), a fast Python package manager:
> ```bash
> curl -LsSf https://astral.sh/uv/install.sh | sh
> ```
> A plain `pip install twicc` in your own virtualenv also works.

### Permanent install

If you use TwiCC regularly, install it as a persistent tool:

```bash
uv tool install twicc
twicc
```

Update with:

```bash
uv tool upgrade twicc
```

## Features

### Claude Code and Codex, side by side

- **Both providers in one UI**, using your existing credentials — nothing extra to set up
- Live conversation with full tool-use details, real-time streaming of assistant text and thinking
- Per-session agent control: model (Opus 4.7, Sonnet 4.6, …), context window (200K / 1M), effort, thinking, permissions, with reusable presets
- Interactive tool approvals and provider questions, answered directly from the browser
- **Persistent Claude Code cron jobs**: scheduled tasks survive TwiCC restarts and are auto-renewed before their 7-day expiry — they would otherwise be lost on a Claude Code CLI restart
- Provider status monitoring (Anthropic and OpenAI status pages) with in-app outage notifications

### Mobile-first, work from anywhere

- Fully responsive UI, designed for touch from the start
- Mobile-friendly terminal: touch selection, paste, proper scrollbar, scroll/select toggle, support for tmux and alternate-screen apps
- **Extra keys bar** (Essentials / More / F-keys) with modifiers (tap for one-shot, double-tap to lock), arrow keys, special characters, function keys
- User-defined key combos and reusable text snippets for the terminal
- Combined with a tunnel ([Cloudflare Tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/), [ngrok](https://ngrok.com/), [Tailscale Funnel](https://tailscale.com/docs/features/tailscale-funnel), …), TwiCC turns a phone into a usable Claude or Codex frontend — see [Remote access](#remote-access) below

### Productivity tools

- **Command palette** (Ctrl+K / Cmd+K): jump to any project, session, or workspace, change session settings, and trigger common actions from a single keyboard-driven menu
- **Slash commands and file references**: `/` for Claude Code skills and commands, `$` for Codex skills, `@` for the file picker — all from the message input
- **Message snippets**: reusable text snippets with placeholders, scoped globally or per-project, for the prompts you keep retyping
- **Inline code comments**: click a line number in a code block to annotate, then send a formatted review comment back to the agent — human review of AI-generated code, right from the browser
- **Selection comments**: select text in Chat, Files, Git, or Terminal to copy it or attach a comment, then send the selection and your note back to the agent, preformatted and ready to go
- Full-text search across all sessions (Ctrl+Shift+F) and in-session search (Ctrl+F)
- Multiple simultaneous terminals, with optional custom `tmux.conf`
- Git integration: log, diffs (including side-by-side image diff), file browser, commit details
- Project and session archiving (including bulk archive of old sessions)

### Costs, usage and activity

- Per-session and per-project cost tracking
- Quota graphs and burn rate for the Claude Code and Codex 5h / 7-day windows
- Daily and weekly activity heatmaps and stats, per project, per workspace, and across all projects

### Workspaces and projects

- Group projects into named, color-coded **workspaces**, with optional auto-add by directory pattern
- Scoped session list, search, snippets, and aggregated stats per workspace
- Workspace-level Files, Git, and Terminal tabs in addition to per-project ones

### Self-aware: the TwiCC plugin

TwiCC ships with a Claude Code / Codex plugin (auto-installed) whose skills let agents query TwiCC itself:

- list and inspect **projects** and **workspaces**
- browse **sessions**, read their messages, list their subagents
- run a full-text **search** across all session history
- check current **usage** and cost estimates
- **spawn new sessions** in any project from within a running session

Use it when you want an agent to look up its own past work, audit costs, or kick off a sub-task in another project — without leaving the chat.

### CLI for scripts

Every list / inspect command outputs JSON, so you can compose TwiCC into scripts:

| Command                                       | Purpose                                              |
|-----------------------------------------------|------------------------------------------------------|
| `twicc projects` / `twicc project <id>`       | List or inspect projects                             |
| `twicc sessions` / `twicc session <id>`       | Browse sessions, read messages, list subagents      |
| `twicc workspaces` / `twicc workspace <id>`   | List or inspect workspaces                           |
| `twicc search "<query>"`                      | Full-text search across all sessions                 |
| `twicc usage`                                 | Latest quota snapshot                                |
| `twicc create-session ...`                    | Spawn a fresh Claude or Codex session with a prompt  |
| `twicc claude` / `twicc codex`                | Run the bundled Claude or Codex CLI binary directly  |

Run `twicc --help` (or `twicc <command> --help`) for full details.

## How it works

TwiCC reads the JSONL data files written by each provider and indexes them into a local SQLite database (`~/.twicc/db/data.sqlite`). Claude Code sessions are read from `~/.claude/projects/`; Codex sessions are read from `~/.codex/sessions/`. **Provider data files remain the source of truth** — TwiCC never modifies them. Whether you use Claude Code or Codex directly from the terminal or through TwiCC, everything shows up in the same place.

When you start a session or send messages through TwiCC, it uses the provider SDK under the hood: the [Claude Agent SDK](https://github.com/anthropics/claude-code-sdk-python) for Claude Code and OpenAI's vendored Codex SDK/CLI for Codex. This means it uses your existing provider credentials and configuration — there is nothing extra to set up. The conversation data written by the provider is then picked up by TwiCC's file watcher and broadcast to the UI in real time over WebSocket.

On each startup, TwiCC detects changes and updates its database accordingly. While running, it watches the filesystem for new sessions and updates them in real time.

## Remote access

The interface is fully usable from a mobile browser. Combined with a tunnel service like [Cloudflare Tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/), [ngrok](https://ngrok.com/), or [Tailscale Funnel](https://tailscale.com/docs/features/tailscale-funnel), you can access TwiCC from anywhere and interact with Claude Code or Codex from your phone.

> **Important:** if you expose TwiCC over the internet, enable password protection (see [Configuration](#configuration)) and prefer a tunnel service with its own access control. TwiCC has no built-in access control beyond optional password protection.

> **Tip:** on Android, the author uses [Unexpected Keyboard](https://play.google.com/store/apps/details?id=juloo.keyboard2) for an even better terminal experience — it natively exposes Ctrl, Esc, Tab, and other keys that complement TwiCC's built-in keys bar.

## Requirements

- Claude Code and/or Codex already configured locally
- For existing history: a `~/.claude/projects/` directory created by Claude Code and/or a `~/.codex/sessions/` directory created by Codex

TwiCC needs Python 3.13+. If you install through `uvx` or `uv tool install` (recommended, see [Quick start](#quick-start)), `uv` will download and manage the right Python version for you — nothing to install by hand. If you go the `pip install` route, install Python 3.13 yourself first.

## Configuration

All configuration goes through environment variables, set in `~/.twicc/.env`:

| Variable              | Default     | Description                                |
|-----------------------|-------------|--------------------------------------------|
| `TWICC_PORT`          | `3500`      | Server port                                |
| `TWICC_PASSWORD_HASH` | *(empty)*   | SHA-256 hash to enable password protection |
| `TWICC_DATA_DIR`      | `~/.twicc/` | Data directory (database, logs, settings)  |

Generate a password hash:

```bash
python -c "import hashlib; print(hashlib.sha256(b'your_password').hexdigest())"
```

## Platform support

TwiCC runs on **Linux** and **macOS**. There is no native Windows support — the codebase relies on Unix-specific APIs (PTY, process signals, process groups) that would require significant work to adapt, and the author does not have access to a Windows machine for development and testing.

**WSL (Windows Subsystem for Linux)** is the recommended path for Windows users. TwiCC has been reported to work correctly under WSL2. If you hit issues, please open an issue or a pull request.

## FAQ

### Can I use TwiCC while Claude Code or Codex is running?

Yes. TwiCC only reads provider data files and never modifies them.

### Where is my data stored?

By default in `~/.twicc/`. This includes the SQLite database, logs, and user settings. Set `TWICC_DATA_DIR` to change the location.

### Where are the logs?

In `~/.twicc/logs/backend.log` for the backend (the file to check first when troubleshooting), and `~/.twicc/logs/sdk/` for raw per-provider SDK message logs.

### How do I reset the database?

Delete `~/.twicc/db/data.sqlite*` and restart TwiCC. It will rebuild from the provider source files.

### Is this allowed by Anthropic?

For Claude Code sessions, TwiCC uses the official [Claude Agent SDK](https://github.com/anthropics/claude-code-sdk-python) with the Claude Code system prompt. This is permitted by Anthropic's terms of service.

**Billing change on June 15, 2026:** until that date, SDK usage is billed against the same plan quota as the regular Claude Code CLI. Starting **June 15, 2026**, Anthropic moves SDK usage to a **separate, plan-specific credits quota** dedicated to the SDK. In practice, TwiCC sessions will no longer share the Claude Code plan quota and may be more constrained depending on your plan tier — see Anthropic's documentation for details.

### Is this allowed by OpenAI?

For Codex sessions, TwiCC uses OpenAI's Codex SDK and CLI binary (vendored — see [`docs/codex-vendoring.md`](docs/codex-vendoring.md)). OpenAI does not document any limitation on this kind of usage, and TwiCC sessions draw from the same plan quota as the regular Codex CLI.

### How can I support this project?

If you find TwiCC useful, consider [sponsoring me on GitHub](https://github.com/sponsors/twidi) — it means a lot and helps keep the project going.

## Development

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for development setup, `devctl.py` usage, building, and the release process. Conventions, architecture notes, and patterns live in [`AGENTS.md`](AGENTS.md) and [`CLAUDE.md`](CLAUDE.md). The Codex SDK vendoring is documented in [`docs/codex-vendoring.md`](docs/codex-vendoring.md).

## License

MIT
