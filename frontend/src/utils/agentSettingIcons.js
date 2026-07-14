// Icon + brand tint for the boolean agent-setting flags (thinking, fast mode,
// Chrome MCP). Single source of truth shared by the settings summary strip
// (AgentSettingsSummaryView) and the switch labels (AgentSettingsSwitches), so
// a given setting shows the same glyph and colour on both surfaces.
//
// ``family`` defaults to the WA classic set; only brand glyphs (chrome) set it.
export const AGENT_SETTING_ICONS = {
    thinking_enabled: { icon: 'brain', color: 'var(--wa-color-pink-70)' },
    fast_mode: { icon: 'bolt', color: 'var(--wa-color-yellow-60)' },
    claude_in_chrome: { icon: 'chrome', family: 'brands', color: 'var(--wa-color-blue-60)' },
}
