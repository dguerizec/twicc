# Peer coordination (siblings talk directly)

Children of the same parent coordinate **with each other directly** — a handoff, a
heads-up, a shared discovery — instead of bouncing every exchange up through the
parent. The tree is the control structure (who spawns, who waits, who aggregates);
it is **not** a wall between siblings. Nothing stops peers from talking, and when a
few real cross-dependencies exist, letting them talk laterally is simpler and
faster than relaying through the parent.

Shape: star or mesh · sideways (peer `send-message` + pull) · continuous · per-peer report to parent · homogeneous or mixed.

## Who does what
- **Leader or manager** — sets it up and still owns control. Spawn the peers; in
  each brief, say explicitly that its co-workers are **siblings it may talk to**,
  how to find them (`sessions --siblings self`, `topology self --siblings`), and
  what they may coordinate on. You still wait on and aggregate **your direct
  children** as usual — peer chat does not replace their report up to you.
- **Workers (peers)** — coordinate laterally as the work needs: `send-message
  <sibling_id>` for one peer, `send-messages --siblings self` to broadcast to all
  peers (you are always excluded), pull a peer's transcript with `session <id>
  messages`. Each peer still reports its own result to the parent.

## Protocol
1. The parent spawns the peers (executors, so they can also do project work), giving
   each: its own task, the fact that its co-workers are siblings, and how to discover
   them. (Read-only peers can still send via the `mcp__twicc__*` tools; if MCP is off
   they can only be pulled.)
2. A peer that produces something the others need announces it:
   `send-messages --siblings self --message 'auth done — login at /v2/login, schema in <scratch_dir>/<id>-auth.md'`.
3. A peer that needs a peer's output pulls it (`session <sibling_id> messages
   --tail N`), or asks for it directly (`send-message <sibling_id> '...'`). Bulky
   artifacts go through the shared `scratch_dir`, not the message.
4. Each peer still reports its own result to the parent; the parent aggregates
   normally.

## Use it when
Pieces are *mostly* parallel but have a few genuine cross-links — a shared
decision, an interface one peer defines and the others consume, a finding worth
broadcasting — and routing each one up to the parent and back would only add
latency. The classic case: a fan-out where worker B can start as soon as worker A
publishes one fact, with no reason to wake the parent for the handoff.

Not when pieces are truly independent (→ `scatter-gather`: no peer channel needed),
when the work is a strict sequential chain (→ `pipeline`, or its peer-handoff
variant), or when independence must be **guaranteed** (→ `quorum` / `debate`:
isolate the peers on purpose — never let them confer).

## Pitfalls
- **Coordination, not control.** The parent still owns who waits on whom and the
  final merge. Peers must not become each other's blocking dependency in a way that
  can deadlock — keep lateral exchanges short and asynchronous. A peer that has gone
  `dead` is resurrected by the next `send-message`, so you never need to keep peers
  alive for each other.
- **A read-only peer can still announce** — the `mcp__twicc__*` send tools work in
  every mode (see twicc-orchestration › Permission modes); only with MCP disabled is
  it pull-only.
- **Discovery is live.** `--siblings self` excludes you and reflects whoever exists
  now; a peer spawned later is automatically in scope — re-run the discovery rather
  than caching ids.
- **Don't hand-roll a pipeline.** If the work is a strict chain, use `pipeline`;
  peer coordination is for a handful of cross-links, not a full ordered handoff.

Examples: the contract-alignment note in `examples/parallel-feature-integration.md`, and the peer-handoff variant in `patterns/pipeline.md`.
