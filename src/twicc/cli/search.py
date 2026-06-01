"""CLI implementation for the ``twicc search`` subcommand."""

import sys


def main(
    query: str,
    *,
    limit: int = 20,
    offset: int = 0,
    include_hidden: bool = False,
    only_hidden: bool = False,
    spawned_by: str | None = None,
    spawn_root: str | None = None,
) -> None:
    """Execute a raw Tantivy search and print JSON results to stdout.

    ``spawned_by`` and ``spawn_root`` are raw CLI values (``None``, a
    session_id, or the literal ``"self"``). When either is ``"self"``
    the resolver needs DB access; we ``django.setup()`` only in that case
    so an ordinary full-text query stays Django-free. The typer wrapper
    guarantees they are mutually exclusive.
    """
    if spawned_by == "self" or spawn_root == "self":
        import django

        django.setup()

    from twicc.cli._drop_request.whoami import (
        resolve_spawn_root_filter,
        resolve_spawned_by_filter,
    )

    try:
        spawned_by_id = resolve_spawned_by_filter(spawned_by)
        spawn_root_id = resolve_spawn_root_filter(spawn_root)
    except RuntimeError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)

    from twicc.search import raw_search

    try:
        result = raw_search(
            query,
            limit=limit,
            offset=offset,
            include_hidden=include_hidden,
            only_hidden=only_hidden,
            spawned_by=spawned_by_id,
            spawn_root=spawn_root_id,
        )
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    print(result)
