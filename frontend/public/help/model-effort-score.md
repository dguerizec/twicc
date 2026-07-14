---
title: "Model × effort scores"
---

The model picker is a **matrix**: one row per model, one column per
reasoning effort. Each selectable cell shows a **score from 0 to 100** —
how well that model, at that effort, fits your priorities. Higher is
better. A quiet **?** means that pair hasn't been benchmarked.

The matrix carries a few visual cues:

- the cell's **background** gets more colored as the score rises;
- a **check mark** marks the currently selected cell, a small **dot** the
  default one;
- a **ring** highlights each provider's best score — solid for your
  default provider, dashed for the others when several providers are
  shown.

## Where the data comes from

Scores are built from **[DeepSWE](https://deepswe.datacurve.ai/)**, an
independent, contamination-free software-engineering benchmark by
DataCurve. It runs each model — at each reasoning effort — on a fixed set
of real coding tasks and reports, per (model, effort):

- **success rate** — how often it solves the tasks,
- **cost** — the average price of one attempt,
- **duration** — how long an attempt typically takes.

The data refreshes daily, and scores update automatically.

## What the score blends

The score combines three criteria, each of which you can weight:

- **Capability** — how good the model is at actually solving the tasks
  (its benchmark success). Better → higher.
- **Economy** — how cheap it is per task. Cheaper → higher.
- **Speed** — how fast it is per task. Faster → higher.

The score is **relative to the whole field**: across every benchmarked
(model, effort) pair, the weakest sits near 0 and the strongest near 100.
It's always computed over the **full set of models — even when the matrix
only shows some of them** — so a score never changes just because you
filtered the view.

## Task difficulty

By default a single **Task difficulty** slider sits below the matrix: it
drives the Capability weight (Economy and Speed adjust automatically).
Slide it up for a hard task where raw capability matters most; slide it
down for an easy task where a cheaper, faster model is the smarter pick.

## More controls

The **More controls** link swaps that single slider for the full
weighting block. The three sliders — **Capability / Economy / Speed** —
set how much each criterion counts. They share one budget that always
totals **100%**:

- Move one up and the other two give way (the total stays 100%). Every
  cell's score updates instantly.
- Put all the weight on **Capability** → the ranking is pure benchmark
  strength. Raise **Economy** or **Speed** → cheaper or faster models
  climb, even if they solve a little less.

Around the sliders:

- **Presets** — one-click weighting profiles: **Max quality** (capability
  above all), **Balanced** (the default), **Fast & cheap** (favor low
  cost and speed), **No rush** (best result at the best cost, ignoring
  speed — for a task you launch and leave running).
- **Lock** — freeze one slider while you tune the other two. Only one can
  be locked at a time.
- **When Capability moves, favor** — when you drag Capability, choose
  whether Economy or Speed absorbs the change first (or keep them
  proportional).

**Fewer controls** collapses the block back to the single Task difficulty
slider — nothing resets, both views drive the same weights.

## Auto-select best

With **Auto-select best** on, moving any slider also selects the
best-scoring cell for you, exactly as if you clicked it. When several
providers are shown, **Default provider only** restricts that pick to
your default provider; leave it off to let the best cell win regardless
of provider.

## Under the hood (for the curious)

- **Capability** blends two benchmark views: how often the model succeeds
  on a single attempt, and how many distinct tasks it can eventually
  solve. It leans slightly toward consistency — a model that succeeds
  reliably ranks above one that only occasionally gets it right.
- **Economy** and **Speed** are measured on a **logarithmic** scale: what
  matters is the ratio (from $1 to $2 counts like $10 to $20), not the
  absolute gap — this stops one very expensive or very slow model from
  dominating.
- The three criteria are combined so that being **weak on any single axis
  drags the whole score down** — a model has to be decent across the
  board, not just great at one thing.
- Finally, scores are spread across the full 0–100 range with a slight
  contrast curve, so differences are easy to read at a glance.
