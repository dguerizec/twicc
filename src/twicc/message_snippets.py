"""Read/write message snippets configuration.

File: <data_dir>/message-snippets.json
"""

import orjson

from twicc.atomic_json import atomic_write_json
from twicc.paths import get_message_snippets_config_path


def read_message_snippets_config() -> dict:
    """Read message-snippets.json. Returns empty config if file doesn't exist or is invalid."""
    path = get_message_snippets_config_path()
    try:
        return orjson.loads(path.read_bytes())
    except (FileNotFoundError, orjson.JSONDecodeError):
        return {"snippets": {}}


def write_message_snippets_config(config: dict) -> None:
    """Write message-snippets.json atomically (whole-blob overwrite)."""
    atomic_write_json(get_message_snippets_config_path(), config)
