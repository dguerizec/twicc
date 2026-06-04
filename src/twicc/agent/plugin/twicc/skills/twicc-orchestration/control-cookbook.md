# Process Control Cookbook

Concrete process-control recipes for leaders and managers. Use these when you
need exact `processes` commands; otherwise the main orchestration skill is enough.

## Direct-child barrier

Wait for the children you spawned, not grandchildren:

```bash
$TWICC processes wait --spawned-by self user_turn dead --timeout <N>
```

Use `--all` by default. Use `--first` only for races or queue loops where one
finished child is enough to move forward.

If you also pass explicit session ids, they are added to the filtered wait pool
and deduplicated; omit explicit ids for a pure direct-child barrier.

## Scoped barrier by annotation

Use annotations to wait on one phase, wave, role, or attempt set:

```bash
$TWICC processes wait --spawned-by self --annotation phase=audit user_turn dead --timeout <N>
$TWICC processes wait --spawned-by self --annotation wave=3 user_turn dead --timeout <N>
```

Annotation filters on `processes` must always be paired with a filiation scope.
For orchestration control, prefer `--spawned-by self`.

## First-wins race

Wait for one child to finish, validate it, then stop the losers:

```bash
$TWICC processes wait --spawned-by self --annotation attempt:exists user_turn --first --timeout <N>
$TWICC processes stop <LOSER_ID> [<LOSER_ID>...] --timeout 30
```

If you tagged losers after validation:

```bash
$TWICC processes stop --spawned-by self --annotation status=loser --timeout 30
```

Never stop before validating the first finisher; "first done" is not "correct".

## Stop selected children

For one child, use the exact id:

```bash
$TWICC process <SESSION_ID> stop --timeout 30
```

For a batch you own:

```bash
$TWICC processes stop --spawned-by self --annotation status=cancelled --timeout 30
```

`processes stop` does not accept `parent` or `--spawn-tree`.

## Abort a subtree

Only do this when you intentionally cancel a manager and everything below it.
`--descendants` excludes the target, so pass the manager id explicitly too:

```bash
$TWICC processes stop <MANAGER_ID> --descendants <MANAGER_ID> --timeout 30
```

Explicit ids and filtered ids are merged: the explicit `<MANAGER_ID>` stops the
manager, and `--descendants <MANAGER_ID>` adds every proper descendant below it.

This is exceptional cleanup, not normal synchronization.

## Inspect before acting

Use direct-child listing for ordinary control:

```bash
$TWICC processes --spawned-by self
$TWICC processes --spawned-by self --annotation status=blocked
```

Use `topology self` for structure and context. Use `processes --spawn-tree self`
only for an explicit whole-tree inventory, not for routine manager control.

## Annotation keys for control

- `status` — `working`, `done`, `failed`, `blocked`, `cancelled`, `loser`, `runaway`.
- `phase` — named phase such as `audit`, `implement`, `verify`.
- `wave` — bounded-concurrency wave number.
- `attempt` — speculative attempt id.
- `job` / `role` — functional role when a barrier targets only one role.
