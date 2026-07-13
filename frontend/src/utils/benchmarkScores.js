/**
 * Benchmark scoring — turns the raw per-(provider, model, effort) benchmark
 * rows into a single integer score in 0..100 for each row.
 *
 * Every reference min/max (capability, log-cost, log-duration, and the final raw
 * stretch) is computed over the COMPLETE set of rows — all providers, models
 * and efforts — so a cell's score never depends on what the picker happens to
 * show (a filtered provider, hidden older models, …). Rows missing a metric,
 * or with a non-positive cost / duration / pass@4 (the formula can't take a
 * log or a fractional power of those), carry no score: the matrix renders
 * them as "?".
 *
 * The weights below are fixed constants for now (a future task may make them
 * configurable).
 */

// Metric weights (sum to 1.0)
export const W_CAPABILITY = 0.40 // capability
export const W_COST = 0.50 // cost
export const W_SPEED = 0.10 // speed

export const ALPHA = 1.4 // coverage irregularity exponent (capability metric)
export const GAMMA = 2.5 // contrast (final stretch)
export const EPS = 1e-3 // clamp floor for the [0,1] notes

/** Lookup key joining a benchmark row to a matrix cell. */
export function scoreKey(provider, model, effort) {
    return `${provider} ${model} ${effort}`
}

// Clamp a note into [EPS, 1] so its natural log stays finite and negative.
function clampNote(x) {
    return Math.max(EPS, Math.min(1, x))
}

// (value - min) / (max - min); equal bounds -> 1 (divide-by-zero guard).
function normUp(value, min, max) {
    return max > min ? (value - min) / (max - min) : 1
}

// (max - value) / (max - min): smaller value -> higher note (cheaper / faster);
// equal bounds -> 1 (divide-by-zero guard).
function normDown(value, min, max) {
    return max > min ? (max - value) / (max - min) : 1
}

// Capability metric: p1^ALPHA * p4^(1 - ALPHA).
function capabilityMetric(p1, p4) {
    return Math.pow(p1, ALPHA) * Math.pow(p4, 1 - ALPHA)
}

// A row is scorable only if all four metrics are present and finite, with
// cost / duration / pass@4 strictly positive: log10() needs cost, dur > 0 and
// p4^(1 - ALPHA) with 1 - ALPHA < 0 needs p4 > 0. (p1 may be 0; capability is then 0.)
function isScorable(r) {
    const p1 = r.pass_at_1
    const p4 = r.pass_at_4
    const cost = r.mean_cost_usd
    const dur = r.median_duration_seconds
    return (
        Number.isFinite(p1) && p1 >= 0
        && Number.isFinite(p4) && p4 > 0
        && Number.isFinite(cost) && cost > 0
        && Number.isFinite(dur) && dur > 0
    )
}

/**
 * Compute the integer score for every scorable benchmark row.
 *
 * @param {Array<object>} rows - the COMPLETE set of benchmark rows.
 * @returns {Map<string, number>} scoreKey(provider, model, effort) -> score 0..100.
 *   Only scorable rows are present; a missing key means "no benchmark data".
 */
export function computeBenchmarkScores(rows) {
    const scores = new Map()
    if (!Array.isArray(rows) || rows.length === 0) return scores

    const scorable = rows.filter(isScorable)
    if (scorable.length === 0) return scores

    // Steps 0-1: capability metric and reference min/max over the COMPLETE set.
    const capabilities = scorable.map(r => capabilityMetric(r.pass_at_1, r.pass_at_4))
    const logCosts = scorable.map(r => Math.log10(r.mean_cost_usd))
    const logDurs = scorable.map(r => Math.log10(r.median_duration_seconds))

    const capabilityMin = Math.min(...capabilities)
    const capabilityMax = Math.max(...capabilities)
    const logCostMin = Math.min(...logCosts)
    const logCostMax = Math.max(...logCosts)
    const logDurMin = Math.min(...logDurs)
    const logDurMax = Math.max(...logDurs)

    // Steps 2-3: three notes in [0,1] then their weighted geometric mean.
    const raws = scorable.map((r, i) => {
        const noteResult = clampNote(normUp(capabilities[i], capabilityMin, capabilityMax))
        const noteCost = clampNote(normDown(logCosts[i], logCostMin, logCostMax)) // cheaper -> higher
        const noteSpeed = clampNote(normDown(logDurs[i], logDurMin, logDurMax)) // faster -> higher
        return 100 * Math.exp(
            W_CAPABILITY * Math.log(noteResult)
            + W_COST * Math.log(noteCost)
            + W_SPEED * Math.log(noteSpeed),
        )
    })

    // Step 4: stretch reference, again over the COMPLETE set.
    const rawMin = Math.min(...raws)
    const rawMax = Math.max(...raws)

    // Step 5: contrast then round to an integer 0..100.
    scorable.forEach((r, i) => {
        const stretched = normUp(raws[i], rawMin, rawMax)
        const score = Math.round(100 * Math.pow(stretched, GAMMA))
        scores.set(scoreKey(r.provider, r.model, r.reasoning_effort), score)
    })

    return scores
}
