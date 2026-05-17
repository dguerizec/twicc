/**
 * Vue directive for highlighting search terms in DOM text content.
 *
 * Usage: v-highlight="['term1', 'term2']"
 *
 * Walks all text nodes inside the element and wraps case-insensitive matches
 * in <mark class="search-highlight"> elements. Safe to use alongside v-html:
 * highlights are cleared and re-applied on every Vue update cycle.
 *
 * Matching mirrors Tantivy's "twicc" analyzer (see search.py) to keep
 * frontend highlights in sync with backend matches:
 * - Diacritics are folded (e.g. searching "cafe" highlights "café") via
 *   Unicode NFD decomposition + combining mark stripping. Doesn't cover
 *   ligatures like "ß" → "ss", but covers the common accent cases.
 * - Word boundaries use \p{L}/\p{N} property escapes (Unicode-aware,
 *   matches UAX #29 used by Tantivy's SimpleTokenizer), not \b which only
 *   knows [a-zA-Z0-9_]. So underscores act as separators and accented or
 *   non-Latin letters are treated as word characters.
 * - When a search term itself starts or ends with a non-word character
 *   (e.g. "web_", ".txt"), the corresponding word-boundary lookaround is
 *   dropped on that side: the user explicitly typed a delimiter there, so
 *   requiring a second one beyond it would never match.
 */

function escapeRegex(str) {
    return str.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

const WORD_CHAR = /[\p{L}\p{N}]/u
const WORD_BOUNDARY_LB = '(?<=^|[^\\p{L}\\p{N}])'
const WORD_BOUNDARY_LA = '(?=$|[^\\p{L}\\p{N}])'

/**
 * Strip diacritics and lowercase. Mirrors Tantivy's ascii_fold + lowercase
 * for the common case (precomposed accents); ligatures aren't handled.
 */
function normalize(str) {
    return str.normalize('NFD').replace(/\p{M}/gu, '').toLowerCase()
}

/**
 * Normalize a text while building a map from normalized → original indices,
 * so we can run the regex on the diacritic-free version but still highlight
 * the original (accented) characters at the correct positions.
 *
 * Built char-by-char: each original char contributes 0+ normalized chars
 * (e.g. a standalone combining mark contributes 0; "é" in NFC contributes 1
 * ("e"), since NFD splits it into "e" + mark and the mark is stripped).
 */
function normalizeWithMapping(text) {
    let normalized = ''
    const map = []
    for (let i = 0; i < text.length; i++) {
        const normChar = normalize(text[i])
        for (let j = 0; j < normChar.length; j++) {
            map.push(i)
        }
        normalized += normChar
    }
    return { normalized, map }
}

/**
 * Build the regex source for a single normalized term, with conditional
 * word-boundary lookarounds (see directive-level comment).
 */
function buildTermPattern(term) {
    if (!term) return ''
    const lb = WORD_CHAR.test(term[0]) ? WORD_BOUNDARY_LB : ''
    const la = WORD_CHAR.test(term[term.length - 1]) ? WORD_BOUNDARY_LA : ''
    return `${lb}${escapeRegex(term)}${la}`
}

function clearHighlights(el) {
    const marks = el.querySelectorAll('mark.search-highlight')
    if (marks.length === 0) return
    for (const mark of marks) {
        mark.replaceWith(document.createTextNode(mark.textContent))
    }
    el.normalize()
}

function applyHighlights(el, terms) {
    if (!terms || !terms.length) return

    const normalizedTerms = terms.map(normalize).filter(Boolean)
    if (!normalizedTerms.length) return

    const pattern = normalizedTerms.map(buildTermPattern).filter(Boolean).join('|')
    if (!pattern) return

    const regex = new RegExp(pattern, 'gu')

    // Collect all text nodes first to avoid live-mutation issues
    const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT)
    const textNodes = []
    while (walker.nextNode()) {
        textNodes.push(walker.currentNode)
    }

    for (const textNode of textNodes) {
        const text = textNode.textContent
        if (!text.trim()) continue

        const { normalized, map } = normalizeWithMapping(text)

        regex.lastIndex = 0
        if (!regex.test(normalized)) continue

        regex.lastIndex = 0
        const frag = document.createDocumentFragment()
        let lastOriginalIdx = 0
        let match

        while ((match = regex.exec(normalized)) !== null) {
            const normStart = match.index
            const normEnd = match.index + match[0].length
            const originalStart = map[normStart]
            // map[normEnd] is undefined when the match runs to the end of the
            // normalized string; fall back to the original text length.
            const originalEnd = normEnd < map.length ? map[normEnd] : text.length

            if (originalStart > lastOriginalIdx) {
                frag.appendChild(document.createTextNode(text.slice(lastOriginalIdx, originalStart)))
            }
            // Highlight the *original* slice so accents stay intact visually
            const mark = document.createElement('mark')
            mark.className = 'search-highlight'
            mark.textContent = text.slice(originalStart, originalEnd)
            frag.appendChild(mark)
            lastOriginalIdx = originalEnd

            // Defensive: zero-length matches would loop forever
            if (match[0].length === 0) regex.lastIndex++
        }

        if (lastOriginalIdx < text.length) {
            frag.appendChild(document.createTextNode(text.slice(lastOriginalIdx)))
        }

        textNode.replaceWith(frag)
    }
}

function highlight(el, terms) {
    clearHighlights(el)
    if (terms && terms.length) {
        applyHighlights(el, terms)
    }
}

export const vHighlight = {
    mounted(el, binding) {
        if (binding.value && binding.value.length) {
            highlight(el, binding.value)
        }
    },
    updated(el, binding) {
        highlight(el, binding.value || [])
    },
    beforeUnmount(el) {
        clearHighlights(el)
    },
}
