import { defineStore } from 'pinia'
import { computeBenchmarkScores, scoreKey } from '../utils/benchmarkScores'

/**
 * Benchmark scores for the model × effort matrix (Agent Settings panel).
 *
 * Holds the COMPLETE set of benchmark rows delivered by the bootstrap payload
 * (every provider, model and effort). ``scoreLookup`` derives one integer score
 * per row over that whole dataset (see ``utils/benchmarkScores.js``), so a
 * cell's score is independent of what the picker currently shows. The matrix
 * joins on ``(provider, model, effort)`` via ``getScore`` — ``model`` being the
 * internal SDK ``full_name`` carried on each model-registry entry.
 */
export const useBenchmarksStore = defineStore('benchmarks', {
    state: () => ({
        // [{ provider, model, reasoning_effort, pass_at_1, pass_at_4,
        //    mean_cost_usd, median_duration_seconds }]
        rows: [],
    }),

    getters: {
        /** Map scoreKey(provider, model, effort) -> integer score 0..100. */
        scoreLookup: (state) => computeBenchmarkScores(state.rows),

        /** Score for a (provider, model, effort) triple, or null when absent. */
        getScore() {
            return (provider, model, effort) =>
                this.scoreLookup.get(scoreKey(provider, model, effort)) ?? null
        },
    },

    actions: {
        /** Apply the benchmark rows from the bootstrap payload. */
        applyBenchmarks(rows) {
            this.rows = Array.isArray(rows) ? rows : []
        },
    },
})
