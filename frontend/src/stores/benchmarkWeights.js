import { defineStore } from 'pinia'

/**
 * Adjustable weights for the benchmark score (the three sliders below the
 * model × effort matrix). Global, in-memory state shared by every open Agent
 * Settings panel; the benchmarks store reads ``weightFractions`` so scores
 * recompute live as the sliders move.
 *
 * The SOURCE OF TRUTH is the ``weights`` latent vector — arbitrary non-negative
 * magnitudes, NOT the % shown. ``display`` is its normalization to integers
 * summing to exactly 100 (largest-remainder rounding), written by
 * ``applyLatent``. Keeping the latents as the truth is what preserves each
 * slider's ratio through a value hitting 0/100 and makes every move reversible
 * with no rounding drift — do not collapse ``display`` into the latents.
 */

const KEYS = ['capability', 'economy', 'spd']
// Large multiplier used to drive a normalized display to exactly the available
// max without a divide-by-zero, while keeping the other latents intact.
const BIG = 1e6

// Preset profiles (displayed integer %; each sums to 100). "Balanced" is the
// default state on load and matches the previously hardcoded weights.
export const WEIGHT_PRESETS = [
    { id: 'max_quality', label: 'Max quality', capability: 80, economy: 10, spd: 10 },
    { id: 'balanced', label: 'Balanced', capability: 40, economy: 50, spd: 10 },
    { id: 'fast_cheap', label: 'Fast & cheap', capability: 20, economy: 40, spd: 40 },
    {
        id: 'no_rush',
        label: 'No rush',
        capability: 70,
        economy: 30,
        spd: 0,
        tooltip: 'Best result at the best cost — duration ignored; for a task you launch and leave running',
    },
]

const DEFAULT_PRESET = WEIGHT_PRESETS.find(p => p.id === 'balanced')

export const useBenchmarkWeightsStore = defineStore('benchmarkWeights', {
    state: () => ({
        // Latent magnitudes (source of truth). Seeded from the default preset.
        weights: { capability: DEFAULT_PRESET.capability, economy: DEFAULT_PRESET.economy, spd: DEFAULT_PRESET.spd },
        // Per-key lock (exclusive: at most one true at a time).
        locks: { capability: false, economy: false, spd: false },
        // Frozen displayed % for the locked key.
        lockVal: {},
        // 'proportional' | 'economy' | 'spd' — what to favor when Capability moves.
        priority: 'proportional',
        // Displayed integer % (always sums to 100); written by applyLatent().
        display: { capability: DEFAULT_PRESET.capability, economy: DEFAULT_PRESET.economy, spd: DEFAULT_PRESET.spd },
        // When on, the matrix auto-picks the best-scoring cell whenever the
        // weights change (as if the user clicked that square). defaultProviderOnly
        // restricts "best" to the user's default provider — only meaningful, and
        // only surfaced, when several providers are shown.
        autoSelectBest: false,
        defaultProviderOnly: false,
    }),

    getters: {
        // Effective weights as fractions summing to 1, for the score formula.
        weightFractions: (state) => ({
            capability: state.display.capability / 100,
            economy: state.display.economy / 100,
            speed: state.display.spd / 100,
        }),
        lockedSum: (state) => KEYS.reduce((s, k) => s + (state.locks[k] ? state.lockVal[k] : 0), 0),
        unlockedKeys: (state) => KEYS.filter(k => !state.locks[k]),
        anyLock: (state) => KEYS.some(k => state.locks[k]),
    },

    actions: {
        // Recompute display[*] from the latent weights: normalize the unlocked
        // keys over the room left by the locked one, integer-rounded to sum 100.
        applyLatent() {
            const avail = Math.max(0, 100 - this.lockedSum)
            const uk = this.unlockedKeys
            for (const k of KEYS) if (this.locks[k]) this.display[k] = this.lockVal[k]
            if (uk.length) {
                let sumL = uk.reduce((s, k) => s + Math.max(0, this.weights[k]), 0)
                if (sumL <= 0) {
                    for (const k of uk) this.weights[k] = 1
                    sumL = uk.length
                }
                const raw = {}
                const fl = {}
                for (const k of uk) {
                    raw[k] = avail * Math.max(0, this.weights[k]) / sumL
                    fl[k] = Math.floor(raw[k])
                }
                let rem = avail - uk.reduce((s, k) => s + fl[k], 0)
                // Largest-remainder rounding: +1 to the keys with the biggest
                // fractional part until the integers add back up to `avail`.
                const order = [...uk].sort((a, b) => (raw[b] - fl[b]) - (raw[a] - fl[a]))
                for (let i = 0; i < rem; i++) fl[order[i]] += 1
                for (const k of uk) this.display[k] = fl[k]
            }
        },

        // User dragged slider `which` to integer `v`.
        onDrag(which, v) {
            if (this.locks[which]) return
            const avail = Math.max(0, 100 - this.lockedSum)
            v = Math.min(Math.max(Math.round(v), 0), avail)
            const others = this.unlockedKeys.filter(k => k !== which)
            if (!others.length) {
                this.applyLatent()
                return
            }

            // PRIORITY split — only when moving Capability while Economy & Speed are
            // the two free others.
            if (this.priority !== 'proportional' && others.length === 2
                && others.includes('economy') && others.includes('spd')) {
                const rem = avail - v
                const fav = this.priority
                const flex = fav === 'economy' ? 'spd' : 'economy'
                const delta = rem - (this.display.economy + this.display.spd)
                const flexNew = Math.min(Math.max(this.display[flex] + Math.min(0, delta), 0), rem)
                this.weights[which] = v
                this.weights[flex] = flexNew
                this.weights[fav] = rem - flexNew
                this.applyLatent()
                return
            }

            // PROPORTIONAL (default): change ONLY this slider's latent; the
            // others keep theirs, so their ratio & magnitude survive (this is
            // what makes it reversible, no drift). Rest*v/(avail-v) sets the
            // dragged latent so its normalized display is exactly `v`; BIG
            // handles v == avail without a divide-by-zero.
            let Rest = others.reduce((s, k) => s + this.weights[k], 0)
            if (Rest <= 0) {
                for (const k of others) this.weights[k] = 1
                Rest = others.length
            }
            this.weights[which] = (v >= avail) ? Rest * BIG : (v <= 0) ? 0 : Rest * v / (avail - v)
            this.applyLatent()
        },

        // Toggle a lock — exclusive, and moves NOTHING: snapshot the current
        // displays into the latents so nothing jumps.
        toggleLock(k) {
            const cur = { capability: this.display.capability, economy: this.display.economy, spd: this.display.spd }
            if (this.locks[k]) {
                this.locks[k] = false
            } else {
                for (const x of KEYS) this.locks[x] = false
                this.locks[k] = true
                this.lockVal = { [k]: cur[k] }
            }
            for (const x of KEYS) this.weights[x] = cur[x]
            this.applyLatent()
        },

        setPriority(p) {
            this.priority = p
        },

        // Apply a preset profile — clears any lock.
        applyPreset(capability, economy, spd) {
            for (const x of KEYS) this.locks[x] = false
            this.lockVal = {}
            this.weights = { capability, economy, spd }
            this.applyLatent()
        },
    },
})
