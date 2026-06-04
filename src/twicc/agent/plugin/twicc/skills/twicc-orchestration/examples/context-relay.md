# Relay a job too big for one context

Patterns: `context-offload`.
Shape: chain — each session does part, then hands a fresh successor the rest.

## Walkthrough
1. Work normally, watching your context usage (reported each turn).
2. Before it's critical, write the state that matters to the shared scratch,
   `<scratch_dir>/<id>-state.md`: decisions made, what's left, key pointers — NOT your transcript.
3. Spawn a fresh executor briefed with the remaining goal + the path to that state file.
4. It picks up cold from the briefing + state, finishes (or relays again if still too big).
5. Collect its result; it is your deliverable.

## Notes
- The briefing must be self-contained — the successor has none of your memory.
- Hand over conclusions, not the journey; a fresh context wants state, not history.
- If you can see up front the job is huge, split it instead (`divide-and-conquer`).
