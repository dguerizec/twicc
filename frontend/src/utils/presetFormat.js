// Builds the icon summary parts for an agent-settings PRESET or a concrete
// agent-settings BUNDLE, in the exact same visual vocabulary as the live
// session summary (``BaseProviderHelpers.getSummaryParts``): model + effort
// fused into one part, permission mode as a coloured glyph + label, and the
// boolean flags (thinking / fast / Chrome MCP) as coloured icons. The shared
// ``AgentSettingsSummaryView`` renders the resulting parts.
//
// The difference from the live summary is that a preset/bundle is PARTIAL — it
// only carries the fields it forces — so this builder emits a part *only* for a
// field that is actually set (present, not null/undefined), rather than always
// showing every field (dimmed when off) the way the live state does. This is
// why preset displays historically showed a plain "label: value" text summary;
// they now share the icon renderer for a consistent look across the app.
//
// The boundary between preset shape and wire shape lives here on purpose:
// presets historically use ``model`` / ``thinking`` (no suffix) while the
// session/wire format uses ``selected_model`` / ``thinking_enabled``. Other
// fields share the same name on both sides.
const PRESET_KEY_TO_WIRE = {
    model: 'selected_model',
    context_max: 'context_max',
    effort: 'effort',
    thinking: 'thinking_enabled',
    permission_mode: 'permission_mode',
    permission_mode_if_untrusted: 'permission_mode_if_untrusted',
    claude_in_chrome: 'claude_in_chrome',
    fast_mode: 'fast_mode',
}

/**
 * Core builder: rich summary parts for a CONCRETE bundle keyed by WIRE field
 * names (``selected_model`` / ``thinking_enabled`` / …). ``current`` (optional),
 * also keyed by wire field name, holds the session's effective values: when
 * provided, a part is flagged ``forced`` (the dashed underline) when the
 * bundle's value DIFFERS from the current effective one — i.e. "this is what
 * applying it would change". Fields the provider does not support, or that are
 * null/undefined in the bundle, are skipped.
 *
 * ``helpers`` is the provider's helpers instance — it drives both the
 * choice/label/icon catalogue and which fields surface (via
 * ``supportsAgentSetting``), so provider-specific fields like
 * ``claude_in_chrome`` are silently dropped for any other provider.
 */
function richSummaryParts(helpers, bundle, current) {
    const has = (wire) => bundle?.[wire] !== null && bundle?.[wire] !== undefined
    const supported = (wire) => helpers.supportsAgentSetting(wire)
    const shown = (wire) => supported(wire) && has(wire)
    const val = (wire) => bundle[wire]
    const forcedVs = (wire) => (current ? val(wire) !== current[wire] : false)
    const parts = []

    // Model (+ folded context suffix) + effort — fused into one part, exactly
    // like the live summary. The effort renders as a 5-bar level icon glued
    // after the model label (or standalone when a preset forces effort but not
    // a model).
    const showModel = shown('selected_model')
    const showEffort = shown('effort')
    const showContext = shown('context_max')
    let contextFolded = false
    if (showModel || showEffort) {
        let text = ''
        let forced = false
        if (showModel) {
            text = helpers.getSummaryModelLabel(val('selected_model'))
            forced = forcedVs('selected_model')
            // Fold the context window into the model label the way the live
            // summary does (Claude's "[1m]"); providers without the hook leave
            // it for the standalone context part below.
            if (showContext) {
                const suffix = helpers.getSummaryModelSuffix({ selected: { context_max: val('context_max') }, defaults: {} })
                if (suffix) {
                    text += suffix
                    contextFolded = true
                    forced = forced || forcedVs('context_max')
                }
            }
        }
        const part = { text, forced }
        if (showEffort) {
            const effortValue = val('effort')
            part.effortSrc = helpers.getEffortIconSrc(effortValue)
            part.effortLabel = helpers.getChoiceDisplayLabel('effort', effortValue)
                ?? helpers.getChoiceLabel('effort', effortValue) ?? String(effortValue ?? '')
            part.forced = part.forced || forcedVs('effort')
        }
        parts.push(part)
    }

    // Context window as its own part when it wasn't folded into the model
    // label (a provider without the "[1m]"-style suffix, or a preset that
    // forces context without a model).
    if (showContext && !contextFolded) {
        parts.push({
            text: helpers.getChoiceLabel('context_max', val('context_max')) ?? String(val('context_max')),
            forced: forcedVs('context_max'),
        })
    }

    // Thinking — grouped with the model + effort cluster (no separator before
    // it), matching the live summary. Coloured icon, dimmed + struck when the
    // preset forces it off.
    if (shown('thinking_enabled')) {
        const on = val('thinking_enabled') === true
        parts.push({
            field: 'thinking_enabled', on, forced: forcedVs('thinking_enabled'),
            label: on ? 'Thinking' : 'No thinking',
            groupWithPrevious: parts.length > 0,
        })
    }

    // Permission mode — label prefixed by the coloured mode glyph.
    if (shown('permission_mode')) {
        const iconInfo = helpers.getChoiceIcon('permission_mode', val('permission_mode'))
        parts.push({
            text: helpers.getChoiceLabel('permission_mode', val('permission_mode')) ?? '',
            permissionIcon: iconInfo?.icon ?? null,
            permissionColor: iconInfo?.color ?? null,
            forced: forcedVs('permission_mode'),
        })
    }

    // Permission mode for untrusted projects — a preset-only default-shaping
    // pseudo-field (no live equivalent). Same glyph vocabulary as the trusted
    // mode, marked "(untrusted)". Never diff-marked: sessions have no effective
    // value for it, so a forced flag would read as always-on.
    if (shown('permission_mode_if_untrusted')) {
        const iconInfo = helpers.getChoiceIcon('permission_mode', val('permission_mode_if_untrusted'))
        const label = helpers.getChoiceLabel('permission_mode', val('permission_mode_if_untrusted')) ?? ''
        parts.push({
            text: `${label} (untrusted)`,
            permissionIcon: iconInfo?.icon ?? null,
            permissionColor: iconInfo?.color ?? null,
            forced: false,
        })
    }

    // Fast mode.
    if (shown('fast_mode')) {
        const on = val('fast_mode') === true
        parts.push({ field: 'fast_mode', on, forced: forcedVs('fast_mode'), label: on ? 'Fast mode' : 'No fast mode' })
    }

    // Chrome MCP — kept last.
    if (shown('claude_in_chrome')) {
        const on = val('claude_in_chrome') === true
        parts.push({ field: 'claude_in_chrome', on, forced: forcedVs('claude_in_chrome'), label: on ? 'Chrome MCP' : 'No Chrome MCP' })
    }

    return parts
}

/**
 * Rich summary parts of the fields a preset forces. Preset shape uses ``model``
 * / ``thinking`` keys, converted to the wire-named bundle here. ``current``
 * (optional, wire-keyed) drives the forced dashed-underline diff-marking.
 */
export function presetSummaryParts(preset, helpers, current = null) {
    const bundle = {}
    for (const [presetKey, wire] of Object.entries(PRESET_KEY_TO_WIRE)) {
        const v = preset?.[presetKey]
        if (v !== null && v !== undefined) bundle[wire] = v
    }
    return richSummaryParts(helpers, bundle, current)
}

/**
 * Rich summary parts of a CONCRETE agent-settings bundle keyed by WIRE field
 * names (``selected_model`` / ``thinking_enabled`` / …) — as produced by the
 * resolved default bundles (e.g. the preset picker's "{provider} default"
 * entries) and the project defaults picker.
 */
export function bundleSummaryParts(bundle, helpers, current = null) {
    return richSummaryParts(helpers, bundle, current)
}
