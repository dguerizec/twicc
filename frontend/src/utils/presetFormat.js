// Per-row spec for ``formatPresetSummary``: each entry maps a preset shape
// key to the wire field name (used for ``supportsAgentSetting`` filtering and
// ``getChoiceLabel`` lookups), the short label that prefixes the value in the
// summary, and the formatter applied to the raw value.
//
// The boundary between preset key and wire field name lives here on purpose:
// presets historically use ``model`` / ``thinking`` (no suffix) while the
// session/wire format uses ``selected_model`` / ``thinking_enabled``. Other
// fields share the same name on both sides.
//
// Adding a field to this list is enough to surface it for any provider that
// declares it in ``getAgentSettingsCategories``; providers that don't support
// the field skip it via ``supportsAgentSetting``.
const PRESET_SUMMARY_FIELDS = [
    { presetKey: 'model',            wireField: 'selected_model',  summaryLabel: 'model',      formatValue: (v, h) => h.getModelLabel(v) },
    { presetKey: 'context_max',      wireField: 'context_max',     summaryLabel: 'context',    formatValue: (v, h) => h.getChoiceLabel('context_max', v) ?? v },
    { presetKey: 'effort',           wireField: 'effort',          summaryLabel: 'effort',     formatValue: (v, h) => h.getChoiceLabel('effort', v) ?? v },
    { presetKey: 'thinking',         wireField: 'thinking_enabled', summaryLabel: 'thinking',  formatValue: (v, h) => h.getChoiceLabel('thinking_enabled', v) ?? v },
    { presetKey: 'permission_mode',  wireField: 'permission_mode', summaryLabel: 'permission', formatValue: (v, h) => h.getChoiceLabel('permission_mode', v) ?? v },
    { presetKey: 'claude_in_chrome', wireField: 'claude_in_chrome', summaryLabel: 'chrome',    formatValue: (v, h) => h.getChoiceLabel('claude_in_chrome', v) ?? v },
    { presetKey: 'fast_mode',        wireField: 'fast_mode',       summaryLabel: 'fast',      formatValue: (v, h) => h.getChoiceLabel('fast_mode', v) ?? v },
]

// Shared core: build the per-field summary parts for a settings source.
// ``getValue(spec)`` reads the raw value (by preset-shape key OR by wire name —
// see callers). ``current`` (optional), keyed by WIRE field name, holds the
// session's effective values: when provided, a part is flagged ``forced`` (the
// dashed underline) when the source's value DIFFERS from the current effective
// one — i.e. "this is what applying it would change". Fields the provider does
// not support, or that are null/undefined in the source, are skipped.
//
// ``helpers`` is the provider's helpers instance — it controls both the
// choice/label catalogue and which fields surface (only fields the provider
// declares via ``supportsAgentSetting`` make it in, so a Claude-only field like
// ``claude_in_chrome`` is silently dropped for any other provider).
function summaryParts(helpers, getValue, current) {
    const parts = []
    for (const spec of PRESET_SUMMARY_FIELDS) {
        if (!helpers.supportsAgentSetting(spec.wireField)) continue
        const value = getValue(spec)
        if (value === null || value === undefined) continue
        const forced = current ? value !== current[spec.wireField] : false
        parts.push({ text: `${spec.summaryLabel}: ${spec.formatValue(value, helpers)}`, forced })
    }
    return parts
}

/**
 * Summary parts of the fields a preset forces, as ``[{ text: "label: value",
 * forced }]`` — ``forced`` marks a value differing from ``current`` (the
 * session's effective per-wire-field values). Preset shape uses ``model`` /
 * ``thinking`` keys.
 */
export function presetSummaryParts(preset, helpers, current = null) {
    return summaryParts(helpers, (spec) => preset[spec.presetKey], current)
}

/**
 * Summary parts of a CONCRETE agent-settings bundle keyed by WIRE field names
 * (``selected_model`` / ``thinking_enabled`` / …) — as produced by the
 * reset-stack targets (``useSessionAgentSettings.resetStack``).
 */
export function bundleSummaryParts(bundle, helpers, current = null) {
    return summaryParts(helpers, (spec) => bundle?.[spec.wireField], current)
}

/**
 * Single-line string summary of a preset's forced fields, joined by " · ", or
 * "all default" when nothing is forced. Used where a plain string is needed
 * (e.g. the presets editor dialog).
 */
export function formatPresetSummary(preset, helpers) {
    const parts = presetSummaryParts(preset, helpers)
    return parts.length === 0 ? 'all default' : parts.map(p => p.text).join(' · ')
}

/**
 * String counterpart of ``bundleSummaryParts`` (a CONCRETE bundle keyed by wire
 * field names), joined by " · ", or "all default" when nothing surfaces. Used
 * where a plain string is needed (e.g. the project "Load from…" picker).
 */
export function formatBundleSummary(bundle, helpers) {
    const parts = bundleSummaryParts(bundle, helpers)
    return parts.length === 0 ? 'all default' : parts.map(p => p.text).join(' · ')
}
