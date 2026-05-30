"""Top-level ``twicc update-session`` sub-app.

The sub-app shape mirrors ``twicc session``: a parent ``SESSION_ID`` argument
captured in ``ctx.obj`` and one sub-command per supported update. Currently
``settings``, ``title``, ``archive``, ``unarchive``, ``pin``, ``unpin``,
``hide``, and ``unhide`` are implemented; future sub-commands (``stop``, ...)
will register against this same app.
"""

from __future__ import annotations

import typer

from twicc.cli.update_session.archived_command import (
    update_archive_cmd,
    update_unarchive_cmd,
)
from twicc.cli.update_session.hidden_command import (
    update_hide_cmd,
    update_unhide_cmd,
)
from twicc.cli.update_session.pinned_command import (
    update_pin_cmd,
    update_unpin_cmd,
)
from twicc.cli.update_session.settings_command import update_settings_cmd
from twicc.cli.update_session.title_command import update_title_cmd


update_session_app = typer.Typer(
    name="update-session",
    help=(
        "Update an existing session (settings, title, archive, unarchive, "
        "pin, unpin; stop later)."
    ),
    invoke_without_command=False,
)


@update_session_app.callback()
def _update_session_default(
    ctx: typer.Context,
    session_id: str = typer.Argument(
        ...,
        help="Id of the existing session to update.",
    ),
) -> None:
    """Parent callback. Stashes ``session_id`` for the sub-command to read."""
    ctx.obj = session_id


update_session_app.command(name="settings")(update_settings_cmd)
update_session_app.command(name="title")(update_title_cmd)
update_session_app.command(name="archive")(update_archive_cmd)
update_session_app.command(name="unarchive")(update_unarchive_cmd)
update_session_app.command(name="pin")(update_pin_cmd)
update_session_app.command(name="unpin")(update_unpin_cmd)
update_session_app.command(name="hide")(update_hide_cmd)
update_session_app.command(name="unhide")(update_unhide_cmd)
