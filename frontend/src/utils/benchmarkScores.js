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
 * The three weights are adjustable at runtime (see stores/benchmarkWeights.js);
 * DEFAULT_WEIGHTS is the "Balanced" fallback. ALPHA and GAMMA stay fixed.
 */

// Default score weights (fractions summing to 1) — the "Balanced" profile.
// Adjustable at runtime; used as the fallback when no weights are supplied.
export const DEFAULT_WEIGHTS = { capability: 0.40, economy: 0.50, speed: 0.10 }

export const ALPHA = 1.4 // coverage irregularity exponent (capability metric)
export const GAMMA = 2.5 // contrast (final stretch)
export const EPS = 1e-3 // clamp floor for the [0,1] notes

/** Lookup key joining a benchmark row to a matrix cell. */
export function scoreKey(provider, model, effort) {
    return `${provider} ${model} ${effort}`
}

/**
 * Human-facing detail rows for a matrix cell's "Benchmark data" panel: our own
 * computed ``score`` (the cell's number) first, then the metrics derived from the
 * raw benchmark row (pass rates are fractions in 0..1). Rates render as integer
 * %, costs as $ with 2 decimals, time in minutes with 1 decimal; a missing metric
 * or a zero divisor renders as an em dash.
 *
 * @param {{pass_at_1:number, pass_at_4:number, mean_cost_usd:number,
 *          median_duration_seconds:number}} row
 * @param {number|null} [score] the cell's computed score (0..100).
 * @returns {{label:string, value:string, description:string}[]}
 */
export function formatBenchmarkDetails(row, score = null) {
    const DASH = '—'
    const pct = (x) => (Number.isFinite(x) ? `${Math.round(x * 100)}%` : DASH)
    const usd = (x) => (Number.isFinite(x) ? `$${x.toFixed(2)}` : DASH)
    const p1 = row?.pass_at_1
    const p4 = row?.pass_at_4
    const cost = row?.mean_cost_usd
    const dur = row?.median_duration_seconds
    return [
        {
            label: 'Score',
            value: score != null ? String(score) : DASH,
            description: 'Based on our own calculation from the metrics below.',
        },
        {
            label: 'Success rate',
            value: pct(p1),
            description: 'How often a single attempt solves the task.',
        },
        {
            label: 'Coverage',
            value: pct(p4),
            description: "Share of tasks solved at least once across the benchmark's 4 attempts.",
        },
        {
            label: 'Consistency',
            value: (Number.isFinite(p1) && Number.isFinite(p4) && p4 > 0) ? pct(p1 / p4) : DASH,
            description: 'On the tasks it can solve, how reliably it lands them per attempt.',
        },
        {
            label: 'Cost / task',
            value: usd(cost),
            description: 'Average cost of one attempt.',
        },
        {
            label: 'Cost per solve',
            value: (Number.isFinite(cost) && Number.isFinite(p1) && p1 > 0) ? usd(cost / p1) : DASH,
            description: 'Expected cost to actually get one task solved — it pays for failed attempts too, so it runs higher than Cost / task. Lower is better.',
        },
        {
            label: 'Typical time',
            value: Number.isFinite(dur) ? `${(dur / 60).toFixed(1)} min` : DASH,
            description: 'Median duration of one attempt.',
        },
    ]
}

// Clamp a note into [EPS, 1] so its natural log stays finite and negative.
function clampNote(x) {
    return Math.max(EPS, Math.min(1, x))
}

// Normalize the three weights to fractions summing to 1 (the geometric mean
// needs weights summing to 1). Defensive: the caller's displayed weights
// already sum to 100, but a zero/negative total falls back to the defaults.
function normalizeWeights(w) {
    const capability = Math.max(0, w?.capability ?? 0)
    const economy = Math.max(0, w?.economy ?? 0)
    const speed = Math.max(0, w?.speed ?? 0)
    const total = capability + economy + speed
    if (total <= 0) return DEFAULT_WEIGHTS
    return { capability: capability / total, economy: economy / total, speed: speed / total }
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
 * @param {{capability:number,economy:number,speed:number}} [weights] - metric
 *   weights (any non-negative magnitudes; normalised to sum 1). Defaults to
 *   DEFAULT_WEIGHTS.
 * @returns {Map<string, number>} scoreKey(provider, model, effort) -> score 0..100.
 *   Only scorable rows are present; a missing key means "no benchmark data".
 */
export function computeBenchmarkScores(rows, weights = DEFAULT_WEIGHTS) {
    const scores = new Map()
    if (!Array.isArray(rows) || rows.length === 0) return scores

    const scorable = rows.filter(isScorable)
    if (scorable.length === 0) return scores

    const { capability: wCapability, economy: wEconomy, speed: wSpeed } = normalizeWeights(weights)

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
        const noteEconomy = clampNote(normDown(logCosts[i], logCostMin, logCostMax)) // cheaper cost -> higher economy
        const noteSpeed = clampNote(normDown(logDurs[i], logDurMin, logDurMax)) // faster -> higher
        return 100 * Math.exp(
            wCapability * Math.log(noteResult)
            + wEconomy * Math.log(noteEconomy)
            + wSpeed * Math.log(noteSpeed),
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
