import { getProviderLabel } from '../providers'

/**
 * Markdown rendered in place of an assistant message the provider persisted
 * with no content at all (Codex writes an ``event_msg.agent_message`` whose
 * ``message`` is an empty string; Claude can leave the content array empty).
 * Without it the transcript shows an empty bubble, which reads as a TwiCC bug.
 *
 * Italic, so it never passes for something the agent wrote. Falls back to the
 * generic ``Agent`` label when the provider is unknown (see getProviderLabel).
 *
 * @param {string|null} provider - Session provider (PROVIDER.* value).
 * @returns {string} Markdown source for the replacement text.
 */
export function emptyAssistantMessageMarkdown(provider) {
    return `*${getProviderLabel(provider)} had nothing to add here.*`
}
