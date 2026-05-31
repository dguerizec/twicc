"""``twicc info agent-settings`` — list per-provider field choices and constraints."""

from __future__ import annotations


def build(provider: str | None, include_disabled: bool = False) -> dict[str, dict]:
    """Return ``{provider: {field: {values: [...]}}}`` (no JSON emission, no Django setup).

    For each field, every accepted value carries:

    - ``restricted_to``: ``None`` when the value is universally
      available, or the exhaustive list of model identifiers that
      support it (canonical ``family-version`` form plus bare aliases
      for latest entries).
    - ``description``: a short human-readable explanation when the
      provider declares one (currently for every ``permission_mode``
      value across providers, plus ``fast_mode=true`` for Claude
      Code); absent otherwise.

    Caller must have already initialised Django.
    """
    from twicc.cli._drop_request.help_strings import tokens_to_alias
    from twicc.cli.info._common import resolve_providers

    output: dict[str, dict] = {}
    for prov, helpers in resolve_providers(provider, include_disabled=include_disabled):
        choices = helpers.get_agent_settings_choices()
        constraints = helpers.get_agent_settings_constraints()
        descriptions = helpers.get_agent_settings_descriptions()

        per_field: dict[str, dict] = {}
        for field, values in choices.items():
            field_constraints = constraints.get(field, {})
            field_descriptions = descriptions.get(field, {})
            entries: list[dict] = []
            for value in values:
                entry: dict = {"value": value}
                if field == "context_max":
                    entry["context_max_alias"] = tokens_to_alias(value)
                entry["restricted_to"] = field_constraints.get(value)
                description = field_descriptions.get(value)
                if description is not None:
                    entry["description"] = description
                entries.append(entry)
            per_field[field] = {"values": entries}
        output[prov.value] = per_field

    return output
