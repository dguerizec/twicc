import { defineStore } from 'pinia'

// Mirror of a session's workflow runs (and which one is active), pushed by the
// visible WorkflowsPane. Workflow runs otherwise live only in that component;
// this lets global consumers (the command palette's "Go to … workflow" commands)
// list a session's runs, count them, and mark the current one.
export const useWorkflowRunsStore = defineStore('workflowRuns', {
    state: () => ({
        // sessionId → [{ run_id, name }] in display order (newest-first)
        runs: {},
        // sessionId → active run_id
        active: {},
    }),
    actions: {
        setRuns(sessionId, runs) {
            this.runs[sessionId] = runs
        },
        setActive(sessionId, runId) {
            this.active[sessionId] = runId
        },
        clear(sessionId) {
            delete this.runs[sessionId]
            delete this.active[sessionId]
        },
    },
    getters: {
        getRuns: (state) => (sessionId) => state.runs[sessionId] ?? [],
        getActive: (state) => (sessionId) => state.active[sessionId] ?? null,
    },
})
