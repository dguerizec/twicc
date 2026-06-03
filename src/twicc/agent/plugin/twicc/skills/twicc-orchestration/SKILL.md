---
name: twicc-orchestration
description: Shared model and conventions for multi-session orchestration in TwiCC — the leader/manager/worker tree, briefing and talking to children, visibility and permission propagation. Load this first, then the skill for your own mode.
---

# TwiCC Orchestration

A TwiCC session can spawn other sessions, which can spawn their own, forming a **spawn tree**. This skill is the shared playbook for every node in such a tree. Load it first, then load the skill for your **mode** (`twicc-orchestration-leader`, `-manager`, or `-worker`).

## When to use

- You or the user want to break a task into several cooperating sessions.
- You were spawned as part of an orchestration and need the shared conventions.
- You are about to spawn a child and need to know how to brief it.

## Resolving `$TWICC`

The examples here and in the role skills (`-leader`, `-manager`, `-worker`) call TwiCC's CLI as `$TWICC`. Its executable varies by launch mode (uvx, dev, installed tool), so resolve it at the start of each bash invocation:

```bash
TWICC=${TWICC_BIN:-$(command -v twicc 2>/dev/null)}
[ -n "$TWICC" ] || { echo "TwiCC executable not found in this context" >&2; exit 1; }
```

Then run `$TWICC <args>` — **never quote `$TWICC`** (use `$TWICC args`, never `"$TWICC" args`): it may expand to multiple words, which quoting would break. Every command skill (`twicc-create-session`, `twicc-send-message`, …) repeats this block, so you will see it again when you load one.

## The model

Three **modes**, defined by a node's position in the tree:

- **leader** — the root. No TwiCC parent; driven by a human. Owns the global task, decides the decomposition, reports to the human. Skill: `twicc-orchestration-leader`.
- **manager** — an internal node with a parent and children. Receives a mandate, decomposes it further, aggregates, reports up. Skill: `twicc-orchestration-manager`.
- **worker** — a leaf. Executes one concrete task and delivers. Skill: `twicc-orchestration-worker`.

Who creates whom:

- A leader creates managers and/or workers.
- A manager creates managers and/or workers.
- A worker is a leaf.

A node never changes mode after birth. If a worker turns out to need help, it does not become a manager — it tells its parent.

## Mode vs job

**Mode** (leader/manager/worker) is the orchestration position — a level above the work itself, deciding which skill the session loads. It is orthogonal to the session's **job** (or **role**): the functional hat it wears in the work (project lead, reviewer, designer, backend dev, …). When a parent spawns a child it picks both: the mode (which skill to load) and the job (what the child is there to do).

## Briefing a child

A spawned session starts with **no memory of you** — every prompt must be self-contained. When you create a child (`twicc-create-session`), put in its prompt:

- **its mode** — tell it explicitly which skills to load: `twicc-orchestration` plus `twicc-orchestration-manager` or `twicc-orchestration-worker`.
- **its job and mandate** — what it must do. The work and its context go **in the message**, never in annotations.
- **how to report back** — and that you are its parent (it reaches you with `send-message parent`).

## Talking between sessions

- **Push** — an executor child reports up with `send-message parent` (the `parent` keyword resolves to its spawner). Skill: `twicc-send-message`.
- **Pull** — a parent can read any child's messages at any time with `$TWICC session <child_id> messages --tail N`, whether or not the child can push. The two coexist: you can look in on a child without waiting for its report.
- A **read-only** child (see below) cannot push — pull is the only way to read it.
- Siblings never talk directly: route through the common parent.

## Permission modes — use only the two extremes

Every session knows its own `permission_mode` from its injected context. For orchestration, use only the two **non-interactive** extremes of each provider — one that allows everything, one that allows nothing:

- **Executor — allows everything** (Claude Code `bypassPermissions`, Codex `yolo`): can act, write, spawn children, and push to its parent.
- **Read-only — allows only reading** (Claude Code `dontAsk`, Codex `strict`): pure read/analysis of the given project; cannot run commands, so cannot spawn, `send-message`, or write. Always a terminal leaf, read only by pull. Worth using only for pure code/content analysis.

Avoid the interactive modes: they pause for per-tool approvals or questions, and a spawned session waiting on a human dialog is one you cannot reliably unblock or steer from a parent. Choose a child's mode by what it needs to do — a manager must be an executor (a read-only manager could neither create children nor report).

## Wait only on your direct children

Only ever wait on the children **you** spawned — never on grandchildren. Each level pilots its own children; a manager's subtree is the manager's responsibility, not yours.

```bash
$TWICC processes wait --spawned-by self user_turn dead --timeout 600
$TWICC topology self
```

## Visibility and permission propagation

- **Strongly prefer `--hidden`** for the sessions you spawn (especially if you are hidden yourself): no UI clutter, and a hidden session can never get stuck on a UI dialog.
- **The human's choice wins and propagates.** If the human asks for visible (non-hidden) sessions, or for a specific permission level, honor it **and pass the same rules to every child** — managers spawn with the same rules.
- If the human gave no instruction, the leader may ask, noting the trade-off: visible sessions in non-permissive modes will produce many interruptions and approval prompts.

## Annotations

Annotations are short key/value tags on a session (free-form JSON). They are **strongly recommended** in an orchestration: they turn a tree of opaque sessions into a legible map.

What they buy you:

- **An overview at a glance.** `topology self` and `sessions --spawn-tree self` carry each node's annotations, so you — and the human, on request — see who does what and where it stands without reading any transcript.
- **Filtering and waiting by predicate.** `sessions`, `processes`, `search`, and `topology` accept `--annotation KEY=VALUE` (also `!=`, `:exists`, `:in:a,b`) — e.g. `processes --spawned-by self --annotation status=blocked`, or `sessions --spawn-tree self --annotation mode=worker`.

Useful keys (free — conventions, not rules):

- `mode` — `leader` / `manager` / `worker`, to filter the tree by position.
- `job` / `role` — the functional hat in the work (reviewer, backend-dev, designer, …).
- `status` — `working` / `done` / `failed` / `blocked`, kept current as work progresses.
- `task_id` — ties the session to a unit of work you track.

Who sets them:

- A **parent** tags a child at spawn (`create-session --annotation mode=worker --annotation job=reviewer`).
- A session updates **its own** tags as it goes (`update-session self annotations set:status=done`) — but a read-only session can't run commands, so it keeps whatever the parent gave it.

Keep values **short and single-line** — annotations are metadata, not a message channel. Anything long goes in the message or a scratch file.

## Lifecycle

A session goes `starting → assistant_turn → user_turn`, then `dead` when its process stops. A `dead` session is **resurrected automatically** when it receives a `send-message` — so you never need to keep a child alive; message it whenever you need it.

## Freedom

This is the unconstrained model: nothing here is enforced except the technical limits of read-only mode. The conventions above are what keep an orchestration legible — follow them, but the system will not stop you from doing otherwise.

## Related commands

- `$TWICC create-session <PROMPT>` — spawn a child. Skill: `twicc-create-session`.
- `$TWICC send-message <ID|parent> <TEXT>` — push to a session or to your parent. Skill: `twicc-send-message`.
- `$TWICC session <ID> messages` — pull a child's transcript. Skill: `twicc-session`.
- `$TWICC topology self` — map your spawn tree. Skill: `twicc-topology`.
- `$TWICC processes --spawned-by self` / `wait` — track or wait on your direct children. Skill: `twicc-processes`.
- `$TWICC update-session <ID> annotations` — set tracking annotations. Skill: `twicc-update-session`.
- `$TWICC whoami` — your own session id, settings, and permission mode. Skill: `twicc-whoami`.
