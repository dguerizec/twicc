"""Cross-provider goal-lifecycle reduction for :attr:`Session.goals`.

A ``/goal`` gives a session a standing objective the agent keeps working
toward across turns (Claude Code: a session-scoped Stop hook + evaluator;
Codex: an app-server goal that drives continuation turns). Both providers
surface the goal's lifecycle in their JSONL — Claude as ``goal_status``
attachments, Codex as ``thread_goal_updated`` events — plus the ``/goal``
slash command for a manual clear. TwiCC turns that stream into a durable,
provider-agnostic history: a list of goals, newest last.

The provider-specific extraction lives in each compute
(``extract_goal_event``); this module holds the shared, stateless reducer
that folds the extracted :class:`GoalEvent`s into the stored list. Everything
is inferred from the JSONL lines alone — no dependency on any provider's own
state database.

Business model (deliberately provider-agnostic, not a mirror of either CLI's
internal identity of a goal):

* A goal stays **open** until it is completed or cleared.
* While open, a (re)stated objective is an **addendum** appended to that
  goal's ``objectives`` array (Claude "replaces" and Codex "edits in place"
  both collapse to the same user-facing notion of "same goal, refined").
* A (re)stated objective opens a **new** goal only once the previous one is
  closed (completed or cleared).
* An objective restated verbatim (e.g. the same goal re-emitted on resume) is
  deduplicated, so a resume never forks a spurious entry.

Stored shape — ``Session.goals`` is a chronological list of::

    {"objectives": [str, ...],   # successive full statements; current = last
     "state": "active" | "completed",
     "cleared": bool,            # True once a manual /goal clear closed it
     "raw_state": str | None,    # provider-native status, kept verbatim
     "created_at": iso | None,
     "updated_at": iso | None}
"""

from __future__ import annotations

from datetime import datetime
from typing import NamedTuple

# Normalised lifecycle states stored in ``Session.goals[*].state``.
GOAL_STATE_ACTIVE = "active"
GOAL_STATE_COMPLETED = "completed"


class GoalEvent(NamedTuple):
    """A normalised goal-lifecycle signal extracted from one JSONL line.

    Each provider maps its native lines to this shape in ``extract_goal_event``;
    :func:`apply_goal_event` folds a stream of them into ``Session.goals``.
    """

    # A (re)definition of the objective carried by this line, if any. Set only
    # on lines that actually (re)state the objective — Claude ``goal_status``
    # with ``sentinel: true``, Codex ``thread_goal_updated`` while ``active`` —
    # so progression ticks don't look like new definitions. May open a new goal
    # or extend the current one (see the reducer).
    objective: str | None = None
    # Lifecycle state this line asserts: ``active`` | ``completed`` | ``None``.
    state: str | None = None
    # Provider-native status string, kept verbatim for a finer UI later
    # (Claude: ``met`` / ``unmet``; Codex: ``active`` / ``complete`` /
    # ``paused`` / ``blocked`` / ``usage_limited`` / ``budget_limited``).
    raw_state: str | None = None
    # A manual ``/goal clear`` — closes the current goal without completing it.
    cleared: bool = False


def apply_goal_event(
    goals: list[dict], event: GoalEvent, *, timestamp: datetime | None
) -> bool:
    """Fold one :class:`GoalEvent` into ``goals`` (mutated in place).

    ``goals`` is the ``Session.goals`` list (see module docstring for the entry
    shape). Returns ``True`` when the list changed, so the live watcher can skip
    a write for a no-op batch. The full recompute simply folds every line from
    an empty list; because the fold is deterministic and only ever reads the
    last entry, ``reduce([], all_lines)`` equals ``reduce(stored, new_lines)`` —
    the incremental watcher and the authoritative recompute agree.
    """
    ts = timestamp.isoformat() if timestamp else None
    last = goals[-1] if goals else None
    last_open = (
        last is not None
        and last.get("state") != GOAL_STATE_COMPLETED
        and not last.get("cleared")
    )

    if event.cleared:
        if last_open:
            last["cleared"] = True
            last["updated_at"] = ts
            return True
        return False

    if event.objective is not None:
        if not last_open:
            # Previous goal closed (or none yet) → this opens a new one.
            goals.append(
                {
                    "objectives": [event.objective],
                    "state": event.state or GOAL_STATE_ACTIVE,
                    "cleared": False,
                    "raw_state": event.raw_state,
                    "created_at": ts,
                    "updated_at": ts,
                }
            )
            return True
        # Addendum to the open goal; dedup a verbatim re-statement (resume).
        changed = False
        if not last["objectives"] or event.objective != last["objectives"][-1]:
            last["objectives"].append(event.objective)
            changed = True
        changed = _apply_state(last, event) or changed
        if changed:
            last["updated_at"] = ts
        return changed

    # No objective on this line → a pure state/raw update on the open goal.
    if last_open and _apply_state(last, event):
        last["updated_at"] = ts
        return True
    return False


def _apply_state(entry: dict, event: GoalEvent) -> bool:
    """Apply ``event``'s ``state`` / ``raw_state`` onto ``entry``; return changed."""
    changed = False
    if event.state and event.state != entry.get("state"):
        entry["state"] = event.state
        changed = True
    if event.raw_state is not None and event.raw_state != entry.get("raw_state"):
        entry["raw_state"] = event.raw_state
        changed = True
    return changed
