"""CLI implementation for the ``twicc project`` subcommand."""

from twicc.cli._output import emit_error, emit_json


def main(project_id: str) -> None:
    """Fetch a single project by ID and print its JSON representation to stdout."""
    import django

    django.setup()

    from twicc.core.models import Project
    from twicc.core.serializers import serialize_project
    from twicc.workspaces import read_workspaces

    try:
        project = Project.objects.get(id=project_id)
    except Project.DoesNotExist:
        emit_error(f"Error: project '{project_id}' not found.", code=1)

    data = serialize_project(project)
    data["workspaces"] = [
        ws["id"]
        for ws in read_workspaces().get("workspaces", [])
        if project_id in ws.get("projectIds", [])
    ]

    emit_json(data)
