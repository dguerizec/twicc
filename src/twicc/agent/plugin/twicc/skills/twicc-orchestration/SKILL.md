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

- **leader** — the root. No TwiCC parent; driven by a user. Owns the global task, decides the decomposition, reports to the user. Skill: `twicc-orchestration-leader`.
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

Avoid the interactive modes: they pause for per-tool approvals or questions, and a spawned session waiting on a user dialog is one you cannot reliably unblock or steer from a parent. Choose a child's mode by what it needs to do — a manager must be an executor (a read-only manager could neither create children nor report).

## Wait only on your direct children

Only ever wait on the children **you** spawned in normal synchronization — never on grandchildren. Each level pilots its own children; a manager's subtree is the manager's responsibility, not yours. Use `--descendants` only for exceptional cleanup of a subtree you intentionally abort.

```bash
$TWICC processes wait --spawned-by self user_turn dead --timeout 600
$TWICC topology self
```

## Visibility and permission propagation

- **Strongly prefer `--hidden`** for the sessions you spawn (especially if you are hidden yourself): no UI clutter, and a hidden session can never get stuck on a UI dialog.
- **The user's choice wins and propagates.** If the user asks for visible (non-hidden) sessions, or for a specific permission level, honor it **and pass the same rules to every child** — managers spawn with the same rules.
- If the user gave no instruction, the leader may ask, noting the trade-off: visible sessions in non-permissive modes will produce many interruptions and approval prompts.

## Annotations

Annotations are short key/value tags on a session (free-form JSON). They are **strongly recommended** in an orchestration: they turn a tree of opaque sessions into a legible map.

What they buy you:

- **An overview at a glance.** `topology self` and `sessions --spawn-tree self` carry each node's annotations, so you — and the user, on request — see who does what and where it stands without reading any transcript. The user reads the same map visually: any session in the tree has a read-only **Orchestration** tab in the UI (titles, status, cost, annotations, timing) — point the user there to follow progress (its URL is a session's URL with `/orchestration` appended, e.g. `/project/<project_id>/session/<session_id>/orchestration`).
- **Filtering and waiting by predicate.** `sessions`, `processes`, `search`, and `topology` accept `--annotation KEY=VALUE` (also `!=`, `:exists`, `:in:a,b`). For live processes, annotation is an extra filter on a filiation scope: use `processes --spawned-by self --annotation status=blocked` for direct children, or `processes wait --spawned-by self --annotation job=review user_turn dead --timeout 600` for a scoped barrier.

Useful keys (free — conventions, not rules):

- `mode` — `leader` / `manager` / `worker`, to filter the tree by position.
- `job` / `role` — the functional hat in the work (reviewer, backend-dev, designer, …).
- `status` — `working` / `done` / `failed` / `blocked`, kept current as work progresses.
- `task_id` — ties the session to a unit of work you track.

Who sets them:

- A **parent** tags a child at spawn (`create-session --annotation mode=worker --annotation job=reviewer`).
- A session updates **its own** tags as it goes (`update-session self annotations set:status=done`) — but a read-only session can't run commands, so it keeps whatever the parent gave it.

Keep values **short and single-line** — annotations are metadata, not a message channel. Anything long goes in the message or a scratch file.

## Scratch files: private and shared

Your context block gives a `scratch_base_dir`. Two uses:

- **Your own scratch** — for your private working files, use `<scratch_base_dir>/<your_session_id>/`. It is yours alone, so no filename prefix is needed.
- **Shared scratch** — to exchange files with the other sessions of your tree, use the directory given by the **`scratch_dir` annotation**. Messages and annotations must stay short, so a shared folder is the right channel for bulky output (a large diff, a generated file, a long report).

How the shared scratch works:

- The leader picks a folder (typically `<scratch_base_dir>/<leader_session_id>/`) and passes its absolute path to each child as the `scratch_dir` annotation. A child that spawns children **propagates the same `scratch_dir`**, so the whole subtree converges on one folder. A `scratch_dir` annotation **takes precedence** over your own `scratch_base_dir`.
- **Create on demand**: the first agent that needs it runs `mkdir -p <scratch_dir>` (idempotent) — nobody creates it up front.
- **Prefix every file with your own session id** (`<session_id>-report.md`) so two agents never clobber the same file.
- **Executors only**: a read-only session can neither write nor read the scratch space; it keeps its result in its reply (read by pull).

Pattern: an executor (worker or manager) writes `<scratch_dir>/<session_id>-result.md`, then sends a short `send-message parent` ("done, see `<session_id>-result.md`"); the parent reads the file.

## Lifecycle

A session goes `starting → assistant_turn → user_turn`, then `dead` when its process stops. A `dead` session is **resurrected automatically** when it receives a `send-message` — so you never need to keep a child alive; message it whenever you need it.

## Process control

Use process controls as scoped operations:

- List your direct children's live work with `$TWICC processes --spawned-by self`, or narrow it with `$TWICC processes --spawned-by self --annotation status=blocked`.
- Wait on direct children with `$TWICC processes wait --spawned-by self user_turn dead --timeout <N>`, optionally narrowed by `--annotation`.
- Stop selected children with `$TWICC processes stop --spawned-by self --annotation status=cancelled --timeout <N>`, or pass explicit ids when you know the exact targets.
- Abort a subtree deliberately with `$TWICC processes stop <manager_id> --descendants <manager_id> --timeout <N>`; `--descendants` excludes the target, so pass the manager id explicitly too.

`processes wait` and `processes stop` do not accept `--spawn-tree` or `parent`.

For exact command recipes (barriers, scoped waits, races, batch stops, subtree aborts), read `control-cookbook.md` only when you need to operate live processes.

## Freedom

This is the unconstrained model: nothing here is enforced except the technical limits of read-only mode. The conventions above are what keep an orchestration legible — follow them, but the system will not stop you from doing otherwise.

## Orchestration patterns (for leaders and managers)

You compose a structure from five axes — topology, channel, synchronization, aggregation, voice diversity. The recurring useful combinations are written up as bundled pattern files next to this skill, under `patterns/`. Read `patterns/composing.md` first (the five axes), then open the one that fits — you don't need them all:

- Distribute — `patterns/scatter-gather.md`, `patterns/multi-angle.md`, `patterns/divide-and-conquer.md`
- Gate phases — `patterns/phase-gated-fanout.md`
- Chain — `patterns/pipeline.md`, `patterns/plan-then-execute.md`
- Decide / verify — `patterns/quorum.md`, `patterns/debate.md`, `patterns/produce-refute.md`
- Watch & steer — `patterns/supervisor.md`
- Survive failure / scale — `patterns/speculative-race.md`, `patterns/worker-pool.md`
- Integrate safely — `patterns/single-writer-integration.md`
- Stay within context — `patterns/context-offload.md`

For worked, end-to-end walkthroughs that combine these patterns on a real task, see `examples/` — `pr-review`, `codebase-audit`, `architecture-decision`, `research-synthesis`, `ship-feature`, `parallel-feature-integration`, `content-pipeline`, `go-no-go-quorum`, `long-running-watchdog`, `large-migration`, `hard-bug-race`, `context-relay`. Each pattern file also links the examples that use it.

Workers don't orchestrate, so they can ignore this.

## Related commands

- `$TWICC create-session <PROMPT>` — spawn a child. Skill: `twicc-create-session`.
- `$TWICC send-message <ID|parent> <TEXT>` — push to a session or to your parent. Skill: `twicc-send-message`.
- `$TWICC session <ID> messages` — pull a child's transcript. Skill: `twicc-session`.
- `$TWICC topology self` — map your spawn tree. Skill: `twicc-topology`.
- `$TWICC processes --spawned-by self` — track your direct children. Skill: `twicc-processes`.
- `$TWICC processes wait --spawned-by self ...` / `processes stop --spawned-by self ...` — wait on or stop scoped child batches. Skill: `twicc-processes`.
- `$TWICC update-session <ID> annotations` — set tracking annotations. Skill: `twicc-update-session`.
- `$TWICC whoami` — your own session id, settings, and permission mode. Skill: `twicc-whoami`.
