# Plan, then execute

Separate deciding *what* to do from doing it: a plan is produced first, then
executors carry out its steps.

Shape: chain then star · pull + push · staged · — · planner often a stronger/different voice.

## Who does what
- **Leader or manager** — either plan yourself, or spawn a **planner** (often
  read-only — it only analyzes and proposes). Read the plan, sanity-check it, then
  fan out executors over its steps (scatter-gather if independent, pipeline if sequential).
- **Planner worker** — analyzes the goal and returns a concrete, ordered list of
  steps. Does not execute.
- **Executor workers** — each carries out one step.

## Protocol
1. Get the plan (yourself, or a read-only planner you pull).
2. Validate it — reject or refine an unworkable plan before spending executors.
3. Execute: independent steps → scatter-gather; sequential → pipeline.
4. Aggregate the outcomes against the plan; report what's done and what slipped.

## Use it when
The decomposition itself is hard and worth getting right before committing work,
or you want a cheap read-only pass before expensive execution.
Not when the split is obvious (just scatter-gather directly).

## Pitfalls
- Don't execute a plan you haven't checked — a bad plan multiplies into N bad workers.
- The planner being read-only is a feature (safe, cheap); the executors must not be.
- Keep the plan in the shared scratch if it's long, so executors can refer to it.

Examples: `examples/research-synthesis.md`, `examples/ship-feature.md`, `examples/parallel-feature-integration.md`.
