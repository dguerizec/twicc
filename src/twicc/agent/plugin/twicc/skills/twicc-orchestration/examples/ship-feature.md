# Ship a feature end-to-end

Patterns: `plan-then-execute` + `divide-and-conquer` + `produce-refute`.
Shape: leader → plan → managers/workers implement in parallel → adversarial test pass.

## Walkthrough
1. Plan: decompose the feature into independent slices (API, data layer, UI, tests).
   Do it yourself or via a planner; validate the plan.
2. Execute: spawn an executor per slice (a slice that is itself big becomes a manager
   → divide-and-conquer). Each writes code and reports its diff / scratch file.
3. Barrier on your direct children; integrate the slices.
4. Produce-refute the result: spawn a tester/refuter to break it (run tests, find edge
   cases); loop fixes back to the relevant slice (`send-message`).
5. Aggregate into a ready-to-review change.

## Notes
- Executors need write permission; keep them `--hidden` + non-interactive to avoid approvals.
- Strongest verification: refuter on a different provider than the implementer.
- Slices that touch the same files shouldn't run in parallel — sequence them (pipeline).
