---
name: twicc-send-message
description: Send a message (and optional attachments) to an existing TwiCC session, regardless of its provider (Claude Code, Codex, ...). The session keeps its currently stored agent settings (model, effort, permission mode, ...). Use when the user wants to continue a conversation in another session, drop a follow-up from a script, or attach files to an ongoing session.
argument-hint: <session_id> <prompt>
---

# TwiCC Send Message

Send a message to an existing agent session by dropping a request file the live TwiCC server picks up. The message ends up in the session as if it had been typed from the UI.

## When to use

- The user asks to "send / push / forward a message to" an existing session
- A script needs to programmatically queue follow-up work into an existing session (use `--json` for machine-parseable output)
- The user wants to attach files (images, PDFs, text) to an ongoing conversation


## How to invoke

TwiCC's executable varies by launch mode (uvx, dev, installed tool). Resolve it once at the start of each Bash invocation:

```bash
TWICC=${TWICC_BIN:-$(command -v twicc 2>/dev/null)}
[ -n "$TWICC" ] || { echo "TwiCC executable not found in this context" >&2; exit 1; }
```

Then run any subcommand via `$TWICC <args>` — **do NOT quote `$TWICC`** (use `$TWICC args`, never `"$TWICC" args`). The variable may expand to a multi-word command (e.g. `uv run --directory ... run.py`); Bash relies on word-splitting to parse it, and quoting it would treat the entire expansion as a single program name and fail with "No such file or directory". All bash examples below use the unquoted form.

## Prerequisite: the server must be running

The command communicates with the live TwiCC server through the data directory (no network, no auth). It checks `<data_dir>/twicc.heartbeat` first and fails fast (exit code 2) if it's missing or stale (> 15 s old). If the user gets that error, ask them to start the server (`twicc` in another terminal) and retry.

## How to send a message

Basic shape:

```bash
$TWICC send-message [OPTIONS] '<SESSION_ID>' '<PROMPT>'
```

### Required arguments

- **`SESSION_ID`** — id of the existing session to send to. You can get one from `twicc sessions` (browse) or directly from the UI URL.
- **`PROMPT`** — the message text. Either inline text or an absolute/relative path to a UTF-8 file whose content will be used as the message.

### Options

- `--attach PATH` — repeatable. Each call adds one file.
- Accepted types per provider (sniffed by magic bytes, not extension):
  - **Claude Code**: PNG, JPEG, GIF, WebP, PDF, text/plain
  - **Codex**: images only (PNG, JPEG, GIF, WebP)
- Per-file cap: 5 MB. Per-batch cap: 100 files, 32 MB total.
- Images are auto-resized to the long-edge cap the provider/model accepts (see `twicc-create-session` for the exact rules — same code path).
- `--timeout SECONDS` — how long to wait for the server's final status (default 30). Independent of the message itself — even if the CLI times out, the message may still get delivered on the server.
- `--json` — emit a single JSON object on stdout instead of pretty text (implies `--no-color`). Use this from scripts.
- `--no-color` — disable ANSI colors. Always implied by `--json`.

## Examples

```bash
# Simplest: send an inline message
$TWICC send-message 4a8352fb-1674-41c0-8a85-0a5a3e4e623a 'Run the tests now'

# Prompt from a file
$TWICC send-message 4a8352fb-1674-41c0-8a85-0a5a3e4e623a /home/twidi/prompts/follow-up.md

# Attachments
$TWICC send-message \
    4a8352fb-1674-41c0-8a85-0a5a3e4e623a \
    --attach /home/twidi/screenshot.png \
    --attach /home/twidi/report.pdf \
    'What do you think of these results?'

# Machine-parseable output for scripts
$TWICC send-message --json \
    4a8352fb-1674-41c0-8a85-0a5a3e4e623a \
    'Hello'
# → {"status":"sent","session_id":"...","provider":"claude_code","project_id":"...","request_uuid":"..."}
```

## Output format

### Text mode (default)

```
✓ Heartbeat OK (last seen 0.7s ago)
✓ Session '4a8352fb-...' resolved (provider: claude_code, project: -home-twidi-dev-myproj)
✓ Prompt resolved (40 chars)
✓ Attachments validated (1 images, 0 documents)
  • screenshot.png — image (image/png), resized 3000x2000 → 1568x1045, 22.0 KB → 7.1 KB
→ Request submitted (request_uuid: 4a8352fb...)
✓ Message sent to session: 4a8352fb-1674-41c0-8a85-0a5a3e4e623a
```

Validation errors (exit code 1) print as:

```
✗ Validation error:
  - SESSION_ID: Session 'abc' not found
  - --attach /tmp/big.pdf: size 8.2 MB exceeds 5 MB limit
```

### JSON mode (`--json`)

A single JSON object, one of:

```json
{"status":"sent","session_id":"...","provider":"...","project_id":"...","request_uuid":"..."}
{"status":"validation_error","errors":[{"field":"SESSION_ID","code":"session_stale","message":"..."}]}
{"status":"rejected","errors":[{"field":"...","code":"...","message":"..."}],"request_uuid":"..."}
{"status":"failed","error":"...","request_uuid":"..."}
{"status":"timeout","received_seen":true,"message":"...","request_uuid":"..."}
```

### Exit codes

| Code | Meaning |
|------|---------|
| 0 | Message sent |
| 1 | CLI validation error (prompt empty, session id unknown, session stale, bad attachment, etc.) |
| 2 | TwiCC server not running (heartbeat missing or stale) |
| 3 | Server rejected the request — see `errors[].code` (typical: `awaiting_user_input`, `provider_disabled`, `session_not_found`/`session_stale` race-detected) |
| 4 | Server hit an unexpected error mid-flight |
| 5 | Timeout waiting for the server's final status |
| 64 | Bad CLI usage (handled by Typer) |

## Following up

The message is queued on the server; the agent will pick it up on its next turn. To check what happened, use the same flow as `twicc-create-session`:

- **Is the assistant working / done?** → `twicc process <SESSION_ID>` (see `twicc-process` skill)
- **Fetch the reply** → `twicc session <SESSION_ID> messages --tail 1`

## Error handling

When the server rejects (exit 3), parse `errors[].code` from JSON mode to give a precise diagnosis:

- **`awaiting_user_input`** → The session has a pending dialog open in the TwiCC UI (tool approval or `AskUserQuestion`). **A CLI message will not unblock it** — the user has to click in the UI first. Tell them exactly that. You can fetch the pending question's text via `twicc session <id> messages --tail 1` to surface what's being asked.
- **`is_subagent`** → The id points to a subagent. **Subagents cannot be messaged directly** — the parent session is the right target. Use `twicc session <id>` to inspect the row and find the parent (`parent_session` field).
- **`provider_disabled`** → The owning provider was disabled in settings; suggest enabling it from the UI.
- **`session_not_found`** → The id doesn't match any session in DB (typo or already deleted).
- **`session_stale`** → The session's JSONL file is gone from disk; the session can't be resumed.
- **`project_no_directory`** → The session's project lost its directory; the agent can't be started.
- **`manager_busy`** → Transient — another operation for the same session is in flight; retry.

## Related commands

- **Create a new session instead:** `twicc create-session` — full options for picking provider, model, settings, project, etc.
- **Change the session's settings, rename, archive, or pin it:** `twicc update-session <session_id> {settings|title|archive|unarchive|pin|unpin} ...` — see the `twicc-update-session` skill. The session is otherwise untouched.
- **Check the live agent's state:** `twicc process <session_id>` — is the agent still working, blocked on a user click, or done? The only reliable way to detect `awaiting_user_input` before sending
- **List all live processes:** `twicc processes --state awaiting_user_input` — to see which sessions are blocked on a user click
- **Read the agent's reply (uniform shape):** `twicc session <session_id> messages --tail 1`
- **Read raw session content:** `twicc session <session_id> content <line_or_range>`
- **List sessions:** `twicc sessions --project <project_id>` — useful to find the right session id
- **Inspect the session:** `twicc session <session_id>` — full metadata

## How to present results

1. On success, give the user the session id, the project id (so they can recognize it), and a clickable link to the session: `[link text](/project/{project_id}/session/{session_id})`.
2. On a validation error, summarise the failing fields with the codes — don't just dump the raw output.
3. If the server is down (exit 2), tell the user to start TwiCC (`twicc` in another terminal) and offer to retry.
4. If the request was rejected (exit 3), follow the diagnosis table above — especially the `awaiting_user_input` case where the user has actual work to do in the UI before the CLI can succeed.
5. Only mention resize info or per-file summaries if the user is debugging or explicitly asks.
