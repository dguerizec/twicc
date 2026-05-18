---
name: twicc-create-session
description: Create a new TwiCC session in a project, with a prompt and optional agent settings (provider, model, effort, permission mode, attachments, etc.). Use when the user wants to spawn a fresh Claude Code or Codex session, kick off a sub-task in another project, or scaffold conversations from a script.
argument-hint: <prompt>
---

# TwiCC Create Session

Spawn a new agent session by dropping a request file the live TwiCC server picks up. The session ends up as a regular row in TwiCC, same as if it had been started from the UI.

## When to use

- The user asks to "create / start / spawn a new session" in some project
- The user wants to kick off work on a separate project (or sub-task) without leaving the current one
- A script needs to programmatically queue work into TwiCC (use `--json` for machine-parseable output)
- The user wants to attach files (images, PDFs, text) to a fresh conversation

## How to invoke

TwiCC's executable varies by launch mode (uvx, dev, installed tool). Resolve it once at the start of each Bash invocation:

```bash
TWICC=${TWICC_BIN:-$(command -v twicc 2>/dev/null)}
[ -n "$TWICC" ] || { echo "TwiCC executable not found in this context" >&2; exit 1; }
```

Then run any subcommand via `$TWICC <args>` — **do NOT quote `$TWICC`** (use `$TWICC args`, never `"$TWICC" args`). The variable may expand to a multi-word command (e.g. `uv run --directory ... run.py`); Bash relies on word-splitting to parse it, and quoting it would treat the entire expansion as a single program name and fail with "No such file or directory". All bash examples below use the unquoted form.

## Prerequisite: the server must be running

The command communicates with the live TwiCC server through the data directory (no network, no auth). It checks `<data_dir>/.server-heartbeat` first and fails fast (exit code 2) if it's missing or stale (> 15 s old). If the user gets that error, ask them to start the server (`twicc` in another terminal) and retry.

## How to create a session

Basic shape:

```bash
$TWICC create-session [OPTIONS] '<PROMPT>'
```

### Required argument

- **`PROMPT`** — the first user message. Either inline text or an absolute/relative path to a UTF-8 file whose content will be used as the prompt.

### Common options

All options are optional. Omitted values fall back to the synced defaults.

- `--project ID-OR-PATH` — project id (with or without leading dash) or directory (absolute / relative). New directories are auto-created as projects after `realpath` resolution. Defaults to the current working directory.
- `--provider claude_code|codex` — provider to use. Falls back to the synced `defaultProvider` when omitted.
- `--preset NAME` — name of a saved agent-settings preset for the chosen provider. Per-flag options below override preset values; unset fields fall back to the synced defaults.
- `--title TEXT` — **you MUST always pass this.** A concise 5–7 word title — not necessarily a grammatical sentence, but specific enough that the user can find the session later in a long list. Derive it from the prompt; don't fall back on the auto-derived title (it's rarely as good).
- `--timeout SECONDS` — how long to wait for the server's final status (default 30). Independent of the session itself — even if the CLI times out, the session may still get created on the server.

### Agent settings (per provider)

Each value is optional — omit to let the preset / synced default apply.

| Flag | Claude Code                                                                                                  | Codex                                               |
|------|--------------------------------------------------------------------------------------------------------------|-----------------------------------------------------|
| `--model VALUE` | `opus`, `sonnet`, `opus-4.6`, `opus-4.5`, `sonnet-4.5`                                                       | `gpt`, `gpt-mini`, `gpt-5.4`                        |
| `--effort VALUE` | `low`, `medium`, `high`, `xhigh`, `max` (xhigh / max are silently demoted if the model doesn't support them) | `low`, `medium`, `high`, `xhigh`                    |
| `--permission-mode VALUE` | `default`, `acceptEdits`, `plan`, `dontAsk`, `bypassPermissions`                                             | `read_only`, `strict`, `auto`, `autonomous`, `yolo` |
| `--thinking / --no-thinking` | extended thinking on/off                                                                                     | not supported                                       |
| `--claude-in-chrome / --no-claude-in-chrome` | Chrome MCP integration on/off                                                                                | not supported                                       |
| `--context-max VALUE` | `200k` or `1m` (1m (million) silently capped to 200K on unsupported models)                                  | `272k`                                              |

The exact model list can shift over time — `twicc create-session --help` always reflects the current state. If a flag isn't supported by the chosen provider, the CLI rejects with an explicit `unsupported_field` validation error before sending anything.

### Attachments

- `--attach PATH` — repeatable. Each call adds one file.
- Accepted types per provider (sniffed by magic bytes, not extension):
  - **Claude Code**: PNG, JPEG, GIF, WebP, PDF, text/plain
  - **Codex**: images only (PNG, JPEG, GIF, WebP)
- Per-file cap: 5 MB. Per-batch cap: 100 files, 32 MB total.
- Images are auto-resized to the long-edge cap the provider/model accepts (Opus 4.7+ keeps 2576 px, older Claude models drop to 1568 px, >20 images further caps at 2000 px, Codex re-resizes server-side so we ship at 2576 px). Resize is no-op when the image is already within the cap. JPEG stays JPEG (quality 92); everything else becomes PNG (lossless).
- Resize errors abort the whole batch (no partial delivery).

### Output mode

- Pretty text by default — progress lines, then summary, then final result.
- `--json` emits a single JSON object on stdout instead (implies `--no-color`). Use this from scripts.
- `--no-color` disables ANSI colors. Always implied by `--json`.

## Examples

```bash
# Simplest: project = cwd, provider = synced default, inline prompt
$TWICC create-session 'Run the tests and fix the failing ones'

# Explicit project + provider
$TWICC create-session \
    --project /home/twidi/dev/myproj \
    --provider claude_code \
    'Add a /healthz endpoint'

# Prompt from a file, with a saved preset
$TWICC create-session \
    --project home-twidi-dev-myproj \
    --preset 'deep think' \
    /home/twidi/prompts/security-audit.md

# Preset + targeted override (preset wins for the other fields)
$TWICC create-session \
    --provider claude_code \
    --preset 'deep think' \
    --effort low \
    'Quick review of last commit'

# Attachments (Claude Code only for PDF/text)
$TWICC create-session \
    --provider claude_code \
    --attach /home/twidi/screenshot.png \
    --attach /home/twidi/report.pdf \
    'What do you think of these results?'

# Machine-parseable output for scripts
$TWICC create-session --json \
    --provider claude_code \
    'Hello'
# → {"status":"created","session_id":"...","provider":"claude_code","project_id":"...","request_uuid":"..."}
```

## Output format

### Text mode (default)

```
✓ Heartbeat OK (last seen 0.7s ago)
✓ Bootstrap loaded (2 providers, 3 presets total)
✓ Prompt resolved (40 chars)
✓ Project '-home-twidi-dev-myproj' (existing)
✓ Settings validated
✓ Attachments validated (1 images, 0 documents)
  • screenshot.png — image (image/png), resized 3000x2000 → 1568x1045, 22.0 KB → 7.1 KB
→ Request submitted (request_uuid: 4a8352fb...)
✓ Session created: 4a8352fb-1674-41c0-8a85-0a5a3e4e623a
```

Validation errors (exit code 1) print as:

```
✗ Validation error:
  - --effort: invalid value 'ultra' for claude_code. Expected: ['low', 'medium', 'high', 'xhigh', 'max'].
  - --attach /tmp/big.pdf: size 8.2 MB exceeds 5 MB limit
```

### JSON mode (`--json`)

A single JSON object, one of:

```json
{"status":"created","session_id":"...","provider":"...","project_id":"...","request_uuid":"..."}
{"status":"validation_error","errors":[{"field":"--effort","code":"invalid_choice","message":"..."}]}
{"status":"rejected","errors":[{"field":"...","code":"...","message":"..."}],"request_uuid":"..."}
{"status":"failed","error":"...","request_uuid":"..."}
{"status":"timeout","received_seen":true,"message":"...","request_uuid":"..."}
```

### Exit codes

| Code | Meaning |
|------|---------|
| 0 | Session created |
| 1 | CLI validation error (bad flag, bad attachment, prompt empty, etc.) |
| 2 | TwiCC server not running (heartbeat missing or stale) |
| 3 | Server rejected the request (provider disabled, project missing, etc. — see `errors[].code`) |
| 4 | Server hit an unexpected error mid-flight |
| 5 | Timeout waiting for the server's final status |
| 64 | Bad CLI usage (handled by Typer) |

## Following up

Creation returns immediately; the agent keeps working in the background. **Only if the user asks** for the reply, fetch it with:

```bash
$TWICC session <SESSION_ID> messages --tail 1
```

- `role: "assistant"` → that's the reply.
- `role: "user"` → no text reply yet (still working, or only tool calls so far) — wait and retry.

Use `--tail N` for more turns when needed.

## Related commands

- **Inspect the created session:** `twicc session <session_id>` — full metadata for the new session
- **Read the agent's reply (uniform shape):** `twicc session <session_id> messages --tail 1` — the latest user/assistant message (see "Following up" above)
- **Read raw session content:** `twicc session <session_id> content <line_or_range>` — see every item (tool calls, reasoning, system) in the provider's native JSONL shape
- **List sessions:** `twicc sessions --project <project_id>` — to see other recent sessions in the same project
- **Find a project id:** `twicc projects` — useful to resolve `--project` if you'd rather pass the canonical id

## How to present results

1. On success, give the user the title you set, the canonical session id, and (when available) a clickable link to the new session: `[link text](/project/{project_id}/session/{session_id})`.
2. On a validation error, summarise the failing fields and the expected values — don't just dump the raw output.
3. If the server is down (exit 2), tell the user to start TwiCC (`twicc` in another terminal) and offer to retry.
4. If the request was rejected (exit 3), parse `errors[].code` from JSON mode to give a precise diagnosis:
   - `provider_disabled` → suggest enabling the provider from the UI or settings.
   - `project_not_found` / `project_no_directory` → the `--project` value didn't resolve; re-check it.
   - `manager_busy` → another session for the same id is already live; retry with a fresh request.
5. Only mention resize info or per-file summaries if the user is debugging or explicitly asks.
