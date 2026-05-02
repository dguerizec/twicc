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
]

/**
 * Single-line summary of the fields a preset forces, joined by " · ", or
 * "all default" when nothing is forced.
 *
 * The ``helpers`` argument is the provider's helpers instance — it controls
 * both the choice/label catalogue and which fields are surfaced (only fields
 * the provider declares via ``supportsAgentSetting`` make it into the
 * summary, so a Claude-only field like ``claude_in_chrome`` is silently
 * dropped for any other provider).
 */
export function formatPresetSummary(preset, helpers) {
    const parts = []
    for (const { presetKey, wireField, summaryLabel, formatValue } of PRESET_SUMMARY_FIELDS) {
        if (!helpers.supportsAgentSetting(wireField)) continue
        const value = preset[presetKey]
        if (value === null || value === undefined) continue
        parts.push(`${summaryLabel}: ${formatValue(value, helpers)}`)
    }
    return parts.length === 0 ? 'all default' : parts.join(' · ')
}
