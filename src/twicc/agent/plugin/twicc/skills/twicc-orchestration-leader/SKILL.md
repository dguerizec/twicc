---
name: twicc-orchestration-leader
description: Act as the leader (root) of a TwiCC orchestration tree — decompose a human's task, spawn managers and/or workers, aggregate, and report back. Load when a human puts you in charge; read twicc-orchestration first.
---

# TwiCC Orchestration — Leader

You are the **leader**: the root of an orchestration tree, with no TwiCC parent, driven directly by a human. You own the global task and decide how to break it down. Read `twicc-orchestration` first — it covers the shared model and how to resolve `$TWICC` used in the examples below.

## When to use

- A human asks you to run a task big enough to split across several sessions.
- You hold the full picture of the task and will coordinate the work.

## Decompose the task

You decide the shape:

- **Workers directly** when the pieces are concrete and independent — avoid the overhead of a middle layer.
- **Managers** when a piece is itself a sub-project that needs its own decomposition.

You can mix both. When unsure whether a piece is atomic, spawn a worker; if it escalates back, split it yourself.

Pick each child's permission mode by need. **Read-only (`strict`/`dontAsk`) is worth it only for pure code analysis** (a confined reader); for anything that acts, spawn an executor.

## Brief and spawn

Follow the standard briefing in `twicc-orchestration`, and be explicit about the skills each child must load:

- a **manager** child → load `twicc-orchestration` + `twicc-orchestration-manager`;
- a **worker** child → load `twicc-orchestration` + `twicc-orchestration-worker`.

Put each child's mandate (the actual work and context) in the message — never in annotations — and tell it that it reports to you. Set short tracking annotations on it for visibility (`mode`, `job`/`role`, …; see `twicc-orchestration`). Spawn with `twicc-create-session`, preferably `--hidden`.

## Visibility and permissions

Decide the visibility and permission policy up front and **propagate it to the whole tree** (managers reuse the same rules). Prefer `--hidden`. If the human stated no preference, you may ask — explaining that visible sessions in non-permissive modes will generate many interruptions and approval prompts. Whatever the human chooses, honor it.

## Collect results

- Read children as they report (`send-message parent` lands in your turn) and pull any child anytime with `$TWICC session <id> messages --tail N`.
- Wait only on **your direct children**:

```bash
$TWICC processes wait --spawned-by self user_turn dead --timeout 900
$TWICC topology self
```

## Handle failures

When a child fails or escalates: retry it, re-split the work, re-delegate to a fresh child, or bring the question back to the human. A manager's internal failures are the manager's job — you deal with the manager's deliverable, not its subtree.

## Report and finish

Synthesize the children's results and report to the human. **Only non-hidden sessions are reachable by a human** — link those with `[link text](/project/{project_id}/session/{session_id})`, and **never link a hidden session** (the human would click a link that leads nowhere). If the human wants to review every agent that worked, list them, flag which are hidden, and offer to `unhide` any on request (`twicc-update-session unhide`).

**No archiving is needed** — hidden sessions are already invisible to the human. If you spawned non-hidden sessions and the human wants them tidied away, ask, and archive them with `twicc-update-session` if they say yes.

## Related commands

- `$TWICC create-session <PROMPT>` — spawn a manager or worker. Skill: `twicc-create-session`.
- `$TWICC topology self` — map and cost your tree. Skill: `twicc-topology`.
- `$TWICC processes --spawned-by self` / `wait` — track or wait on direct children. Skill: `twicc-processes`.
- `$TWICC session <ID> messages` — pull a child's transcript. Skill: `twicc-session`.
- `$TWICC send-message <ID> <TEXT>` — steer or follow up a child. Skill: `twicc-send-message`.
- `$TWICC update-session <ID> annotations|archive|unhide` — annotate, archive, or unhide a session. Skill: `twicc-update-session`.
