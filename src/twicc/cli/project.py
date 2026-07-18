"""CLI implementation for the ``twicc project`` subcommand."""

from twicc.cli._output import emit_error, emit_json


def main(project_id: str) -> None:
    """Fetch a single project by ID and print its JSON representation to stdout."""
    import django

    django.setup()

    from twicc.core.models import Project
    from twicc.core.serializers import serialize_project
    from twicc.project_icons import load_repo_icon_cache
    from twicc.projects import worktree_child_ids
    from twicc.workspaces import read_workspaces

    try:
        project = Project.objects.get(id=project_id)
    except Project.DoesNotExist:
        emit_error(f"Error: project '{project_id}' not found.", code=1)

    # Warm the repo-icon cache (a short-lived CLI process never ran startup
    # discovery) so ``serialize_project``'s ``repo_icon_url`` brick is populated
    # rather than always ``None``.
    load_repo_icon_cache()
    data = serialize_project(project)
    data["workspaces"] = [
        ws["id"]
        for ws in read_workspaces().get("workspaces", [])
        if project_id in ws.get("projectIds", [])
    ]
    # Reverse of ``worktree_of``: the ids of this project's git worktrees (the
    # projects whose ``worktree_of`` points back here), most-recently-active
    # first. ``[]`` when the project has no worktrees. Injected here, alongside
    # ``workspaces``, rather than baked into ``serialize_project`` — both are
    # CLI-output enrichments that would otherwise cost a query per call site
    # and break the serializer's async-safe, query-free contract.
    data["worktrees"] = worktree_child_ids(project_id)

    emit_json(data)
