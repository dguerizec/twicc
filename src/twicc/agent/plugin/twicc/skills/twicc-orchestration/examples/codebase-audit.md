# Audit / refactor a large codebase

Patterns: `divide-and-conquer` + `worker-pool`.
Shape: leader → one manager per service/area → workers per file, with bounded
concurrency inside each manager.

## Walkthrough
1. Leader splits the codebase into a few areas (services, packages, layers).
2. Per area, spawn a manager (`--annotation job=<area>`): "audit/refactor <area>;
   return a per-file summary of changes and risks."
3. Each manager lists its files and runs a worker-pool: at most K workers at once,
   one file per worker; as one finishes, hand it the next (`send-message`) or spawn fresh.
4. Each manager aggregates its files into one area deliverable, `send-message parent`.
5. Leader waits on its managers only, merges the area deliverables into the audit report.

## Notes
- Managers must be executors (they spawn + report). Workers that only read can be
  read-only; workers that edit need executor write permission.
- Cap K per manager from `usage` headroom — a wide codebase × wide pool exhausts quota.
- For a read-only audit (no edits), all workers are `dontAsk`/`strict` analysts, pulled.
