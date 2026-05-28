# Handoff réduit — feature/centralize-db-writes

> Version condensée pour reprise. Histoire complète :
> `git log 67ce75dc^..d18d96d6` (125 commits, messages détaillés).
> Détails archi : docstrings de `src/twicc/providers/db_writer.py`.
> Worktree : `/home/twidi/dev/twicc-poc/.worktrees/feature-centralize-db-writes/`.

## 1. État (2026-05-28)

- Branche fusionnée dans `main` le 2026-05-28 (merge commit `--no-ff`).
  125 commits, du premier `67ce75dc` (`perf(compute): batch apply_session_complete
  writes in a single transaction`) au dernier `d18d96d6` (`docs(handoffs): add
  reduced handoff for centralize-db-writes`).
- Tests : **352/352 verts** (`uv run --extra test pytest`).
- **Audit DB write coverage (2026-05-28) : complet.** Toute écriture ORM
  Django dans `src/twicc/` passe soit par le DB writer (`_apply_*`),
  soit sous `run_under_db_write_lock(...)`. Zéro cas ambigu.

## 2. Ce qui reste

- (Optionnel) Runtime test sur DB non-vierge pour exercer `PrepareCronRestartsJob`
  avec de vrais `ProcessRun`/`SessionCron`. DB vierge ne touche pas ce path.
  Opération réservée au dev.

## 3. Architecture en 1 page

**But** : tuer les `database is locked` SQLite en routant tous les writes
DB via **(A)** un DB writer unique, **ou** **(B)** un `asyncio.Lock` partagé.

**A — DB writer permanent** (`src/twicc/providers/db_writer.py`)
- Lifecycle : `start_db_writer()` en TÊTE de `run_server`, `stop_db_writer()`
  en queue de shutdown. Outlive strictement tout producteur.
- 3 queues :
  - `_thread_queue` ← `put_thread_message(payload)` (threads initial sync)
  - `_subprocess_queue` ← `get_subprocess_queue()` (subprocess compute)
  - `_async_queue` ← `submit_async_job(job)` (await result) /
    `enqueue_async_job(job)` (fire-and-forget). Fallback registry via
    `BaseProviderHelpers.try_handle_async_job` pour les jobs
    provider-specific.

**B — Lock partagé**
- **Utiliser** : `await run_under_db_write_lock(lambda: ...)` — public,
  recommandé. Cancellation-safe, réentrant (LOCK#2 via `_LockLease` +
  `ContextVar`), shield-loops pour empêcher lock-vs-thread leak sur cancel.
- Éviter `get_db_write_lock()` (raw) sauf cas avancé documenté.
- Réentrance OK via `await` direct ou nested `run_under_db_write_lock`.
- **Footgun** : `asyncio.create_task(sub)` *à l'intérieur* du lock + `await
  sub` (où `sub` essaie d'acquire) = deadlock infini que `wait_for` ne
  casse PAS. Si besoin d'un sub avec son propre slot FIFO, utiliser
  `spawn_isolated_db_write_task(coro)`.

## 4. Invariants à préserver

- **Le DB writer outlive strictement tout producteur**.
- **Broadcasts hors lock** (règle granularité = simplicité-d'abord). Pour
  les autres side effects (caches, workspace JSON), utiliser
  `transaction.on_commit` pour différer après l'atomic.
- **Création projet** : toujours via `register_project` /
  `register_project_db_only`. Jamais de `Project.objects.create` brut.
- **`mtime` filtre `stale=False` partout** ; `sessions_count` inclut les
  stale partout.
- **Wiring d'un caller externe** : wrapper **les writes**, pas les
  broadcasts, pas les SDK boots, pas l'I/O FS longue.
- **Convention `provider`** : tout job sur `_async_queue` déclare un field
  `provider: Provider | None` (`None` = cross-provider).

## 5. Discipline worktree (CRITIQUE)

**Préfixer chaque commande Bash** par
`cd /home/twidi/dev/twicc-poc/.worktrees/feature-centralize-db-writes && ...`.

Sans ça, `uv run` résout l'editable install sur `main`. Brûlé plusieurs
fois pendant la branche.

Smoke test :
```bash
cd <worktree> && TWICC_DATA_DIR=$PWD DJANGO_SETTINGS_MODULE=twicc.settings uv run python -c "..."
```
Note : `twicc.settings`, **pas** `twicc.django.settings` (le `CLAUDE.md`
worktree section l'écrit faux).

## 6. Où chercher le détail

- **`git log 67ce75dc^..d18d96d6`** — 125 commits, messages détaillés. Marqueurs
  de phase dans les subjects : R17, R18, R19, LOCK#1, LOCK#2, WIRE#1/#2/#3,
  Phases 1-4 vues async.
- **`src/twicc/providers/db_writer.py`** — docstrings canoniques sur
  `run_under_db_write_lock`, `_drive_inner_under_held_lock`, `_LockLease`,
  `spawn_isolated_db_write_task`.
- **`docs/superpowers/plans/2026-05-20-shared-queue-db-writer.md`** —
  Appendix "As-built design" (commit `ee0f3249`). Pré-LOCK#1 ; pour le
  lock voir docstrings de `db_writer.py`.
- **`tests/test_db_write_lock_reentrance.py`** — 13 tests dédiés LOCK#2.

## 7. Processus Codex review (à préserver intact)

> **Cette section doit rester intacte** à toute réduction ultérieure.
> Elle documente le mécanisme à reproduire pour chaque nouveau cycle review.

Après chaque commit non trivial : session Codex review scope strict, cron
polling toutes les 2 min, convergence sur `Findings: none`.

### Lancer la session

```bash
TWICC=${TWICC_BIN:-$(command -v twicc 2>/dev/null)}
[ -n "$TWICC" ] || { echo "TwiCC not found" >&2; exit 1; }
$TWICC create-session \
  --project /home/twidi/dev/twicc-poc/.worktrees/feature-centralize-db-writes \
  --provider codex --preset Maximal \
  --title "Codex review <HASH7> <short topic>" --json \
  /tmp/codex-review-<HASH7>.md
```

Suivre le skill `twicc:twicc-create-session` à la lettre (résolution
`$TWICC` au début de chaque Bash, pas d'override `TWICC_DATA_DIR`, pas
de hardcoding).

### Prompt review (`/tmp/codex-review-<HASH7>.md`)

Sessions Codex stateless → self-contained obligatoire :
- Header `⚠ Scope (strict)` : hash du commit, fichiers en scope.
  Interdiction explicite des autres modules.
- Background suffisant pour comprendre le commit (DB writer, lock).
- Diff summary avec snippets.
- 3-6 checks ciblés.
- Instruction marker : terminer le dernier message par
  `<<<REVIEW_COMPLETE>>>` sur sa propre ligne. Pas dans les messages
  intermédiaires, pas dans les shell scripts du reasoning.

Exemples concrets : `/tmp/codex-review-5f9bbf21.md`,
`/tmp/codex-review-f32fd35f.md`.

### Cron de polling

```python
CronCreate({
    "cron": "*/2 * * * *",
    "recurring": True,
    "prompt": """
[Cron] Poll Codex review session <SESSION_ID> for `<<<REVIEW_COMPLETE>>>`.

One Bash:
  TWICC=${TWICC_BIN:-$(command -v twicc 2>/dev/null)}
  [ -n "$TWICC" ] || exit 1
  $TWICC session <SESSION_ID> messages --tail 1

If marker present:
  1. Filter findings to <SCOPE_FILES> only. Ignore other modules.
  2. Each in-scope finding: cd worktree, fix, pytest, commit,
     new strict-scope review, new cron, CronDelete this one.
  3. If Findings: none (or all out-of-scope): summarize, CronList,
     find this cron (prompt mentions <SESSION_ID>), CronDelete.

If no marker: one sentence ("still in progress at HH:MM"), stop.
Don't delete the cron. Don't spam tool calls.
""",
})
```

### Règle scope

- **In-scope** = fichiers touchés par le commit en review.
- **Hors-scope** = tout le reste, ignoré pour ce loop.
- **Jamais** relancer avec un scope élargi parce que Codex a vu autre
  chose. Si on veut élargir, c'est un nouveau loop sur un nouveau commit.

### Workflow

```
Commit X → /tmp/codex-review-X.md (scope strict + marker)
        → $TWICC create-session → SESSION_ID
        → CronCreate (poll 2 min)
        → marker → filter scope → fix + pytest + commit Y
            → CronDelete actuel → boucle sur Y
        → Findings: none → CronDelete → fini
```

### Mémoires associées

- `feedback_skill_invocation_exact` — résolution `$TWICC` à chaque Bash
- `project_codex_review_loop` — pattern général
- `feedback_twicc_sessions_stateless` — prompts self-contained
- `feedback_review_findings_must_be_fixed` — findings in-scope = fix immédiat
- `feedback_handoff_update_after_review` — update ce fichier après chaque review
