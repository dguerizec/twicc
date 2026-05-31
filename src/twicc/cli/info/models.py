"""``twicc info models`` — list per-provider supported model versions."""

from __future__ import annotations


def build(provider: str | None, include_disabled: bool = False) -> dict[str, list[dict]]:
    """Return ``{provider: [models]}`` (no JSON emission, no Django setup).

    Each entry exposes the canonical ``identifier`` (``family-version``)
    and an ``alias`` field — the bare family name for the latest of its
    family, ``None`` otherwise. ``extra`` is the fully-expanded
    provider-specific capability dict (every flag, including ``false``
    values). ``full_name`` and pricing are intentionally omitted: the
    former is provider-internal SDK plumbing the agent does not need,
    and the latter has its own concerns.

    Caller must have already initialised Django.
    """
    from twicc.cli.info._common import resolve_providers

    output: dict[str, list[dict]] = {}
    for prov, helpers in resolve_providers(provider, include_disabled=include_disabled):
        entries: list[dict] = []
        for mv in helpers.MODEL_VERSIONS:
            entries.append({
                "identifier": f"{mv.model}-{mv.version}",
                "alias": mv.model if mv.latest else None,
                "family": mv.model,
                "version": mv.version,
                "latest": mv.latest,
                "retirement_date": mv.retirement_date.isoformat() if mv.retirement_date else None,
                "extra": helpers.serialize_model_extra(mv),
            })
        output[prov.value] = entries

    return output
