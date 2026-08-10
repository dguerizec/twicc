// frontend/src/stores/codeComments.js
// Pinia store for code comments (inline annotations on code lines).

import { toRaw } from 'vue'
import { defineStore, acceptHMRUpdate } from 'pinia'
import { getAllCodeComments, saveCodeComment, deleteCodeComment } from '../utils/codeCommentsStorage.js'
import { getLanguageFromPath } from '../utils/languages.js'

// ─── Key helpers ────────────────────────────────────────────────────────────

/**
 * Build a serialized key from the 6 comment identity fields.
 * Uses \0 as separator (invalid in file paths on all OSes).
 */
export function buildCommentKey({ projectId, sessionId, filePath, source, sourceRef, lineNumber }) {
    return `${projectId}\0${sessionId}\0${filePath}\0${source}\0${sourceRef}\0${lineNumber}`
}

/**
 * Build the compound key array for IndexedDB operations (delete, get).
 */
export function buildKeyArray({ projectId, sessionId, filePath, source, sourceRef, lineNumber }) {
    return [projectId, sessionId, filePath, source, sourceRef, lineNumber]
}

// ─── Count cache key builders ───────────────────────────────────────────────

function projectKey(c) { return c.projectId }
function sessionKey(c) { return `${c.projectId}\0${c.sessionId}` }
function sourceKey(c) { return `${c.projectId}\0${c.sessionId}\0${c.source}` }
function sourceRefKey(c) { return `${c.projectId}\0${c.sessionId}\0${c.source}\0${c.sourceRef}` }
function fileKey(c) { return `${c.projectId}\0${c.sessionId}\0${c.source}\0${c.sourceRef}\0${c.filePath}` }

const COUNT_KEY_BUILDERS = [projectKey, sessionKey, sourceKey, sourceRefKey, fileKey]

// ─── Debounce timers (module-level, not reactive) ───────────────────────────

const _debouncers = {}
const DEBOUNCE_MS = 500

// ─── Store ──────────────────────────────────────────────────────────────────

export const useCodeCommentsStore = defineStore('codeComments', {
    state: () => ({
        /**
         * All comments indexed by serialized key.
         * @type {Object<string, {projectId: string, sessionId: string, filePath: string, source: string, sourceRef: string, lineNumber: number, lineText: string, content: string, createdAt: number, updatedAt: number}>}
         */
        comments: {},

        /**
         * Cached counts at each hierarchical level.
         * Keyed by the level's composite key (built by COUNT_KEY_BUILDERS).
         * Updated incrementally on add/remove — never recomputed by iteration.
         * @type {{ byProject: Object<string,number>, bySession: Object<string,number>, bySource: Object<string,number>, bySourceRef: Object<string,number>, byFile: Object<string,number> }}
         */
        counts: {
            byProject: {},
            bySession: {},
            bySource: {},
            bySourceRef: {},
            byFile: {},
        },
    }),

    getters: {
        /**
         * Returns a function that filters comments matching a given context.
         * Usage: store.getCommentsForContext({ projectId, sessionId, filePath, source, sourceRef })
         * Returns an array of comment objects (with lineNumber and content).
         */
        getCommentsForContext: (state) => (context) => {
            if (!context) return []
            return Object.values(state.comments).filter(c =>
                c.projectId === context.projectId &&
                c.sessionId === context.sessionId &&
                c.filePath === context.filePath &&
                c.source === context.source &&
                c.sourceRef === (context.sourceRef ?? '')
            )
        },

        /** Get all comments with content for a session (across all files/sources).
         *  Only returns comments with non-empty trimmed content — used for
         *  indicators and "add to message" features. Empty textareas are still
         *  stored and displayed as widgets, but don't signal to the user. */
        getCommentsBySession: (state) => (projectId, sessionId) => {
            return Object.values(state.comments).filter(c =>
                c.projectId === projectId && c.sessionId === sessionId && c.content?.trim()
            )
        },

        // ─── Cached hierarchical count getters (O(1) lookups) ────────────

        /** Count all comments in a project. */
        countByProject: (state) => (projectId) => {
            return state.counts.byProject[projectId] || 0
        },

        /** Count all comments across multiple projects. */
        countByProjects: (state) => (projectIds) => {
            let total = 0
            for (const pid of projectIds) {
                total += state.counts.byProject[pid] || 0
            }
            return total
        },

        /** Count comments in a specific session. */
        countBySession: (state) => (projectId, sessionId) => {
            return state.counts.bySession[`${projectId}\0${sessionId}`] || 0
        },

        /** Count comments for a source tab (files/git/tool) within a session. */
        countBySource: (state) => (projectId, sessionId, source) => {
            return state.counts.bySource[`${projectId}\0${sessionId}\0${source}`] || 0
        },

        /** Count comments for a specific source reference within a session+source. */
        countBySourceRef: (state) => (projectId, sessionId, source, sourceRef) => {
            return state.counts.bySourceRef[`${projectId}\0${sessionId}\0${source}\0${sourceRef ?? ''}`] || 0
        },

        /** Count comments for a specific file within a full context. */
        countByFile: (state) => (projectId, sessionId, source, sourceRef, filePath) => {
            return state.counts.byFile[`${projectId}\0${sessionId}\0${source}\0${sourceRef ?? ''}\0${filePath}`] || 0
        },
    },

    actions: {
        // ─── Count cache management ─────────────────────────────────────

        /** Rebuild all count caches from scratch (called once at hydration).
         *  Only counts comments with non-empty trimmed content (indicators
         *  should only signal "you have content to send", not empty textareas). */
        _rebuildCounts() {
            const caches = [
                this.counts.byProject = {},
                this.counts.bySession = {},
                this.counts.bySource = {},
                this.counts.bySourceRef = {},
                this.counts.byFile = {},
            ]
            for (const comment of Object.values(this.comments)) {
                if (!comment.content?.trim()) continue
                COUNT_KEY_BUILDERS.forEach((fn, i) => {
                    const k = fn(comment)
                    caches[i][k] = (caches[i][k] || 0) + 1
                })
            }
        },

        /** Increment counts for a comment (only if it has content). */
        _incrementCounts(comment) {
            if (!comment.content?.trim()) return
            const caches = [this.counts.byProject, this.counts.bySession, this.counts.bySource, this.counts.bySourceRef, this.counts.byFile]
            COUNT_KEY_BUILDERS.forEach((fn, i) => {
                const k = fn(comment)
                caches[i][k] = (caches[i][k] || 0) + 1
            })
        },

        /** Decrement counts for a comment (only if it had content). */
        _decrementCounts(comment) {
            if (!comment.content?.trim()) return
            const caches = [this.counts.byProject, this.counts.bySession, this.counts.bySource, this.counts.bySourceRef, this.counts.byFile]
            COUNT_KEY_BUILDERS.forEach((fn, i) => {
                const k = fn(comment)
                const newVal = (caches[i][k] || 0) - 1
                if (newVal <= 0) delete caches[i][k]
                else caches[i][k] = newVal
            })
        },

        // ─── CRUD actions ───────────────────────────────────────────────

        /**
         * Hydrate the store from IndexedDB at app startup.
         */
        async hydrateComments() {
            try {
                const all = await getAllCodeComments()
                const comments = {}
                for (const comment of all) {
                    const key = buildCommentKey(comment)
                    comments[key] = comment
                }
                this.comments = comments
                this._rebuildCounts()
            } catch (err) {
                console.error('[codeComments] Failed to hydrate from IndexedDB:', err)
            }
        },

        /**
         * Add a new comment (empty content). Writes to IndexedDB immediately.
         * @param {Object} context - { projectId, sessionId, filePath, source, sourceRef }
         * @param {number} lineNumber
         * @param {string} [lineText]
         * @param {number} [displayLineNumber] - Real file line number for message formatting (patch-only mode)
         */
        addComment(context, lineNumber, lineText, displayLineNumber) {
            const commentData = {
                projectId: context.projectId,
                sessionId: context.sessionId,
                subagentSessionId: context.subagentSessionId ?? '',
                filePath: context.filePath,
                source: context.source,
                sourceRef: context.sourceRef ?? '',
                toolLineNum: context.toolLineNum ?? null,
                subagentToolLineNum: context.subagentToolLineNum ?? null,
                lineNumber,
                displayLineNumber: displayLineNumber ?? null,
                lineText: lineText ?? '',
                content: '',
                createdAt: Date.now(),
                updatedAt: Date.now(),
            }
            const key = buildCommentKey(commentData)
            if (this.comments[key]) return // already exists

            this.comments[key] = commentData
            this._incrementCounts(commentData)
            // Save the plain object (before Vue wraps it in a reactive proxy)
            // — IndexedDB's structured clone cannot handle Proxy objects.
            saveCodeComment({ ...commentData }).catch(err =>
                console.error('[codeComments] Failed to save:', err)
            )
        },

        /**
         * Update comment content. Debounces IndexedDB write.
         * @param {Object} context - { projectId, sessionId, filePath, source, sourceRef }
         * @param {number} lineNumber
         * @param {string} content
         */
        updateComment(context, lineNumber, content) {
            const key = buildCommentKey({ ...context, sourceRef: context.sourceRef ?? '', lineNumber })
            const comment = this.comments[key]
            if (!comment) return

            // Track content transition for count cache updates.
            // Decrement BEFORE content change (guard checks current content),
            // increment AFTER (guard checks new content).
            const hadContent = !!comment.content?.trim()
            const hasContent = !!content?.trim()

            if (hadContent && !hasContent) this._decrementCounts(comment)

            comment.content = content
            comment.updatedAt = Date.now()

            if (!hadContent && hasContent) this._incrementCounts(comment)

            // Debounce IndexedDB write — use toRaw() to unwrap the reactive
            // proxy before saving; IndexedDB's structured clone cannot handle Proxies.
            clearTimeout(_debouncers[key])
            _debouncers[key] = setTimeout(() => {
                const raw = toRaw(this.comments[key])
                if (raw) {
                    saveCodeComment({ ...raw }).catch(err =>
                        console.error('[codeComments] Failed to save:', err)
                    )
                }
                delete _debouncers[key]
            }, DEBOUNCE_MS)
        },

        /**
         * Remove a comment. Deletes from IndexedDB immediately.
         * @param {Object} context - { projectId, sessionId, filePath, source, sourceRef }
         * @param {number} lineNumber
         */
        removeComment(context, lineNumber) {
            const fields = { ...context, sourceRef: context.sourceRef ?? '', lineNumber }
            const key = buildCommentKey(fields)
            // Flush any pending debounced write
            clearTimeout(_debouncers[key])
            delete _debouncers[key]

            const comment = this.comments[key]
            if (comment) this._decrementCounts(comment)
            delete this.comments[key]
            deleteCodeComment(buildKeyArray(fields)).catch(err =>
                console.error('[codeComments] Failed to delete:', err)
            )
        },

        /** Remove all comments for a session. */
        removeAllSessionComments(projectId, sessionId) {
            const keys = Object.entries(this.comments)
                .filter(([, c]) => c.projectId === projectId && c.sessionId === sessionId)
                .map(([key, c]) => ({ key, comment: c, fields: { projectId: c.projectId, sessionId: c.sessionId, filePath: c.filePath, source: c.source, sourceRef: c.sourceRef, lineNumber: c.lineNumber } }))

            for (const { key, comment, fields } of keys) {
                clearTimeout(_debouncers[key])
                delete _debouncers[key]
                this._decrementCounts(comment)
                delete this.comments[key]
                deleteCodeComment(buildKeyArray(fields)).catch(err =>
                    console.error('[codeComments] Failed to delete:', err)
                )
            }
        },
    },
})

// ─── Formatting helpers ─────────────────────────────────────────────────────

/**
 * Return a backtick fence long enough to avoid conflicts with the content.
 * Scans for the longest run of consecutive backticks and uses one more.
 */
function makeFence(text) {
    let max = 0
    const re = /`+/g
    let m
    while ((m = re.exec(text)) !== null) {
        if (m[0].length > max) max = m[0].length
    }
    return '`'.repeat(Math.max(3, max + 1))
}

/**
 * Return a colon fence long enough to wrap `texts` without being closed early.
 * Same principle as makeFence: scan every line that opens with a colon run and
 * use one colon more, so quoted content containing `:::` stays inside the block.
 */
function makeContainerMarker(...texts) {
    let max = 2
    const re = /^ {0,3}(:{3,})/gm
    for (const text of texts) {
        if (!text) continue
        re.lastIndex = 0
        let m
        while ((m = re.exec(text)) !== null) {
            if (m[1].length > max) max = m[1].length
        }
    }
    return ':'.repeat(max + 1)
}

/**
 * Wrap the selected text the way its source reads best: a fenced block for
 * anything verbatim (code, diffs, terminal output — shiki highlights it when a
 * language is known), a blockquote for prose taken from a conversation.
 */
function formatQuotedSelection(text, quoteMode, lang) {
    if (quoteMode === 'quote') {
        return text.split('\n').map(line => `> ${line}`).join('\n')
    }
    const fence = makeFence(text)
    return `${fence}${lang || ''}\n${text}\n${fence}`
}

/**
 * Format a single comment for insertion into the message textarea.
 *
 * The output is a `:::` container (see utils/markdownContainers.js): a label
 * line, the quoted selection, then the user's comment as plain markdown. The
 * rendering mode is implicit — the renderer reads it from the first child block
 * — so nothing can desync between the source and the display. A blank line
 * separates the two: without it a comment following a blockquote is swallowed
 * by it (markdown's lazy continuation).
 *
 * @param {Object} comment - Comment object with lineText, content, filePath, etc.
 * @param {Object} [options]
 * @param {boolean} [options.isSelectedText=false] - If true, formats as a session text
 *   comment ("comment on selected text") instead of a file/line comment.
 * @param {string} [options.sourceLabel] - Optional source suffix (e.g. "from terminal").
 *   Only used when isSelectedText is true.
 * @param {string} [options.subject='selected text'] - What the quoted block is (e.g.
 *   "selected area" for the browser element picker). Only used when isSelectedText is true.
 * @param {'code'|'quote'} [options.quoteMode='code'] - How to wrap the selection.
 *   Only used when isSelectedText is true.
 * @param {string} [options.lang] - Language for the fence, when the caller knows it and
 *   no file path carries it (e.g. a shiki code block selected in the conversation).
 */
export function formatComment(
    comment,
    { isSelectedText = false, sourceLabel = '', subject = 'selected text', quoteMode = 'code', lang = null } = {},
) {
    const body = comment.content?.trim() || ''
    const marker = makeContainerMarker(comment.lineText, body)
    // The label's first word is the container type: a block with no comment is
    // a bare excerpt, not a comment on something.
    const verb = body ? 'comment on' : 'excerpt of'

    if (isSelectedText) {
        const suffix = sourceLabel ? ` ${sourceLabel}` : ''
        // When the selection comes from a code editor we include the source file and
        // line range so the message is unambiguous (especially for agents reading it):
        // the quoted content is only an excerpt of those lines, not the full lines.
        // When only the file path is known (e.g. selection in a rendered markdown
        // preview) we still surface it without a line range.
        let location = ''
        if (comment.filePath) {
            if (comment.lineFrom != null) {
                const range = comment.lineFrom === comment.lineTo
                    ? `line ${comment.lineFrom}`
                    : `lines ${comment.lineFrom}-${comment.lineTo}`
                location = ` from \`${comment.filePath}\` ${range}`
            } else {
                location = ` from \`${comment.filePath}\``
            }
        }
        const label = `${verb} ${subject}${location}${suffix}`
        const resolvedLang = lang || getLanguageFromPath(comment.filePath) || ''
        const quoted = formatQuotedSelection(comment.lineText, quoteMode, resolvedLang)
        return `\n${marker} ${label}\n${quoted}${body ? `\n\n${body}` : ''}\n${marker}`
    }

    const resolvedLang = lang || getLanguageFromPath(comment.filePath) || ''
    const line = comment.displayLineNumber ?? comment.lineNumber
    const label = `${verb} \`${comment.filePath}\` line ${line}`
    const quoted = formatQuotedSelection(comment.lineText, 'code', resolvedLang)
    return `\n${marker} ${label}\n${quoted}${body ? `\n\n${body}` : ''}\n${marker}`
}

/**
 * Format multiple comments for insertion into the message textarea.
 * Groups by file and sorts by line number for readability.
 */
export function formatAllComments(comments) {
    const sorted = [...comments].sort((a, b) =>
        a.filePath.localeCompare(b.filePath) || a.lineNumber - b.lineNumber
    )
    return sorted.map(c => formatComment(c)).join('\n')
}

/**
 * Build a Set of paths (files + all ancestor directories) from a list of file paths.
 * Used by file trees to show comment indicators on both files and containing folders.
 */
export function buildCommentedPathsSet(filePaths) {
    const set = new Set()
    for (const fp of filePaths) {
        set.add(fp)
        // Add all ancestor directories
        let dir = fp
        while (true) {
            const slash = dir.lastIndexOf('/')
            if (slash <= 0) break
            dir = dir.substring(0, slash)
            if (set.has(dir)) break // already added this and all parents
            set.add(dir)
        }
    }
    return set
}

if (import.meta.hot) {
    import.meta.hot.accept(acceptHMRUpdate(useCodeCommentsStore, import.meta.hot))
}
