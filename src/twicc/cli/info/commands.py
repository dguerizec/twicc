"""``twicc info commands`` — list slash / dollar commands per provider."""

from __future__ import annotations


def build(
    provider: str | None,
    project_id: str | None,
    filter_query: str | None = None,
    include_disabled: bool = False,
) -> dict[str, list[dict]]:
    """Return ``{provider: [commands]}`` (no JSON emission, no Django setup).

    Without a project, lists only global commands (``project=NULL``).
    With one, lists global commands plus those scoped to the given
    project — i.e. the commands a session in that project would
    actually see at runtime.

    ``filter_query`` is a case-insensitive whitespace-tokenised search:
    each token must appear (as a substring) in either ``command`` or
    ``description``. Empty / whitespace-only queries are no-ops.

    Caller must have already initialised Django.
    """
    from twicc.cli.info._common import resolve_providers
    from twicc.core.models import Command

    tokens = filter_query.lower().split() if filter_query else []

    def _matches(command_str: str, description: str) -> bool:
        if not tokens:
            return True
        haystack = f"{command_str}\n{description}".lower()
        return all(token in haystack for token in tokens)

    output: dict[str, list[dict]] = {}
    for prov, _helpers in resolve_providers(provider, include_disabled=include_disabled):
        qs = Command.objects.filter(provider=prov.value)
        if project_id is None:
            qs = qs.filter(project__isnull=True)
        else:
            qs = qs.filter(project__isnull=True) | qs.filter(project_id=project_id)
        qs = qs.order_by("activation_char", "project_id", "name")

        entries: list[dict] = []
        for cmd in qs:
            command_str = f"{cmd.activation_char}{cmd.name}"
            if not _matches(command_str, cmd.description or ""):
                continue
            entries.append({
                "command": command_str,
                "plugin_name": cmd.plugin_name,
                "description": cmd.description,
                "argument_hint": cmd.argument_hint,
                "is_builtin": cmd.is_builtin,
                "scope": "global" if cmd.project_id is None else f"project:{cmd.project_id}",
            })
        output[prov.value] = entries

    return output
