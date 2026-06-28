// =============================================================================
// Workflow template generator — Script A, browser side.
//
//   generateTemplates(scriptText, { runs }) -> { meta, templates }
//
// A running Claude Code workflow never records which agent belongs to which
// phase until it finishes. To show a run *live*, the back matches each agent's
// prompt against "templates" — the static segments of every agent() call's
// prompt, tagged with its phase. This module produces those templates by
// *executing* the workflow script with stubbed hooks (the exact hooks the real
// engine injects), many times with randomized agent return values, and unioning
// what it observes. The back then does pure string matching (no eval) to tag
// live agents. See providers/claude_code/workflow_synthesis.py for the match.
//
// Runs in the user's browser: the workflow script is the user's own code (it
// already ran locally), the main app has no CSP, and the stubs expose only
// injected hooks + pure JS builtins (no fs/net) — so `new AsyncFunction` is the
// right tool and needs no sandbox. Validated 163/163 against 8 real runs.
// =============================================================================

const SENT = '\u0001' // dynamic-boundary marker — splits static prompt text from interpolated values
const SEED = 0x9e3779b9
const CAP = 400 // max recorded agent() calls per run (liveness guard, not security)

// eslint-disable-next-line no-new-func
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor

function makeRng(seed) {
    let a = seed >>> 0
    return () => {
        a = (a + 0x6d2b79f5) | 0
        let t = Math.imul(a ^ (a >>> 15), 1 | a)
        t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t
        return ((t ^ (t >>> 14)) >>> 0) / 4294967296
    }
}

// number that stringifies to the marker but is a real (random) number in math.
function numberish(n) {
    return { [Symbol.toPrimitive]: (h) => (h === 'string' ? SENT : n), toString: () => SENT, valueOf: () => n }
}

// permissive sentinel for no-schema (text) results.
function sentinel(d = 0) {
    return new Proxy(() => {}, {
        get(_, p) {
            if (p === Symbol.toPrimitive || p === 'toString' || p === 'valueOf') return () => SENT
            if (p === Symbol.iterator) return function* () { yield sentinel(d + 1); yield sentinel(d + 1) }
            if (p === Symbol.asyncIterator || p === 'then') return undefined
            if (p === 'length') return 2
            if (p === 'map' || p === 'flatMap' || p === 'filter') return (cb) => {
                const out = []
                if (typeof cb === 'function') for (let i = 0; i < 2; i++) { try { const r = cb(sentinel(d + 1), i, sentinel()); out.push(p === 'filter' ? sentinel(d + 1) : r) } catch { /* keep going */ } }
                return out
            }
            if (['slice', 'sort', 'flat', 'concat', 'reverse'].includes(p)) return () => [sentinel(d + 1), sentinel(d + 1)]
            if (['join', 'toFixed', 'trim', 'replace', 'padStart', 'padEnd', 'toLowerCase', 'toUpperCase', 'split'].includes(p)) return () => (p === 'split' ? [sentinel(d + 1)] : SENT)
            if (typeof p === 'symbol') return undefined
            return sentinel(d + 1)
        },
        apply: () => sentinel(d + 1),
        construct: () => sentinel(d + 1),
        has: () => true,
    })
}

function synthFromSchema(schema, rng, depth = 0) {
    if (!schema || typeof schema !== 'object' || depth > 6) return SENT
    // enum: half the time the marker (clean template), half a random member (fires
    // ===-gated branches). No dual value works: === doesn't coerce.
    if (Array.isArray(schema.enum) && schema.enum.length) return rng() < 0.5 ? SENT : schema.enum[Math.floor(rng() * schema.enum.length)]
    const alt = schema.anyOf || schema.oneOf || schema.allOf
    if (alt && alt.length) return synthFromSchema(alt[Math.floor(rng() * alt.length)], rng, depth + 1)
    let type = schema.type
    if (Array.isArray(type)) type = type.find((t) => t !== 'null') || type[0]
    if (!type) type = schema.properties ? 'object' : schema.items ? 'array' : 'string'
    switch (type) {
        case 'object': {
            const o = {}
            const props = schema.properties || {}
            for (const k of Object.keys(props)) o[k] = synthFromSchema(props[k], rng, depth + 1)
            return o
        }
        case 'array': return [synthFromSchema(schema.items || {}, rng, depth + 1), synthFromSchema(schema.items || {}, rng, depth + 1)]
        case 'boolean': return rng() < 0.5
        case 'number': case 'integer': {
            const lo = Number.isFinite(schema.minimum) ? schema.minimum : 0
            const hi = Number.isFinite(schema.maximum) ? schema.maximum : 10
            return numberish(type === 'integer' ? Math.floor(lo + rng() * (hi - lo + 1)) : lo + rng() * (hi - lo))
        }
        default: return SENT
    }
}

// Build a fresh set of stubbed hooks (and the records sink) for one run.
function makeHooks(rng, records) {
    let currentPhase = null
    let spent = 0
    const TOTAL = 1_000_000
    const agent = async (prompt, opts = {}) => {
        if (records.length >= CAP) { spent = TOTAL; return SENT }
        spent += 5000
        const phase = (opts && typeof opts === 'object' && typeof opts.phase === 'string') ? opts.phase : currentPhase
        records.push({ phase, prompt: String(prompt) })
        const schema = opts && typeof opts === 'object' ? opts.schema : null
        return schema ? synthFromSchema(schema, rng) : sentinel()
    }
    const phase = (t) => { try { currentPhase = String(t) } catch { currentPhase = null } }
    const log = () => {}
    const parallel = async (thunks) => {
        let arr; try { arr = Array.isArray(thunks) ? thunks : [...thunks] } catch { arr = [] }
        return Promise.all(arr.map((t) => { try { return Promise.resolve(typeof t === 'function' ? t() : t) } catch { return null } }))
    }
    const pipeline = async (items, ...stages) => {
        let arr; try { arr = Array.isArray(items) ? items : [...items] } catch { arr = [sentinel()] }
        if (!arr.length) arr = [sentinel()]
        const out = []
        for (let i = 0; i < arr.length; i++) {
            let cur = arr[i]
            let dead = false
            for (const st of stages) { try { cur = await st(cur, arr[i], i) } catch { dead = true; break } }
            if (!dead) out.push(cur)
        }
        return out
    }
    const workflow = async () => sentinel()
    const budget = { total: TOTAL, spent: () => spent, remaining: () => Math.max(0, TOTAL - spent) }
    const args = rng() < 0.5 ? SENT : { tokenCap: 1_000_000, question: SENT, topic: SENT }
    return [agent, phase, parallel, pipeline, log, workflow, budget, args]
}

const norm = (s) => s.replace(/\s+/g, ' ').trim()

// Run `body` (an async fn taking the 8 hooks) `runs` times with fresh stubs;
// union + dedupe the (phase, segments) it records. Mirrors the Python detector's
// expectations: segments are whitespace-normalized and >= 12 chars.
async function makeHarness(body, { runs = 100, seed = SEED } = {}) {
    const rng = makeRng(seed)
    const seen = new Set()
    const out = []
    for (let r = 0; r < runs; r++) {
        const records = []
        const hooks = makeHooks(rng, records)
        try { await body(...hooks) } catch { /* partial records before a throw are kept */ }
        for (const rec of records) {
            const segments = rec.prompt.split(SENT).map(norm).filter((s) => s.length >= 12)
            const key = `${rec.phase || '∅'}|${segments.join('¦')}`
            if (seen.has(key)) continue
            seen.add(key)
            out.push({ phase: rec.phase, segments })
        }
    }
    return out
}

// --- script → { meta, body } --------------------------------------------------

// Locate `export const meta = { ... }` and return the literal `{...}` text,
// matching braces while ignoring those inside string/template literals. `meta`
// is a pure literal per the Workflow tool spec, so this scan is sufficient.
function findMetaLiteral(src) {
    const m = /export\s+const\s+meta\s*=\s*\{/.exec(src)
    if (!m) return null
    const braceStart = src.indexOf('{', m.index)
    let depth = 0
    let quote = null
    let esc = false
    for (let i = braceStart; i < src.length; i++) {
        const c = src[i]
        if (quote) {
            if (esc) esc = false
            else if (c === '\\') esc = true
            else if (c === quote) quote = null
        } else if (c === '"' || c === "'" || c === '`') quote = c
        else if (c === '{') depth++
        else if (c === '}') { depth--; if (depth === 0) return src.slice(braceStart, i + 1) }
    }
    return null
}

function extractMeta(scriptText) {
    const literal = findMetaLiteral(scriptText)
    if (!literal) return {}
    try {
        // eslint-disable-next-line no-new-func
        return new Function(`return (${literal})`)()
    } catch {
        return {}
    }
}

function buildBody(scriptText) {
    // Lift the meta export to a local const (so the body can still reference
    // `meta`); the whole script then becomes a legal async-function body
    // (top-level await/return allowed), with the bare hooks resolving to the
    // stubbed parameters. This is exactly how the real engine runs the script.
    const body = scriptText.replace(/export\s+const\s+meta/, 'const meta')
    return new AsyncFunction('agent', 'phase', 'parallel', 'pipeline', 'log', 'workflow', 'budget', 'args', body)
}

/**
 * Generate phase-tagged prompt templates for a workflow script.
 * @returns {Promise<{ meta: object, templates: Array<{phase: string|null, segments: string[]}> }>}
 */
export async function generateTemplates(scriptText, { runs = 100 } = {}) {
    const meta = extractMeta(scriptText)
    const body = buildBody(scriptText)
    const templates = await makeHarness(body, { runs })
    return { meta, templates }
}

/** sha256 hex of `text` (UTF-8) — must match the back's hashlib.sha256(...).hexdigest(). */
export async function sha256Hex(text) {
    const buf = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text))
    return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, '0')).join('')
}
