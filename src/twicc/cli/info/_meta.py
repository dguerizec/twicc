"""Static metadata always surfaced by ``twicc info``.

Two payloads live here:

- :data:`AVAILABLE_INFO_ARGUMENTS` — the positional section names the
  command accepts, paired with a one-line description. Hand-written.
- :func:`load_twicc_command_metadata` — discovers every bundled
  ``twicc-*`` skill (under ``src/twicc/agent/plugin/twicc/skills``),
  reads each ``SKILL.md`` frontmatter, and returns a dict keyed by the
  derived command name (``twicc-foo`` → ``foo``). Lazy and cached: the
  scan runs once per process.

:func:`build_available_twicc_commands` is the small adapter on top that
injects the runtime-resolved ``twicc_executable`` into the per-command
``command`` field.
"""

from __future__ import annotations

from functools import cache
from pathlib import Path

AVAILABLE_INFO_ARGUMENTS: dict[str, str] = {
    "__description": (
        "Positional section names you can pass to 'info' to enrich the "
        "payload. Several can be composed in a single call; the output "
        "keys follow a canonical order regardless of input order."
    ),
    "presets": (
        "Agent settings presets per provider (with the synthetic "
        "'__defaults__' entry capturing effective defaults)."
    ),
    "commands": (
        "Slash / dollar commands per provider; filterable by --project "
        "and --filter."
    ),
    "models": (
        "Supported models per provider — identifiers, aliases, "
        "retirement dates, and capability flags."
    ),
    "agent-settings": (
        "Per-provider catalog of agent setting values and their model "
        "restrictions."
    ),
    "all": (
        "Shortcut expanding to presets + commands + models + "
        "agent-settings."
    ),
}


_SKILLS_DIR = Path(__file__).resolve().parent.parent.parent / "agent" / "plugin" / "twicc" / "skills"


def _parse_skill_description(skill_md_path: Path) -> str | None:
    """Return the ``description`` field of a ``SKILL.md`` frontmatter.

    The bundled TwiCC skills all share the same shape: a leading ``---``,
    one ``key: value`` per line (each value on a single line — no folded
    YAML), a trailing ``---``. We read just enough to extract the
    ``description`` line and stop — no YAML dependency.
    """
    try:
        with skill_md_path.open("r", encoding="utf-8") as fh:
            first_line = fh.readline()
            if first_line.rstrip("\n") != "---":
                return None
            for raw in fh:
                line = raw.rstrip("\n")
                if line == "---":
                    return None
                if line.startswith("description:"):
                    return line[len("description:") :].strip() or None
    except OSError:
        return None
    return None


@cache
def load_twicc_command_metadata() -> dict[str, dict[str, str]]:
    """Return ``{command_name: {"description": str, "skill": str}}``.

    Discovers every ``twicc-*`` skill bundled with the package and
    derives the matching CLI command name by stripping the ``twicc-``
    prefix. Entries are sorted alphabetically by command name for a
    deterministic ``info`` output. Skills whose ``SKILL.md`` is missing
    a parseable ``description`` are skipped silently — the bundle is
    in-tree and lint-checked, so this only guards against accidental
    breakage rather than papering over a real condition.
    """
    out: dict[str, dict[str, str]] = {}
    if not _SKILLS_DIR.is_dir():
        return out

    for skill_dir in sorted(_SKILLS_DIR.iterdir()):
        if not skill_dir.is_dir():
            continue
        name = skill_dir.name
        if not name.startswith("twicc-"):
            continue
        skill_md = skill_dir / "SKILL.md"
        description = _parse_skill_description(skill_md)
        if description is None:
            continue
        command_name = name[len("twicc-") :]
        out[command_name] = {"description": description, "skill": name}
    return out


def build_available_twicc_commands(twicc_executable: str) -> dict[str, dict[str, str]]:
    """Materialise the ``available_twicc_commands`` block for one invocation.

    The static per-skill payload comes from :func:`load_twicc_command_metadata`
    (cached); the ``command`` field is composed here because it embeds the
    runtime-resolved ``twicc_executable``.
    """
    metadata = load_twicc_command_metadata()
    return {
        command_name: {
            "description": entry["description"],
            "command": f"{twicc_executable} {command_name} --help",
            "skill": entry["skill"],
        }
        for command_name, entry in metadata.items()
    }
