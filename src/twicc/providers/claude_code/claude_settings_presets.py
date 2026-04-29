"""Read/write Claude config presets.

File: <data_dir>/claude-settings-presets.json
"""

import os
import tempfile

import orjson

from twicc.paths import get_claude_settings_presets_path


def read_claude_settings_presets() -> dict:
    """Read claude-settings-presets.json. Returns empty config if missing or invalid."""
    path = get_claude_settings_presets_path()
    try:
        return orjson.loads(path.read_bytes())
    except (FileNotFoundError, orjson.JSONDecodeError):
        return {"presets": []}


def write_claude_settings_presets(config: dict) -> None:
    """Write claude-settings-presets.json atomically.

    Uses write-to-temp-then-rename to avoid partial writes.
    """
    path = get_claude_settings_presets_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    content = orjson.dumps(config, option=orjson.OPT_INDENT_2)

    fd, tmp_path = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(content)
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
