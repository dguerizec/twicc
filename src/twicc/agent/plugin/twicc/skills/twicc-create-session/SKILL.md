---
name: twicc-create-session
description: Create a new TwiCC session with a prompt and optional agent settings (provider, model, effort, permission mode, attachments, etc.). Use when you or the user want to spawn a fresh Claude Code or Codex session, kick off a sub-task in another project, or scaffold conversations from a script.
argument-hint: <prompt>
---

# TwiCC Create Session

Spawn a new agent session. The session appears in TwiCC exactly as if started from the UI.

## When to use

- You or the user want to start a new session in a project.
- You want to kick off work on a sub-task or separate project.
- A script needs to queue work into TwiCC programmatically.

## How to invoke

TwiCC's executable varies by launch mode (uvx, dev, installed tool). ALWAYS USE THIS TO RESOLVE $TWICC AT THE START OF EACH BASH INVOCATION:

```bash
TWICC=${TWICC_BIN:-$(command -v twicc 2>/dev/null)}
[ -n "$TWICC" ] || { echo "TwiCC executable not found in this context" >&2; exit 1; }
```

Then run `$TWICC <args>` — **never quote `$TWICC`** (use `$TWICC args`, never `"$TWICC" args`): it may expand to multiple words, which quoting would break.

## Usage

```bash
$TWICC create-session [OPTIONS] '<PROMPT>'
```

### Arguments

- `PROMPT` — the first user message. Inline text or a path to a UTF-8 file.

### Options

- `--project PATH-OR-ID` — directory path or project id (**drop the leading dash** on ids). Non-existent directories are auto-created as projects. Defaults to the current working directory.
- `--provider claude_code|codex` — falls back to the synced `defaultProvider`.
- `--preset NAME` — saved agent-settings preset. Per-flag options override preset values. Run `$TWICC create-session --help` for the current list.
- `--title TEXT` — **always pass this.** A concise 5–7 word title derived from the prompt. Don't rely on the auto-derived title.
- `--timeout SECONDS` — seconds to wait for the server's response (default 30). If the CLI times out, the session may still get created.
- `--json` — emit a single JSON object on stdout (implies `--no-color`).
- `--no-color` — disable ANSI colors.

### Agent settings

All optional. Omit to use preset / synced defaults. Run `$TWICC create-session --help` for the current list (model values can shift over time).

- `--model VALUE` — Claude Code: `opus`, `sonnet`, `opus-4.7`, `opus-4.6`, `opus-4.5`, `sonnet-4.5`. Codex: `gpt`, `gpt-mini`, `gpt-5.4`.
- `--effort VALUE` — Claude Code: `low`, `medium`, `high`, `xhigh`, `max`. Codex: `low`, `medium`, `high`, `xhigh`.
- `--permission-mode VALUE` — Claude Code: `default`, `auto`, `acceptEdits`, `plan`, `dontAsk`, `bypassPermissions`. Codex: `read_only`, `strict`, `auto`, `autonomous`, `yolo`.
- `--thinking / --no-thinking` — Claude Code only.
- `--claude-in-chrome / --no-claude-in-chrome` — Claude Code only (Allows to manipulate browser tabs, take screenshots, etc.).
- `--fast-mode / --no-fast-mode` — Claude Code only (Opus only; billed against extra credits).
- `--context-max VALUE` — Claude Code: `200k` or `1m` (silently capped to 200k on unsupported models). Codex: `272k`.
- `--question-widget / --no-question-widget` — Claude Code only. See below.

### `--hidden`

Creates the session invisible in every user-facing listings, search, and broadcasts (still counted in cost aggregates). Requires a non-interactive `--permission-mode` (Claude Code: `bypassPermissions` or `dontAsk`; Codex: `yolo` or `strict`). `--hidden` forces `question_widget=False` automatically — passing `--question-widget` alongside is rejected.

### `--no-question-widget`

By default (Claude Code), questions from the agent surface as an interactive UI widget (`AskUserQuestion`) — the user must click in the TwiCC UI to answer. Pass `--no-question-widget` when driving the workflow from a script: questions then appear as plain text in the conversation, readable via `messages` and answerable via `send-message`.

### Attachments

- `--attach PATH` (repeatable). Accepted types (sniffed by magic bytes): Claude Code: PNG, JPEG, GIF, WebP, PDF, text/plain; Codex: images only. Per-file cap: 5 MB. Per-batch cap: 100 files, 32 MB. Images are auto-resized to the provider/model's long-edge cap.

## Errors

### Local (exit 1)

- `unsupported_field` — flag not supported by the chosen provider.
- `invalid_choice` — value out of the provider's allowed set.
- `hidden_constraint_violation` — `--hidden` used with an interactive permission mode or `--question-widget`.

### Server (exit 3)

- `provider_disabled` — enable the provider from the UI.
- `project_not_found` / `project_no_directory` — `--project` didn't resolve.
- `manager_busy` — transient; retry.

## Output format

```json
{"status":"created","session_id":"...","provider":"...","project_id":"...","request_uuid":"..."}
{"status":"validation_error","errors":[{"field":"--effort","code":"invalid_choice","message":"..."}]}
{"status":"rejected","errors":[{"field":"...","code":"...","message":"..."}],"request_uuid":"..."}
{"status":"failed","error":"...","request_uuid":"..."}
{"status":"timeout","received_seen":true,"message":"...","request_uuid":"..."}
```

### Exit codes

- `0` — Session created
- `1` — Local validation error
- `2` — TwiCC server not running
- `3` — Server rejected
- `4` — Server error
- `5` — Timeout
- `64` — Bad CLI usage

## Examples

```bash
$TWICC create-session 'Run the tests and fix the failing ones'
$TWICC create-session --project /home/twidi/dev/myproj --provider claude_code 'Add a /healthz endpoint'
$TWICC create-session --project /home/twidi/dev/myproj --preset 'deep think' /home/twidi/prompts/audit.md
$TWICC create-session --provider claude_code --preset 'deep think' --effort low 'Quick review of last commit'
$TWICC create-session --provider claude_code --attach /home/twidi/screenshot.png --attach /home/twidi/report.pdf 'What do you think?'
$TWICC create-session --provider claude_code --no-question-widget 'Resize images — ask me before overwriting'
$TWICC create-session --json --provider claude_code 'Hello'
# → {"status":"created","session_id":"...","provider":"claude_code","project_id":"...","request_uuid":"..."}
```

## Following up

Creation returns immediately; the agent keeps working in the background.

**Check state:** `$TWICC process <SESSION_ID>` (skill: `twicc-process`):
- `assistant_turn` → still working.
- `awaiting_user_input` → blocked on a pending UI dialog. Do NOT call `send-message` — the user must click in the TwiCC UI first. Fetch what's being asked with `$TWICC session <ID> messages --tail 1`.
- `user_turn` → done; fetch the reply with `$TWICC session <ID> messages --tail 1`.
- `starting` → still booting; retry shortly.
- Exit 1 (no process row) → the process finished and was cleaned up. Check `messages --tail 1`: if the last message is from the assistant, the turn completed; if still from the user, the agent likely crashed.

**Continue the conversation:** once at `user_turn`, post a follow-up with `$TWICC send-message <SESSION_ID> '<text>'` (skill: `twicc-send-message`). To change settings mid-session, use `$TWICC update-session <SESSION_ID> settings ...` (skill: `twicc-update-session`).

## Related commands

- `$TWICC send-message <session_id>` — send a follow-up. Skill: `twicc-send-message`.
- `$TWICC process <session_id>` — check agent state. Skill: `twicc-process`.
- `$TWICC processes --spawned-by self` — track sessions you spawned. Skill: `twicc-processes`.
- `$TWICC update-session <session_id> settings` — change agent settings. Skill: `twicc-update-session`.
- `$TWICC session <session_id>` — full metadata. Skill: `twicc-session`.
- `$TWICC sessions --project <PROJECT>` — browse sessions in the project. Skill: `twicc-sessions`.

## How to present results

1. On success, give the title, session id, and a clickable link: `[link text](/project/{project_id}/session/{session_id})`.
2. On validation error, summarize the failing fields with expected values.
3. On exit 3, diagnose from `errors[].code` (see Errors section above).
