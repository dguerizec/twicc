"""
Read the named dockable-layouts catalog from layouts.json in the data directory.

A "layout" is a reusable, user-global dockable-panel arrangement (the intention:
tab->dock assignment, minimized docks, resize fractions). Catalog entries are
``{"id", "name", "intention"}`` objects stored under the ``"layouts"`` key in
``<data_dir>/layouts.json``. The catalog is user-config (like ``workspaces.json``),
synced across devices over the websocket, and written WHOLE-BLOB from the frontend
(see ``UpdatesConsumer._handle_update_layouts``); there is no per-entry CLI mutation
path yet.

Sessions and project/global defaults only *reference* a layout by id — they never
embed its content. The special ``"single-pane"`` id is synthetic (no docks); it is
injected by the frontend and never stored here.
"""

import orjson

from twicc.paths import get_layouts_path


def read_layouts() -> dict:
    """Read the layouts catalog from layouts.json.

    Returns an empty dict if the file doesn't exist or is invalid; callers read
    the ``"layouts"`` list out of it (defaulting to ``[]``).
    """
    path = get_layouts_path()
    try:
        return orjson.loads(path.read_bytes())
    except (FileNotFoundError, orjson.JSONDecodeError):
        return {}
