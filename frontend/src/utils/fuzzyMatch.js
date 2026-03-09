/**
 * Fuzzy match a query against a text string.
 *
 * @param {string} query - The search query (case-insensitive)
 * @param {string} text - The text to match against
 * @returns {{ match: boolean, score: number, ranges: number[][] }}
 *   - match: whether all query chars were found in order
 *   - score: higher is better (consecutive matches, word-start matches, prefix matches)
 *   - ranges: array of [start, end] pairs (inclusive) for highlighting
 */
export function fuzzyMatch(query, text) {
  if (!query) return { match: true, score: 0, ranges: [] }
  if (!text) return { match: false, score: 0, ranges: [] }

  const queryLower = query.toLowerCase()
  const textLower = text.toLowerCase()

  const queryLen = queryLower.length
  const textLen = textLower.length

  // Quick check: all chars present in order?
  let checkIdx = 0
  for (let i = 0; i < textLen && checkIdx < queryLen; i++) {
    if (textLower[i] === queryLower[checkIdx]) checkIdx++
  }
  if (checkIdx < queryLen) return { match: false, score: 0, ranges: [] }

  // Exact prefix match — highest score, return early
  if (textLower.startsWith(queryLower)) {
    const score = queryLen * 10
    return { match: true, score, ranges: [[0, queryLen - 1]] }
  }

  // Find match positions with scoring (greedy, favoring word starts)
  const positions = []
  let score = 0
  let qi = 0
  let lastMatchIdx = -2

  for (let ti = 0; ti < textLen && qi < queryLen; ti++) {
    if (textLower[ti] === queryLower[qi]) {
      positions.push(ti)

      // Consecutive bonus
      if (ti === lastMatchIdx + 1) {
        score += 5
      }

      // Word-start bonus
      if (ti === 0 || isWordBoundary(text, ti)) {
        score += 8
      }

      // Earlier position bonus (prefer matches near the start)
      score += Math.max(0, 3 - Math.floor(ti / 5))

      lastMatchIdx = ti
      qi++
    }
  }

  if (qi < queryLen) return { match: false, score: 0, ranges: [] }

  return { match: true, score, ranges: buildRanges(positions) }
}

function isWordBoundary(text, index) {
  if (index === 0) return true
  const prev = text[index - 1]
  const curr = text[index]
  // After separator
  if (' -_/'.includes(prev)) return true
  // camelCase boundary
  if (prev === prev.toLowerCase() && curr === curr.toUpperCase() && curr !== curr.toLowerCase()) return true
  return false
}

function buildRanges(positions) {
  if (!positions.length) return []
  const ranges = []
  let start = positions[0]
  let end = positions[0]
  for (let i = 1; i < positions.length; i++) {
    if (positions[i] === end + 1) {
      end = positions[i]
    } else {
      ranges.push([start, end])
      start = positions[i]
      end = positions[i]
    }
  }
  ranges.push([start, end])
  return ranges
}
