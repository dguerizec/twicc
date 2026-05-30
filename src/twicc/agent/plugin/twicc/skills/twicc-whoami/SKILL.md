---
name: twicc-whoami
description: Return the details of the session that owns the calling process. Use to discover your own TwiCC session_id from inside a Bash tool, when you need to reference your own session (e.g. for related-command filtering).
---

# twicc-whoami

Identify the TwiCC session **you** are running in. Useful when an agent needs
its own `session_id` but does not already have it in context.

## Mechanism

`twicc whoami` walks the PID chain from the current process up to PID 1 and
looks for a match against the `agent_pid` of live sessions tracked by TwiCC.
On a match, it prints the same details `twicc session <ID>` would print — id,
provider, title, project_id, costs, settings, lifecycle, etc.

If no match is found (you ran it from a plain shell, not from an agent's Bash
tool), the command exits 1 with a clear message.

## Invocation

TwiCC's executable varies by launch mode (uvx, dev, installed tool). Resolve it once at the start of each Bash invocation:

```bash
TWICC=${TWICC_BIN:-$(command -v twicc 2>/dev/null)}
[ -n "$TWICC" ] || { echo "TwiCC executable not found in this context" >&2; exit 1; }
```

Then run any subcommand via `$TWICC <args>` — **do NOT quote `$TWICC`** (use `$TWICC args`, never `"$TWICC" args`). The variable may expand to a multi-word command (e.g. `uv run --directory ... run.py`); Bash relies on word-splitting to parse it, and quoting it would treat the entire expansion as a single program name and fail with "No such file or directory".

```bash
$TWICC whoami           # human-readable (indented JSON)
$TWICC whoami --json    # compact JSON, suitable for parsing
```

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Session resolved; details printed on stdout |
| 1 | No session in the PID ancestry (also: invoked from a plain shell) |

## Typical use

```bash
# I'm an agent; what's my TwiCC session_id?
MY_SESSION_ID=$($TWICC whoami --json | jq -r .id)
```

## Related commands

To list or filter the sessions YOU created, prefer the dedicated `--spawned-by self`
flag on `twicc sessions`, `twicc processes`, and `twicc search` — no need to call
`whoami` first; the flag resolves the current session under the hood.
