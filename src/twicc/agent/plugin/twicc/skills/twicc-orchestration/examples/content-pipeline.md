# Raw material to polished deliverable

Patterns: `pipeline` + a `produce-refute` fact-check stage.
Shape: chain — collect → structure → draft → fact-check → format; each stage one child.

## Walkthrough
1. Define the stages and the artifact passed between them (kept in the shared scratch).
2. Collect/clean: a worker turns raw input into a clean structured outline, writes
   `<scratch_dir>/<id>-outline.md`, reports done.
3. Draft: a worker reads the outline, writes `<id>-draft.md`.
4. Fact-check (produce-refute): a refuter reads the draft and flags every unsupported
   claim; loop corrections back to the draft stage if needed.
5. Format: produce the final, polished artifact.
6. Wait for each stage before starting the next; the last artifact is the result.

## Notes
- Hand artifacts through the scratch, never through giant messages.
- Re-run a single stage in place if its output is wrong (idempotent) — no need to restart the chain.
- The fact-check stage can be a read-only worker (it only reads + reports).
