// No code comments in read-only shares. All-zero counts, empty lists, no-op writes.
// The reused transcript/editor tree statically imports the store AND the free
// helper functions (formatComment/formatAllComments/buildCommentedPathsSet), so
// they must all resolve — as harmless stubs (never exercised: no comments exist).
import { defineStore } from 'pinia'

export const useCodeCommentsStore = defineStore('shareCodeComments', {
    getters: {
        getCommentsBySession: () => () => [],
        // Read by the reused CodeEditor/DiffEditor at setup (per-context lookup +
        // add/update/remove callbacks wired into the CodeMirror comment gutter).
        // Missing here, it threw and froze the whole viewer when a diff mounted
        // (e.g. opening a subagent whose transcript auto-expands an Edit).
        getCommentsForContext: () => () => [],
        countBySession: () => () => 0,
        countByProjects: () => () => 0,
        countBySource: () => () => 0,
    },
    // Write surface never exercised in read-only shares — no-ops so the editor
    // callbacks resolve.
    actions: {
        hydrateComments() {},
        addComment() {}, updateComment() {}, removeComment() {},
        removeAllSessionComments() {},
    },
})

export function formatComment() { return '' }
export function formatAllComments() { return '' }
export function buildCommentedPathsSet() { return new Set() }
