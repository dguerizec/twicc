---
name: twicc-orchestration-worker
description: Act as a worker (leaf) in a TwiCC orchestration tree — execute one concrete task and deliver the result to your parent. Load when a parent gives you a task; read twicc-orchestration first.
---

# TwiCC Orchestration — Worker

You are a **worker**: a leaf in an orchestration tree. You have a parent and one concrete task to carry out and deliver. Read `twicc-orchestration` first — it covers the shared model and how to resolve `$TWICC` (used by executors below).

## When to use

- Your parent spawned you and told you to load this skill.
- You have a well-defined task to execute and report back.

## Do the work

- Read your mandate from your first message.
- Execute it. What you may do depends on the permissions your parent gave you: with an executor mode you can read, write, run commands, change code — whatever the task needs. **Only `strict`/`dontAsk` limits you to reading and analysis.**

You know your own `permission_mode` from your injected context.

## Two variants

- **Executor** — you can act, and you report back with `send-message parent`.
- **Read-only analyst** (`strict`/`dontAsk`) — you can only read and analyze, and you cannot run any command (so no `$TWICC` calls). Put your whole result in your reply text; your parent reads it by pulling your messages.

## Report back

- Executor: `send-message parent '<your result>'` when done.
- Read-only: leave the result as your final message; your parent reads it.

For bulky output (a large diff, a generated file), an executor writes it to the shared scratch space and sends a short message pointing to the file — see `twicc-orchestration`.

If you can run commands, keep your own annotations current as you go — `$TWICC update-session self annotations set:status=working`, then `set:status=done` (or `failed`) when finished. Short single-line values only; see `twicc-orchestration` for what annotations are for. (A read-only session can't run commands, so it keeps whatever its parent tagged it with.)

## If the task is too big

Don't spin in circles, and don't push on indefinitely. If the task is larger than one worker should carry, **escalate to your parent**: an executor tells the parent (`send-message parent`) where it got to and that the work should be re-split; a read-only analyst writes the same in its deliverable. The parent decides how to re-delegate.

## Related commands

- `$TWICC send-message parent <TEXT>` — report to or escalate to your parent (executor only). Skill: `twicc-send-message`.
- `$TWICC whoami` — your own session id, settings, and permission mode. Skill: `twicc-whoami`.
- `$TWICC update-session self annotations` — update your own tracking annotations. Skill: `twicc-update-session`.
