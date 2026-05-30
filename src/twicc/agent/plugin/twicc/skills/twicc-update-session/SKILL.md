---
name: twicc-update-session
description: Update an existing TwiCC session. Today the `settings` sub-command updates the agent settings (model, effort, permission mode, thinking, chrome MCP, fast mode, context window, question widget) and the `title` sub-command renames the session; future sub-commands will cover archive, pin, and stop. Use when the user wants to change a session's settings or title without sending a new message.
argument-hint: <session_id> {settings|title} [ARGS / OPTIONS]
---

# TwiCC Update Session

Update an existing agent session by dropping a request file the live TwiCC server picks up. Two sub-commands today: `settings` (change the agent settings) and `title` (rename the session). `archive`, `pin`, and `stop` will plug into the same sub-app later.

## When to use

- The user asks to "change the model / effort / permission mode / etc. on session X" → `settings`.
- A script needs to re-tune a session's settings between turns (e.g. drop to `low` effort for a quick follow-up, then bump back up) → `settings`.
- The user wants to reset a setting back to the synced default (use `--unset <field>`) → `settings`.
- The user asks to "rename / re-title session X" or wants a script to set a better title after a few turns of conversation → `title`.

**Out of scope (today):** archiving / unarchiving, pinning / unpinning, and killing the live process. These will become other sub-commands of `twicc update-session` — refuse if asked here and tell the user the relevant operation isn't wired yet (the UI can still do it).

## How to invoke

TwiCC's executable varies by launch mode (uvx, dev, installed tool). Resolve it once at the start of each Bash invocation:

```bash
TWICC=${TWICC_BIN:-$(command -v twicc 2>/dev/null)}
[ -n "$TWICC" ] || { echo "TwiCC executable not found in this context" >&2; exit 1; }
```

Then run any subcommand via `$TWICC <args>` — **do NOT quote `$TWICC`** (use `$TWICC args`, never `"$TWICC" args`). The variable may expand to a multi-word command (e.g. `uv run --directory ... run.py`); Bash relies on word-splitting to parse it, and quoting it would treat the entire expansion as a single program name and fail with "No such file or directory". All bash examples below use the unquoted form.

## Prerequisite: the server must be running

The command communicates with the live TwiCC server through the data directory (no network, no auth). It checks `<data_dir>/twicc.heartbeat` first and fails fast (exit code 2) if it's missing or stale (> 15 s old). If the user gets that error, ask them to start the server (`twicc` in another terminal) and retry.

## How to update settings

Basic shape:

```bash
$TWICC update-session '<SESSION_ID>' settings [OPTIONS]
```

### Required argument

- **`SESSION_ID`** — id of the existing session to update. You can get one from `twicc sessions` (browse) or directly from the UI URL.

### Patch mode (no `--preset`)

Without `--preset`, only the fields you explicitly touch get written. Every other field keeps its current value in DB.

- Pass a per-field flag (`--model opus`, `--effort high`, `--no-thinking`, ...) to set a new value.
- Pass `--unset <field>` to reset a field to `NULL` (= use the synced default). Repeatable.
- At least one of: per-field flag, `--unset`. Otherwise: `no_op` validation error.

### Replace mode (`--preset NAME`)

With `--preset`, every settings field is rewritten:

- The preset's values write the fields it defines.
- Fields **absent from the preset become NULL** (= use the synced default).
- Per-flag options override the preset.
- `--unset <field>` forces a field to `NULL` even if the preset defined it.

### Options

| Flag | Claude Code                                                                                                  | Codex                                               |
|------|--------------------------------------------------------------------------------------------------------------|-----------------------------------------------------|
| `--model VALUE` | `opus`, `sonnet`, `opus-4.7`, `opus-4.6`, `opus-4.5`, `sonnet-4.5`                                           | `gpt`, `gpt-mini`, `gpt-5.4`                        |
| `--effort VALUE` | `low`, `medium`, `high`, `xhigh`, `max`                                                                      | `low`, `medium`, `high`, `xhigh`                    |
| `--permission-mode VALUE` | `default`, `acceptEdits`, `plan`, `dontAsk`, `bypassPermissions`                                             | `read_only`, `strict`, `auto`, `autonomous`, `yolo` |
| `--thinking / --no-thinking` | extended thinking on/off                                                                                     | not supported                                       |
| `--claude-in-chrome / --no-claude-in-chrome` | Chrome MCP integration on/off                                                                                | not supported                                       |
| `--fast-mode / --no-fast-mode` | fast mode on/off (only on Opus)                                                                              | not supported                                       |
| `--context-max VALUE` | `200k` or `1m`                                                                                               | `272k`                                              |
| `--question-widget / --no-question-widget` | interactive question widget on/off                                                                           | not supported                                       |
| `--unset TOKEN` (repeatable) | accepted tokens: `model`, `effort`, `permission-mode`, `thinking`, `claude-in-chrome`, `fast-mode`, `context-max`, `question-widget` | same set, but only the fields the provider supports |
| `--preset NAME` | name of a saved settings preset for the session's provider                                                   | same                                                |
| `--timeout SECONDS` | seconds to wait for the server's final status (default 30)                                                   | same                                                |
| `--json` | emit a single JSON object on stdout (implies `--no-color`)                                                   | same                                                |
| `--no-color` | disable ANSI colors                                                                                          | same                                                |

The exact model / effort lists can shift over time — `twicc update-session DUMMY settings --help` always reflects the current state (`DUMMY` here can be any string; the parser accepts the SESSION_ID argument before showing help).

### Rejections caught locally (exit 1)

- `unknown_unset_field` — `--unset foo` where `foo` is not in the accepted tokens.
- `unsupported_field` — `--<field> VALUE` or `--unset <field>` where the field is not supported by the session's provider (e.g. `--thinking` on Codex).
- `invalid_choice` — value out of the provider's allowed set (e.g. `--effort ultra`).
- `invalid_format` — `--context-max` malformed (expected `1m`, `200k`, etc.).
- `unset_conflict` — `--<field> VALUE` and `--unset <field>` passed together.
- `no_op` — nothing to update (no preset, no per-field flag, no `--unset`).
- `is_subagent` — the SESSION_ID points to a subagent. **Subagents cannot be updated directly**; target the parent session instead.
- `session_not_found` / `session_stale` / `project_no_directory` / `provider_disabled` — same vocabulary as the other CLI commands.

## How to update the title

Basic shape:

```bash
$TWICC update-session '<SESSION_ID>' title '<NEW_TITLE>'
```

### Required arguments

- **`SESSION_ID`** — id of the existing session to rename.
- **`NEW_TITLE`** — the new title. Trimmed before validation; must be non-empty and ≤ 200 characters (the provider may impose a stricter cap).

### Options

Only the standard output controls — title has no per-field options.

| Flag | |
|------|---|
| `--timeout SECONDS` | seconds to wait for the server's final status (default 30) |
| `--json` | emit a single JSON object on stdout (implies `--no-color`) |
| `--no-color` | disable ANSI colors |

### Rejections caught locally (exit 1)

- `invalid_title` — title empty (or whitespace only) after trim.
- `is_subagent` — the SESSION_ID points to a subagent. **Subagents cannot be renamed directly**; target the parent session instead.
- `session_not_found` / `session_stale` / `project_no_directory` / `provider_disabled` — same vocabulary as the other CLI commands.

### Rejections from the server (exit 3)

- `invalid_title` — the provider's `validate_title` rejected the trimmed value (typically: too long; default cap is 200 characters).

### What the rename does on the provider side

After the DB write, the server asks the provider to persist the new title into its backing store. The agent does not need to be restarted:

- **Claude Code** — appends a `custom-title` JSONL entry and registers the title as protected against any stale CLI re-appends.
- **Codex** — calls the SDK's `thread/name/set`.

Provider-side failures are non-critical and logged: the DB is already updated and every open UI client receives a `session_updated` broadcast with the new title regardless.

## Examples

```bash
# Patch: only change the model. Other settings unchanged.
$TWICC update-session 4a8352fb-1674-41c0-8a85-0a5a3e4e623a settings --model sonnet

# Patch: change effort + reset model to the synced default.
$TWICC update-session 4a8352fb-... settings --effort high --unset model

# Replace: apply a preset wholesale, then override effort.
$TWICC update-session 4a8352fb-... settings --preset 'deep think' --effort low

# Replace via preset + wipe a field the preset would have set.
$TWICC update-session 4a8352fb-... settings --preset 'deep think' --unset effort

# Machine-parseable output for scripts.
$TWICC update-session --json 4a8352fb-... settings --model opus
# → {"status":"updated","session_id":"...","provider":"claude_code","project_id":"...","request_uuid":"..."}

# Rename a session.
$TWICC update-session 4a8352fb-... title 'Better title that survives a long listing'

# Rename + JSON for scripts.
$TWICC update-session --json 4a8352fb-... title 'Renamed'
# → {"status":"updated","session_id":"...","provider":"claude_code","project_id":"...","request_uuid":"..."}
```

## Output format

### Text mode (default)

```
✓ Heartbeat OK (last seen 0.7s ago)
✓ Session '4a8352fb-...' resolved (provider: claude_code, project: -home-twidi-dev-myproj)
✓ Settings validated (replace_all=False, fields=['effort', 'selected_model'])
→ Request submitted (request_uuid: 4a8352fb...)
✓ Session updated: 4a8352fb-1674-41c0-8a85-0a5a3e4e623a
```

Validation errors (exit code 1) print as:

```
✗ Validation error:
  - --unset model: --unset model conflicts with --model 'opus'; pick one.
  - --thinking: thinking_enabled is not supported by codex. Supported: [...].
```

### JSON mode (`--json`)

A single JSON object, one of:

```json
{"status":"updated","session_id":"...","provider":"...","project_id":"...","request_uuid":"..."}
{"status":"validation_error","errors":[{"field":"--unset model","code":"unset_conflict","message":"..."}]}
{"status":"rejected","errors":[{"field":"...","code":"...","message":"..."}],"request_uuid":"..."}
{"status":"failed","error":"...","request_uuid":"..."}
{"status":"timeout","received_seen":true,"message":"...","request_uuid":"..."}
```

### Exit codes

| Code | Meaning |
|------|---------|
| 0 | Update applied (settings or title) |
| 1 | CLI validation error (bad flag, unknown unset token, unset conflict, no-op, invalid title, session lookup failed, etc.) |
| 2 | TwiCC server not running (heartbeat missing or stale) |
| 3 | Server rejected the request — see `errors[].code` (`invalid_title`, `provider_disabled`, `session_not_found`/`session_stale` race-detected, `manager_busy`, ...) |
| 4 | Server hit an unexpected error mid-flight |
| 5 | Timeout waiting for the server's final status |
| 64 | Bad CLI usage (handled by Typer) |

## How a settings change reaches the live agent

If a live agent is attached to the session at the time of a `settings` update, the new settings are also propagated to the manager (`send_to_session` with an empty text). The manager applies the change according to each field's category:

- **Live** (`permission_mode` on Claude Code): applied immediately, whatever state the agent is in.
- **Idle** (`selected_model`, `context_max` on Claude Code; `selected_model`, `effort`, `permission_mode`, `context_max` on Codex): applied during `USER_TURN`.
- **Startup** (`effort`, `thinking_enabled`, `claude_in_chrome`, `fast_mode`, `question_widget` on Claude Code): the agent is restarted with the new values. If the agent is currently `awaiting_user_input`, the pending dialog is lost — the user will have to re-run whatever action it was blocking.

If no live agent is attached, the DB is updated and the change applies on the next session resume.

## Following up

Same loop as `twicc-send-message` and `twicc-create-session`:

- **Is the live agent still busy / blocked?** → `twicc process <SESSION_ID>` (see the `twicc-process` skill).
- **Read the latest assistant reply** → `twicc session <SESSION_ID> messages --tail 1`.
- **Drop a follow-up** → `twicc send-message <SESSION_ID> '<TEXT>'`.

## Error handling

When the server rejects (exit 3), parse `errors[].code` from JSON mode:

- **`is_subagent`** → the session is a subagent. Subagents cannot be updated; target the parent. Fetch the parent via `twicc session <id>` if needed.
- **`invalid_title`** → only on the `title` sub-command. Title was empty after trim or exceeded `MAX_TITLE_LENGTH` (200 by default). Shorten / fix and retry.
- **`session_not_found`** / **`session_stale`** → race vs. the local pre-check (session deleted or JSONL removed between lookup and service). Retry once; if persistent, the session is really gone.
- **`provider_disabled`** → the session's provider was disabled in settings; tell the user to re-enable it from the UI.
- **`project_no_directory`** → the project lost its directory; the agent can't be re-started.
- **`manager_busy`** → transient — only relevant to the `settings` sub-command (when the live agent is busy applying the settings). Retry.

## Related commands

- **Create a new session instead:** `twicc create-session` — full options for picking provider, model, settings, project, etc.
- **Send a message (settings unchanged):** `twicc send-message <session_id> '<text>'`.
- **Check the live agent's state:** `twicc process <session_id>` — particularly useful right after a startup-setting change (`--effort`, `--thinking`, ...) since the agent will be restarted.
- **Read the latest reply:** `twicc session <session_id> messages --tail 1`.
- **Inspect the session:** `twicc session <session_id>` — full metadata.

## How to present results

1. On success, restate which fields were touched (you have them in the success output) and remind the user the change has been broadcast to any open UI client. Give a clickable link: `[link text](/project/{project_id}/session/{session_id})`.
2. On validation error, summarise the failing fields with codes — don't dump the raw output. For `unset_conflict` / `no_op` / `unsupported_field`, the message explains itself.
3. If the request was rejected (exit 3), follow the error handling table above.
4. Mention the live-agent propagation only when relevant (e.g. when a startup setting changed and the agent gets restarted) — the user often just wants confirmation that the DB is up to date.
