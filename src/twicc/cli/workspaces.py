"""CLI implementation for the ``twicc workspaces`` subcommand."""

import sys

import orjson


def main(*, limit: int = 20, offset: int = 0, archived: bool = False) -> None:
    """List workspaces as JSON to stdout.

    Workspaces are stored in ``<data_dir>/workspaces.json`` and managed via the
    TwiCC UI; this command is read-only.
    """
    from twicc.workspaces import read_workspaces

    workspaces = read_workspaces().get("workspaces", [])

    if not archived:
        workspaces = [w for w in workspaces if not w.get("archived", False)]

    workspaces = workspaces[offset : offset + limit]

    sys.stdout.buffer.write(orjson.dumps(workspaces, option=orjson.OPT_INDENT_2))
    sys.stdout.buffer.write(b"\n")
