"""``twicc info`` — read-only inspection of TwiCC's per-provider catalogues.

A single command that always returns ``twicc_version`` + ``providers``,
plus zero or more sections selected by positional arguments. The valid
section names are ``presets``, ``commands``, ``models``, and
``agent-settings`` — passing several composes them in a single payload,
saving the caller from issuing multiple commands.
"""

from __future__ import annotations

import typer

from twicc.cli._output import emit_error

VALID_SECTIONS: tuple[str, ...] = ("presets", "commands", "models", "agent-settings")


def info_cmd(
    sections: list[str] = typer.Argument(
        None,
        metavar="[SECTION...]",
        help=(
            "Zero or more sections to include in the output. Valid "
            "values: presets, commands, models, agent-settings, all "
            "(shortcut for the four others). The output always carries "
            "twicc_version and providers; each section name listed adds "
            "the matching key. Order of the output keys is canonical "
            "(presets, commands, models, agent-settings) regardless of "
            "input order; duplicates are ignored."
        ),
    ),
    provider: str = typer.Option(
        None,
        "--provider",
        help=(
            "Filter every section by provider key (e.g. 'claude_code', "
            "'codex'). Naming a provider bypasses the disabled-provider "
            "filter — passing an explicit key always returns its data."
        ),
    ),
    project: str = typer.Option(
        None,
        "--project",
        help=(
            "Project (id with or without leading dash, or directory "
            "path). Only consumed when the 'commands' section is "
            "requested. When supplied, commands scoped to that project "
            "are listed alongside the globals; otherwise only globals."
        ),
    ),
    filter_query: str = typer.Option(
        None,
        "--filter",
        help=(
            "Case-insensitive substring filter for the 'commands' "
            "section: whitespace-separated tokens are ANDed, each must "
            "appear in either the command literal or its description."
        ),
    ),
    include_disabled: bool = typer.Option(
        False,
        "--include-disabled-providers",
        help=(
            "When set, sections also include providers the user has "
            "disabled. Naming a provider with --provider already "
            "bypasses the filter for that one; this flag flips it for "
            "the listing as a whole. The providers key is always "
            "exhaustive — this flag only affects the section payloads."
        ),
    ),
) -> None:
    """Inspect TwiCC's per-provider catalogues (presets/commands/models/agent-settings)."""
    sections = sections or []
    canonical_sections = _validate_sections(sections)

    if (project is not None or filter_query is not None) and "commands" not in canonical_sections:
        emit_error(
            "Error: --project and --filter only apply to the 'commands' section; pass 'commands' as a positional argument.",
            code=1,
        )

    project_id = None
    if project is not None:
        from twicc.cli._drop_request.project import derive_project_id

        project_id = derive_project_id(project)[0]

    import django

    django.setup()

    from django.conf import settings

    from twicc.cli.info._common import emit_json
    from twicc.cli.info._meta import AVAILABLE_INFO_ARGUMENTS
    from twicc.providers.helpers import get_provider_helpers_registry
    from twicc.providers.state import is_provider_enabled
    from twicc.synced_settings import read_synced_settings
    from twicc.version import get_version

    default_provider = read_synced_settings().get("defaultProvider")

    providers: dict[str, dict] = {}
    for prov, helpers in get_provider_helpers_registry().items():
        providers[prov.value] = {
            "identifier": prov.value,
            "name": helpers.LABEL,
            "disabled": not is_provider_enabled(prov),
            "default": prov.value == default_provider,
        }

    output: dict = {
        "twicc_version": get_version(),
        "twicc_executable": settings.TWICC_LAUNCH_PREFIX,
        "providers": providers,
        "available_info_arguments": AVAILABLE_INFO_ARGUMENTS,
    }

    if "presets" in canonical_sections:
        from twicc.cli.info.presets import build as build_presets

        output["presets"] = build_presets(provider=provider, include_disabled=include_disabled)

    if "commands" in canonical_sections:
        from twicc.cli.info.commands import build as build_commands

        output["commands"] = build_commands(
            provider=provider,
            project_id=project_id,
            filter_query=filter_query,
            include_disabled=include_disabled,
        )

    if "models" in canonical_sections:
        from twicc.cli.info.models import build as build_models

        output["models"] = build_models(provider=provider, include_disabled=include_disabled)

    if "agent-settings" in canonical_sections:
        from twicc.cli.info.agent_settings import build as build_agent_settings

        output["agent-settings"] = build_agent_settings(provider=provider, include_disabled=include_disabled)

    emit_json(output)


def _validate_sections(sections: list[str]) -> set[str]:
    """Return the set of requested section names, exiting 1 on unknown values.

    Recognises ``"all"`` as a shortcut expanding to every entry of
    :data:`VALID_SECTIONS`. Order of the input list does not matter —
    callers iterate :data:`VALID_SECTIONS` in canonical order — so a set
    is the natural return shape, and duplicates collapse for free.
    """
    valid = set(VALID_SECTIONS)
    accepted = valid | {"all"}
    invalid = sorted({s for s in sections if s not in accepted})
    if invalid:
        emit_error(
            f"Error: unknown section(s) {invalid}. Valid: {[*VALID_SECTIONS, 'all']}.",
            code=1,
        )
    if "all" in sections:
        return valid
    return {s for s in sections if s in valid}
