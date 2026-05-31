---
name: twicc-send-message
description: Send a message (and optional attachments) to an existing TwiCC session. Use when you or the user want to continue a conversation in an existing session, drop a follow-up from a script, or attach files to an ongoing session.
argument-hint: <session_id|parent> <prompt>
---

# TwiCC Send Message

Send a message to an existing session. The message is delivered as if typed from the UI.

## When to use

- You or the user want to send a follow-up message to an existing session.
- A script needs to queue work into a running session.
- You want to attach files to an ongoing conversation.

## How to invoke

TwiCC's executable varies by launch mode (uvx, dev, installed tool). ALWAYS USE THIS TO RESOLVE $TWICC AT THE START OF EACH BASH INVOCATION:

```bash
TWICC=${TWICC_BIN:-$(command -v twicc 2>/dev/null)}
[ -n "$TWICC" ] || { echo "TwiCC executable not found in this context" >&2; exit 1; }
```

Then run `$TWICC <args>` — **never quote `$TWICC`** (use `$TWICC args`, never `"$TWICC" args`): it may expand to multiple words, which quoting would break.

## Usage

```bash
$TWICC send-message [OPTIONS] '<SESSION_ID|parent>' '<PROMPT>'
```

### Arguments

- `SESSION_ID` — id of the session to send to, **or** the keyword `parent` to target the session that spawned the calling agent. When using `parent`, fails with `parent_not_found` if not running inside a TwiCC agent or if the current session has no spawner.
- `PROMPT` — message text, or a path to a UTF-8 file. For available slash/dollar commands, use `$TWICC info commands` (skill: `twicc-info`).

### Options

- `--attach PATH` (repeatable) — attach a file. Accepted types (sniffed by magic bytes): Claude Code: PNG, JPEG, GIF, WebP, PDF, text/plain; Codex: images only. Per-file cap: 5 MB. Per-batch cap: 100 files, 32 MB. Images are auto-resized to the provider/model's long-edge cap.
- `--timeout SECONDS` — seconds to wait for the server's response (default 30). If the CLI times out, the message may still get delivered.
- `--json` — emit a single JSON object on stdout (implies `--no-color`).
- `--no-color` — disable ANSI colors.

### Target discovery

To message a sibling or descendant whose id you don't know yet, use `$TWICC topology self` first and pick the target node (skill: `twicc-topology`).

## Errors

### Local (exit 1)

- `session_not_found`
- `is_subagent` — subagents cannot be messaged directly; target the parent session.
- `session_stale`
- `project_no_directory`
- `parent_not_found` — `parent` used but no TwiCC session in the ancestry, or the current session has no `spawned_by` link.

### Server (exit 3)

- `awaiting_user_input` — the session has a pending dialog in the UI; a CLI message cannot unblock it. The user must click in the UI first. Fetch what's being asked with `$TWICC session <id> messages --tail 1`.
- `is_subagent`
- `provider_disabled`
- `session_not_found`
- `session_stale`
- `project_no_directory`
- `manager_busy` — transient; retry.

## Output format

```json
{"status":"sent","session_id":"...","provider":"...","project_id":"...","request_uuid":"..."}
{"status":"validation_error","errors":[{"field":"SESSION_ID","code":"session_stale","message":"..."}]}
{"status":"rejected","errors":[{"field":"...","code":"...","message":"..."}],"request_uuid":"..."}
{"status":"failed","error":"...","request_uuid":"..."}
{"status":"timeout","received_seen":true,"message":"...","request_uuid":"..."}
```

### Exit codes

- `0` — Message sent
- `1` — Local validation error
- `2` — TwiCC server not running
- `3` — Server rejected
- `4` — Server error
- `5` — Timeout
- `64` — Bad CLI usage

## Examples

```bash
$TWICC send-message 4a8352fb-1674-41c0-8a85-0a5a3e4e623a 'Run the tests now'
$TWICC send-message 4a8352fb-1674-41c0-8a85-0a5a3e4e623a /home/twidi/prompts/follow-up.md
$TWICC send-message 4a8352fb-1674-41c0-8a85-0a5a3e4e623a --attach /home/twidi/screenshot.png --attach /home/twidi/report.pdf 'What do you think?'
$TWICC send-message --json 4a8352fb-1674-41c0-8a85-0a5a3e4e623a 'Hello'
# → {"status":"sent","session_id":"...","provider":"claude_code","project_id":"...","request_uuid":"..."}
$TWICC send-message parent 'I finished the sub-task you asked for.'
# From inside an agent: targets the session that spawned it;
```

## Following up

The message is queued; the agent picks it up on its next turn.

- Check state: `$TWICC process <SESSION_ID>` — still working, blocked, or done? Skill: `twicc-process`.
- Read the reply: `$TWICC session <SESSION_ID> messages --tail 1`. Skill: `twicc-session`.

**Let the child talk back:** if the target is a session you spawned, tell it in the message to load the `twicc-send-message` skill and use `send-message parent '<text>'` to reply async — `parent` resolves to you via its `spawned_by` link. Loading the skill is what gives the child the `$TWICC` resolution and full invocation syntax.

## Related commands

- `$TWICC info commands [--provider <key>] [--project <PROJECT>]` — list slash / dollar commands available in the target session's scope before referencing them in the message. Skill: `twicc-info`.
- `$TWICC create-session` — create a new session instead. Skill: `twicc-create-session`.
- `$TWICC topology self` — discover sibling and descendant session ids. Skill: `twicc-topology`.
- `$TWICC update-session <session_id> settings` — change agent settings before sending. Skill: `twicc-update-session`.
- `$TWICC process <session_id> stop` — stop the live agent. Skill: `twicc-process`.
- `$TWICC processes --state awaiting_user_input` — find sessions blocked on user input. Skill: `twicc-processes`.
- `$TWICC session <session_id>` — full session metadata. Skill: `twicc-session`.
- `$TWICC sessions --project <PROJECT>` — find session ids. Skill: `twicc-sessions`.

## How to present results

1. On success, give a clickable link: `[link text](/project/{project_id}/session/{session_id})`.
2. On validation error, summarize the failing fields with codes.
3. On `awaiting_user_input` (exit 3), tell the user they must click in the TwiCC UI first — a CLI message cannot unblock the pending dialog.
