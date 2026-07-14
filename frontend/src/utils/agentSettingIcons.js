// Icon + brand tint for the boolean agent-setting flags (thinking, fast mode,
// Chrome MCP). Single source of truth shared by the settings summary strip
// (AgentSettingsSummaryView) and the switch labels (AgentSettingsSwitches), so
// a given setting shows the same glyph and colour on both surfaces.
//
// An entry carries either ``icon`` (a WA glyph name, ``family`` defaulting to
// the classic set; only brand glyphs (chrome) set it) or ``src`` (a local
// monochrome SVG under public/, tinted via currentColor).
import { resolvePublicAssetUrl } from './publicAsset'

export const AGENT_SETTING_ICONS = {
    thinking_enabled: { src: resolvePublicAssetUrl('icons/brain-bulb.svg'), color: 'var(--wa-color-purple-60)' },
    fast_mode: { icon: 'bolt', color: 'var(--wa-color-yellow-60)' },
    claude_in_chrome: { icon: 'chrome', family: 'brands', color: 'var(--wa-color-blue-60)' },
}
