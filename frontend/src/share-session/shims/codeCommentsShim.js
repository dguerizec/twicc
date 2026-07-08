// No code comments in read-only shares. All-zero counts, empty lists, no-op writes.
// The reused transcript/editor tree statically imports the store AND the free
// helper functions (formatComment/formatAllComments/buildCommentedPathsSet), so
// they must all resolve — as harmless stubs (never exercised: no comments exist).
import { defineStore } from 'pinia'

export const useCodeCommentsStore = defineStore('shareCodeComments', {
    getters: {
        getCommentsBySession: () => () => [],
        countBySession: () => () => 0,
        countByProjects: () => () => 0,
        countBySource: () => () => 0,
    },
    actions: { hydrateComments() {} },
})

export function formatComment() { return '' }
export function formatAllComments() { return '' }
export function buildCommentedPathsSet() { return new Set() }
