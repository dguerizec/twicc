import { getModelLabel as getClaudeCodeModelLabel } from '../providers/claude_code/constants'
import { claudeCodeHelpers } from '../providers/claude_code/helpers'

/**
 * Returns a single-line summary of the fields a preset forces, joined by " · ",
 * or "all default" when nothing is forced.
 *
 * Preset shape uses ``thinking`` (not ``thinking_enabled``) — the wire-name
 * boundary lives at the lookup site below.
 */
export function formatPresetSummary(preset) {
    const parts = []
    if (preset.model !== null && preset.model !== undefined) {
        parts.push(`model: ${getClaudeCodeModelLabel(preset.model)}`)
    }
    if (preset.context_max !== null && preset.context_max !== undefined) {
        parts.push(`context: ${claudeCodeHelpers.getChoiceLabel('context_max', preset.context_max) ?? preset.context_max}`)
    }
    if (preset.effort !== null && preset.effort !== undefined) {
        parts.push(`effort: ${claudeCodeHelpers.getChoiceLabel('effort', preset.effort) ?? preset.effort}`)
    }
    if (preset.thinking !== null && preset.thinking !== undefined) {
        parts.push(`thinking: ${claudeCodeHelpers.getChoiceLabel('thinking_enabled', preset.thinking) ?? preset.thinking}`)
    }
    if (preset.permission_mode !== null && preset.permission_mode !== undefined) {
        parts.push(`permission: ${claudeCodeHelpers.getChoiceLabel('permission_mode', preset.permission_mode) ?? preset.permission_mode}`)
    }
    if (preset.claude_in_chrome !== null && preset.claude_in_chrome !== undefined) {
        parts.push(`chrome: ${claudeCodeHelpers.getChoiceLabel('claude_in_chrome', preset.claude_in_chrome) ?? preset.claude_in_chrome}`)
    }
    return parts.length === 0 ? 'all default' : parts.join(' · ')
}
