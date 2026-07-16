// Detection/split of Codex Plan-mode ``<proposed_plan>`` blocks.
//
// Plan collaboration mode wraps its final plan in literal
// ``<proposed_plan>`` / ``</proposed_plan>`` tags so clients can render it
// specially. The mode's built-in instructions make the shape a stable
// contract: exact tags (never translated), each on its own line, markdown
// inside, at most one block per turn, possibly surrounded by ordinary
// assistant text.
//
// Shared by the transcript rendering (``codex/AssistantMessage.vue``) and
// the post-plan "implement in a new session" action
// (``codex/PlanImplementationBody.vue``), which re-reads the latest plan off
// the session items.

const OPEN_TAG_RE = /(?:^|\n)[ \t]*<proposed_plan>[ \t]*(?:\n|$)/
const CLOSE_TAG_RE = /(?:^|\n)[ \t]*<\/proposed_plan>[ \t]*(?:\n|$)/

/**
 * Split an assistant message around its ``<proposed_plan>`` block.
 *
 * The closing tag is optional so a streaming placeholder already renders the
 * plan while its text is still growing (the block always ends the message in
 * that case). A tag mentioned inline (not on its own line) does not trigger.
 *
 * @param {string} text - The assistant message text.
 * @returns {{before: string, plan: string, after: string} | null} The trimmed
 *   segments, or ``null`` when the message carries no plan block.
 */
export function splitProposedPlan(text) {
    if (typeof text !== 'string' || !text) return null
    const openMatch = text.match(OPEN_TAG_RE)
    if (!openMatch) return null
    const before = text.slice(0, openMatch.index)
    const rest = text.slice(openMatch.index + openMatch[0].length)
    const closeMatch = rest.match(CLOSE_TAG_RE)
    const plan = closeMatch ? rest.slice(0, closeMatch.index) : rest
    const after = closeMatch ? rest.slice(closeMatch.index + closeMatch[0].length) : ''
    return { before: before.trim(), plan: plan.trim(), after: after.trim() }
}
