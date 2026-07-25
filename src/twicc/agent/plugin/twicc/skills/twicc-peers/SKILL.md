---
name: twicc-peers
description: List peer TwiCC instances the user has approved for cross-instance messaging. Use before peer-send to resolve a peer's id or exact name.
---

# TwiCC Peers

List peer instances — other TwiCC installations the user has paired with (a human-managed friend-request flow; agents cannot add, accept, or revoke peers). Output includes `active` peers (messageable) and `broken` ones (revoked or unreachable — listed so a failing send can be explained instead of "peer unknown").

## When to use

- You or the user want to send a message to another TwiCC instance and need the peer's id or exact name.
- A `peer-send` failed and you want to check the relationship's state.

## How to invoke

**Prefer the `mcp__twicc__*` tools when you have them.** Inside a TwiCC session your tool list may include `mcp__twicc__*` tools — one per command below (the command with `/` and `-` turned into `_`, e.g. `mcp__twicc__create_session`, `mcp__twicc__update_session_settings`). When present, use them instead of the `$TWICC` CLI: same arguments, same JSON result, no shell, and your session identity travels with the call so `self`/`parent` resolve on their own. Fall back to the `$TWICC` CLI below when those tools aren't available (outside a session, or when scripting from a terminal).

TwiCC's executable varies by launch mode (uvx, dev, installed tool). ALWAYS USE THIS TO RESOLVE $TWICC AT THE START OF EACH BASH INVOCATION:

```bash
TWICC=${TWICC_BIN:-$(command -v twicc 2>/dev/null)}
[ -n "$TWICC" ] || { echo "TwiCC executable not found in this context" >&2; exit 1; }
```

Then run `$TWICC <args>` — **never quote `$TWICC`** (use `$TWICC args`, never `"$TWICC" args`): it may expand to multiple words, which quoting would break.

## Usage

```bash
$TWICC peers
```

No arguments or options.

## Output format

```json
{"peers": [
  {"id": "peer_a1b2c3d4", "name": "David", "state": "active", "last_contact_at": "2026-07-24T12:00:00+00:00"},
  {"id": "peer_e5f6a7b8", "name": "Old laptop", "state": "broken", "last_contact_at": null}
]}
```

- `state` — `active` (messageable) or `broken` (the peer revoked the relationship or is unreachable; sends will fail until the user fixes it in Settings › Peers).

### Exit codes

- `0` — Success
- `64` — Bad CLI usage

## Examples

```bash
$TWICC peers
# → {"peers":[{"id":"peer_a1b2c3d4","name":"David","state":"active","last_contact_at":"..."}]}
```

## Related commands

- `$TWICC peer-send <peer> '<text>'` — send a message to a peer instance. Skill: `twicc-peer-send`.
- `$TWICC peer-message <message_id>` — re-check an outbound message's status. Skill: `twicc-peer-message`.

## How to present results

1. Refer to peers by their `name`; keep the `id` for follow-up commands.
2. If the peer the user meant is `broken`, say so and point them to Settings › Peers — you cannot repair the relationship yourself.
