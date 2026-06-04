# Composing an orchestration

Patterns are not a fixed menu — they are points in a small space you compose
along five axes. Read this once; the named patterns in this folder are just
common, useful combinations to start from. When none fits, compose your own.

## The five axes

- **Topology** — the shape of the tree: star (you → N children), chain (each step
  feeds the next), deep tree (children that recurse into their own subtrees), or
  panel (N voices + one judge).
- **Channel** — how information moves: push (`send-message parent`), pull (you read
  a child anytime with `session <id> messages`), files (bulky exchange through the
  shared `scratch_dir`), steering (you message a child mid-`assistant_turn` to redirect it).
- **Synchronization** — when you move on: barrier (wait for all,
  `processes wait --spawned-by self ... --all`), first-wins (`--first`, then stop the
  rest), or continuous (no barrier — you watch and steer as they run).
- **Aggregation** — how N results become one: concat/merge/dedup, vote (majority),
  select (best, or first that works), synthesize (a new artifact from the inputs),
  or chain (no merge — one output is the next input).
- **Voice diversity** — how alike the children are: homogeneous (same
  model/preset/brief — throughput, redundancy) or heterogeneous (different model,
  provider, preset, angle, or prompt — diversity of judgment, e.g. Claude vs Codex).

## Two invariants, whatever you compose

- **You wait on, and aggregate from, your direct children only.** A manager's
  subtree is its own business; you see its single deliverable, not its internals.
- **Read-only children can't push or spawn.** A `strict`/`dontAsk` child is a
  pull-only leaf — design around that.

## Reading the named patterns

Each file states its axes (Shape), who does what (leader vs manager), the protocol
in real commands, when to use it, and the pitfalls. Load the one that matches your
situation — you do not need to read them all.
