"""CLI implementation for the ``twicc workspace`` subcommand."""

from twicc.cli._output import emit_error, emit_json


def main(workspace_id: str) -> None:
    """Fetch a single workspace by ID and print its JSON representation to stdout.

    Returns the workspace even when archived (the caller has the explicit ID).
    """
    from twicc.workspaces import read_workspaces

    workspaces = read_workspaces().get("workspaces", [])
    workspace = next((w for w in workspaces if w.get("id") == workspace_id), None)
    if workspace is None:
        emit_error(f"Error: workspace '{workspace_id}' not found.", code=1)

    emit_json(workspace)
