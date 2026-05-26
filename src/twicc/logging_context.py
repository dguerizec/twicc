"""
Per-task provider tagging for log records.

The TwiCC backend runs code from several providers concurrently (Claude
Code, Codex, ...). Most of the long-lived modules under
``twicc.providers.`` (background_compute_task, sessions_watcher, compute_base)
are provider-agnostic by design, so their logger names alone do not say
which provider a given log line belongs to.

This module exposes a :class:`contextvars.ContextVar` that callers set at
each provider entry point (initial sync, watcher start, background-compute
start, spawn worker bootstrap). A logging :class:`logging.Filter` reads
the variable on every record and tacks the value onto the record as
``record.provider`` so the formatter can include ``%(provider)s``.

When no provider context is set (CLI startup, search index init, pricing
sync, model retirement check, ...), the filter falls back to the literal
string ``"global"``: log lines from cross-provider code stay readable and
honest about being unrelated to a specific provider, rather than getting
mis-attributed.

ContextVar semantics:

- Each :class:`asyncio.Task` inherits a *copy* of the parent's context
  when it is created, so a single ``set()`` at the task entry point
  propagates to every coroutine and ``sync_to_async`` call it spawns.
- The spawn worker process starts with a *fresh* default context. The
  worker must therefore call :meth:`current_provider.set` itself once
  it has resolved its compute factory.
- Hand-rolled ``threading.Thread`` does not copy the context. TwiCC
  uses asyncio + ``sync_to_async`` everywhere, so this is not a
  concern in practice.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING, Iterator

if TYPE_CHECKING:
    from twicc.core.enums import Provider

# ``"global"`` is also used by :class:`ProviderFilter` when the variable
# is unset; keep the default in sync with the filter so call sites that
# read ``current_provider.get()`` directly (rare) see the same value.
_PROVIDER_UNSET = "global"

current_provider: ContextVar[str | None] = ContextVar("twicc_provider", default=None)


class ProviderFilter(logging.Filter):
    """Attach the current provider tag to every :class:`LogRecord`.

    Always returns ``True`` (does not actually filter records out) — its
    sole purpose is to populate ``record.provider`` so the configured
    formatter can render ``%(provider)s`` / ``{provider}``.
    """

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        record.provider = current_provider.get() or _PROVIDER_UNSET
        return True


@contextmanager
def provider_log_context(provider: "Provider | None") -> Iterator[None]:
    """Temporarily tag every log record emitted in the body with ``provider``.

    Used by cross-provider services that process per-provider messages
    one at a time (notably :mod:`twicc.providers.db_writer`, whose task
    is launched at the global level with no provider context but whose
    individual applies belong to a specific provider). ``None`` falls
    back to the ``"global"`` tag like an unset context.

    Uses :class:`contextvars.ContextVar` ``set`` / ``reset`` so the
    scope is strictly limited to the ``with`` block — sibling tasks
    are unaffected.
    """
    token = current_provider.set(provider.value if provider is not None else None)
    try:
        yield
    finally:
        current_provider.reset(token)
