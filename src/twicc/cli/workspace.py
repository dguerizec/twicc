"""CLI implementation for the ``twicc workspace`` subcommand."""

import sys

import orjson


def main(workspace_id: str) -> None:
    """Fetch a single workspace by ID and print its JSON representation to stdout.

    Returns the workspace even when archived (the caller has the explicit ID).
    """
    from twicc.workspaces import read_workspaces

    workspaces = read_workspaces().get("workspaces", [])
    workspace = next((w for w in workspaces if w.get("id") == workspace_id), None)
    if workspace is None:
        print(f"Error: workspace '{workspace_id}' not found.", file=sys.stderr)
        sys.exit(1)

    sys.stdout.buffer.write(orjson.dumps(workspace, option=orjson.OPT_INDENT_2))
    sys.stdout.buffer.write(b"\n")
