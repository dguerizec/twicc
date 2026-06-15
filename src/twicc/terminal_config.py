"""Read/write terminal configuration (custom combos and snippets).

File: <data_dir>/terminal-config.json
"""

import orjson

from twicc.atomic_json import atomic_write_json
from twicc.paths import get_terminal_config_path


def read_terminal_config() -> dict:
    """Read terminal-config.json. Returns empty config if file doesn't exist or is invalid."""
    path = get_terminal_config_path()
    try:
        return orjson.loads(path.read_bytes())
    except (FileNotFoundError, orjson.JSONDecodeError):
        return {"combos": [], "snippets": {}}


def write_terminal_config(config: dict) -> None:
    """Write terminal-config.json atomically (whole-blob overwrite)."""
    atomic_write_json(get_terminal_config_path(), config)
