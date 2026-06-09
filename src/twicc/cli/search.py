"""CLI implementation for the ``twicc search`` subcommand."""

from twicc.cli._output import emit_error, emit_json


def main(
    query: str,
    *,
    limit: int = 20,
    offset: int = 0,
    include_hidden: bool = False,
    only_hidden: bool = False,
    spawned_by: str | None = None,
    spawn_tree: str | None = None,
    descendants: str | None = None,
    siblings: str | None = None,
    annotation: list[str] | None = None,
) -> None:
    """Execute a raw Tantivy search and print JSON results to stdout.

    ``spawned_by`` and ``descendants`` are raw CLI values (``None``, a
    session_id, or ``"self"`` / ``"parent"``). ``spawn_tree`` accepts
    ``None``, a session_id, or ``"self"``; ``siblings`` accepts ``None``, a
    session_id, or ``"self"``. When any value needs DB access (a keyword on
    ``spawned_by``, any value on ``spawn_tree`` — the resolver always looks
    the id up to find the tree's root — or any value on ``descendants`` /
    ``siblings`` which always walk the DB), we ``django.setup()`` so an
    ordinary full-text query stays Django-free. The typer wrapper guarantees
    they are mutually exclusive.
    """
    if (
        spawned_by in ("self", "parent")
        or spawn_tree is not None
        or descendants is not None
        or siblings is not None
        or annotation
    ):
        import django

        django.setup()

    from twicc.cli._drop_request.whoami import (
        resolve_descendants_filter,
        resolve_siblings_filter,
        resolve_spawn_tree_filter,
        resolve_spawned_by_filter,
    )

    try:
        spawned_by_id = resolve_spawned_by_filter(spawned_by)
        spawn_root_id = resolve_spawn_tree_filter(spawn_tree)
        descendants_ids = resolve_descendants_filter(descendants)
        siblings_ids = resolve_siblings_filter(siblings)
    except RuntimeError as e:
        emit_error(str(e), code=1)

    annotation_filters = None
    if annotation:
        from twicc.cli._annotation_filters import parse_annotation_filter
        try:
            annotation_filters = [parse_annotation_filter(spec) for spec in annotation]
        except ValueError as exc:
            emit_error(f"Error: {exc}", code=2)

    from twicc.search import raw_search

    try:
        result = raw_search(
            query,
            limit=limit,
            offset=offset,
            to_json=False,
            include_hidden=include_hidden,
            only_hidden=only_hidden,
            spawned_by=spawned_by_id,
            spawn_tree=spawn_root_id,
            descendants=descendants_ids,
            siblings=siblings_ids,
            annotation_filters=annotation_filters,
        )
    except RuntimeError as exc:
        emit_error(f"Error: {exc}", code=1)

    emit_json(result)
