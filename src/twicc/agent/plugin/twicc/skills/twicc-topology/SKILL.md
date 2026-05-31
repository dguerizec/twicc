---
name: twicc-topology
description: Show the spawned-session tree around a session, rooted at its top-level ancestor, with session metadata, process state, and aggregate child/cost data.
argument-hint: <session_id|self> [--no-processes]
---

# TwiCC Topology

Show the spawned-session tree containing a session. The tree follows `spawned_by` links only — provider-internal subagents (`parent_session_id`) are intentionally out of scope.

## When to use

- You or the user want to see the agent hierarchy around a session.
- You need to discover siblings, children, or descendants before sending direct messages.
- You want process state and aggregate child/cost data for every session in an orchestration tree.

## How to invoke

TwiCC's executable varies by launch mode (uvx, dev, installed tool). ALWAYS USE THIS TO RESOLVE $TWICC AT THE START OF EACH BASH INVOCATION:

```bash
TWICC=${TWICC_BIN:-$(command -v twicc 2>/dev/null)}
[ -n "$TWICC" ] || { echo "TwiCC executable not found in this context" >&2; exit 1; }
```

Then run `$TWICC <args>` — **never quote `$TWICC`** (use `$TWICC args`, never `"$TWICC" args`): it may expand to multiple words, which quoting would break.

## Usage

```bash
$TWICC topology <SESSION_ID|self> [OPTIONS]
```

### Arguments

- `SESSION_ID` — any regular session in the spawned tree.
- `self` — resolve the current TwiCC session from PID ancestry.

### Options

- `--processes / --no-processes` — include compact live process state when a TwiCC backend is running. Defaults to `--processes`; if no backend is running, topology is still returned with process data marked unavailable.

## Output format

```json
{
  "seed_session_id": "B",
  "root_session_id": "A",
  "path_to_seed": ["A", "B"],
  "cycle_detected": false,
  "processes": {"requested": true, "available": true, "reason": null},
  "tree": {"id": "A", "children": [{"id": "B", "children": []}]},
  "nodes": [
    {
      "id": "A",
      "session": {
        "id": "A",
        "project_id": "-home-twidi-dev-myproject",
        "provider": "codex",
        "title": "Root orchestrator",
        "total_cost": 1.23,
        "spawned_by": null
      },
      "process": {"id": 42, "state": "user_turn", "started_at": "...", "last_state_change_at": "...", "pid": 12345},
      "direct_child_count": 1,
      "descendant_count": 1,
      "subtree_total_cost": 2.34
    }
  ]
}
```

- `seed_session_id` — session id passed as input, after resolving `self`.
- `root_session_id` — top-level ancestor id for this spawned-session tree.
- `path_to_seed` — root-to-seed chain.
- `tree` — nested id-only tree for traversal.
- `nodes` — node data in tree pre-order; the root is first.
- `nodes[].id` — same value as `nodes[].session.id`, exposed for direct indexing.
- `nodes[].session` — full session metadata, same shape as `$TWICC session <ID>`.
- `nodes[].direct_child_count` — immediate spawned children.
- `nodes[].descendant_count` — spawned descendants across all levels.
- `nodes[].subtree_total_cost` — sum of `session.total_cost` for this node and all descendants, or `null` when none has cost.
- `process.state` — `starting`, `assistant_turn`, `awaiting_user_input`, `user_turn`, or `dead`; `process` is `null` when process data is unavailable or not requested.
- `cycle_detected` — defensive flag for corrupt `spawned_by` data.

### Exit codes

- `0` — Success
- `1` — Session not found, `self` could not resolve, or the target is a provider-internal subagent
- `64` — Bad CLI usage

## Examples

```bash
$TWICC topology self
$TWICC topology 4a8352fb-1674-41c0-8a85-0a5a3e4e623a
$TWICC topology self --no-processes
```

## Related commands

- `$TWICC whoami` — identify the current session. Skill: `twicc-whoami`.
- `$TWICC sessions --spawned-by <ID|self>` — list direct children. Skill: `twicc-sessions`.
- `$TWICC processes --spawned-by <ID|self>` — list live child processes. Skill: `twicc-processes`.
- `$TWICC send-message <SESSION_ID>` — message a discovered session. Skill: `twicc-send-message`.
- `$TWICC session <SESSION_ID>` — inspect full metadata for one node. Skill: `twicc-session`.

## How to present results

1. Lead with the root title/id, the target path, and the number of nodes.
2. Render the tree by title when available, falling back to session id.
3. Call out `awaiting_user_input` and long-running `assistant_turn` nodes first.
4. Mention hidden or archived nodes only when they matter to the user's question.
5. You are in TwiCC — link to a session: `[link text](/project/{project_id}/session/{session_id})`.
