# Long-running migration with a watchdog

Patterns: `supervisor` + retry/escalation.
Shape: leader supervises a set of long-running executor workers (no barrier).

## Walkthrough
1. Spawn the workers for the long job (e.g. migrate N modules), `--annotation job=migrate`.
2. Don't just block — supervise in a loop:
   `processes --spawned-by self` for states; `session <id> messages --tail 2` for progress.
3. Intervene:
   - drifting → `send-message <id>` mid-run to redirect (no restart);
   - hung (stale `last_state_change_at`, output unchanged) → `process <id> stop`, then
     re-spawn it with a smaller mandate;
   - a worker that escalates "stuck" → answer it or re-delegate its remainder.
4. Collect results as each finishes; aggregate when the set is done.

## Notes
- Supervise your direct children only; a manager watches its own subtree.
- Tell "slow but progressing" from "hung" before killing — confirm the output changed.
- Keep workers `--hidden` + non-interactive so steering isn't blocked by a UI dialog.
