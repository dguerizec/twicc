"""CLI implementation for the ``twicc search`` subcommand."""

import sys

import orjson


def main(
    query: str,
    *,
    limit: int = 20,
    offset: int = 0,
    include_hidden: bool = False,
    only_hidden: bool = False,
    spawned_by_id: str | None = None,
) -> None:
    """Execute a full-text search and print JSON results to stdout."""
    import django

    django.setup()

    from twicc import search as search_module
    from twicc.search import init_search_index

    try:
        init_search_index()
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    try:
        results = search_module.search(
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

    data = {
        "query": results.query,
        "total_sessions": results.total_sessions,
        "results": [
            {
                "session_id": r.session_id,
                "score": r.score,
                "matches": [
                    {
                        "line_num": m.line_num,
                        "from_role": m.from_role,
                        "snippet": m.snippet,
                        "score": m.score,
                        "timestamp": m.timestamp,
                    }
                    for m in r.matches
                ],
            }
            for r in results.results
        ],
    }

    sys.stdout.buffer.write(orjson.dumps(data, option=orjson.OPT_INDENT_2))
    sys.stdout.buffer.write(b"\n")
