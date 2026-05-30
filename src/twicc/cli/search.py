"""CLI implementation for the ``twicc search`` subcommand."""

import sys


def main(
    query: str,
    *,
    limit: int = 20,
    offset: int = 0,
    include_hidden: bool = False,
    only_hidden: bool = False,
    spawned_by_id: str | None = None,
) -> None:
    """Execute a raw Tantivy search and print JSON results to stdout."""
    from twicc.search import raw_search

    try:
        result = raw_search(
            query,
            limit=limit,
            offset=offset,
            include_hidden=include_hidden,
            only_hidden=only_hidden,
            spawned_by=spawned_by_id,
        )
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    print(result)
