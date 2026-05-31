---
name: twicc-whoami
description: Return the details of the session that owns the calling process. Use to discover your own TwiCC session_id from inside a Bash tool, when you need to reference your own session.
---

# TwiCC Whoami

Identify the TwiCC session you are running in. Returns the same output as `$TWICC session <ID>`. Exits 1 if not running inside a TwiCC agent.

## When to use

- You need your own `session_id` and don't already have it in context.

Note: to filter listings to sessions you spawned, prefer `--spawned-by self` on `$TWICC sessions`, `$TWICC processes`, and `$TWICC search` — no need to call `whoami` first.

## How to invoke

TwiCC's executable varies by launch mode (uvx, dev, installed tool). ALWAYS USE THIS TO RESOLVE $TWICC AT THE START OF EACH BASH INVOCATION:

```bash
TWICC=${TWICC_BIN:-$(command -v twicc 2>/dev/null)}
[ -n "$TWICC" ] || { echo "TwiCC executable not found in this context" >&2; exit 1; }
```

Then run `$TWICC <args>` — **never quote `$TWICC`** (use `$TWICC args`, never `"$TWICC" args`): it may expand to multiple words, which quoting would break.

## Usage

```bash
$TWICC whoami           # human-readable (indented JSON)
$TWICC whoami --json    # compact JSON, suitable for parsing
```

### Exit codes

- `0` — Session resolved.
- `1` — No session found in PID ancestry.

## Examples

```bash
MY_SESSION_ID=$($TWICC whoami --json | jq -r .id)
```

## Related commands

- `$TWICC sessions --spawned-by self` — list sessions you spawned. Skill: `twicc-sessions`.
- `$TWICC processes --spawned-by self` — list running processes you spawned. Skill: `twicc-processes`.
- `$TWICC search '<query>' --spawned-by self` — search within sessions you spawned. Skill: `twicc-search`.
