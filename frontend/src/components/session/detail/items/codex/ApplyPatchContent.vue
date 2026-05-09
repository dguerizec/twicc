<script setup>
import { computed } from 'vue'
import { useDataStore } from '../../../../../stores/data'
import { getParsedContent } from '../../../../../utils/parsedContent'
import { reconstructFromHunks } from '../../../../../utils/patchUtils'
import { parseApplyPatchEnvelope } from '../../../../../providers/codex/parsePatch'
import { parseUnifiedDiff } from '../../../../../providers/codex/parseUnifiedDiff'
import { formatRelativePath, fileIconFor } from '../../../../../providers/utils/path'
import ApplyPatchFileEntry from './ApplyPatchFileEntry.vue'

/**
 * Body of an ``apply_patch`` tool_use card. Mirrors Claude Code's
 * ``EditContent`` / ``WriteContent`` (a CodeMirror MergeView in a
 * ``ToolDiffViewer``), with two twists:
 *
 *  - One block per file. ``apply_patch`` can touch several paths in
 *    a single call; each gets its own header + diff viewer.
 *  - Two information sources, depending on whether the matching
 *    ``event_msg.patch_apply_end`` is loaded:
 *      * pre-result: parse the raw v4a envelope (``input``) locally.
 *        We get every path + a fragment-style diff per file (no
 *        gutter line numbers).
 *      * post-result: use ``changes[path]`` from the event payload —
 *        absolute paths, ``unified_diff`` with real line numbers for
 *        updates, full ``content`` for adds/deletes.
 *
 *    The transition is transparent (Vue reactivity through
 *    ``dataStore.sessionItems``).
 */

const props = defineProps({
    // Raw v4a envelope passed by the model (``custom_tool_call.input``).
    input: { type: String, required: true },
    // Session id of the call — drives both the result lookup and the
    // base-dir computation for relative paths.
    sessionId: { type: String, required: true },
    // ``call_id`` of the originating ``custom_tool_call``. Used to find
    // the matching ``event_msg.patch_apply_end`` line in the session.
    toolId: { type: String, required: true },
})

const dataStore = useDataStore()

const session = computed(() => dataStore.getSession(props.sessionId))
const project = computed(() => {
    const s = session.value
    return s ? dataStore.getProject(s.project_id) : null
})

const sessionBaseDir = computed(() => (
    session.value?.git_directory || session.value?.cwd || null
))

// Roots considered safe targets for the View-in-Files button. Same
// list the shell uses for its own button (see ``ToolUseContent.vue``)
// so each per-file header has the same eligibility check.
const fileTabRoots = computed(() => [
    session.value?.git_directory,
    session.value?.cwd,
    project.value?.directory,
    project.value?.git_root,
].filter(Boolean))

/**
 * Walk the session's items looking for ``event_msg.patch_apply_end``
 * with our ``call_id``. Returns the payload object or ``null``.
 *
 * Reactive on ``sessionItems`` so the diff transitions from fragment
 * mode (input parser) to full mode (real line numbers) the moment the
 * matching event row reaches the store.
 */
const patchEndPayload = computed(() => {
    const items = dataStore.sessionItems[props.sessionId]
    if (!items) return null
    for (const item of items) {
        if (!item) continue
        const parsed = getParsedContent(item)
        if (!parsed || parsed.type !== 'event_msg') continue
        const payload = parsed.payload
        if (!payload || payload.type !== 'patch_apply_end') continue
        if (payload.call_id !== props.toolId) continue
        return payload
    }
    return null
})

/**
 * Per-path lookup table built from the backend's
 * ``compute_file_change_stats`` JSON, exposed by the ``tool-states``
 * REST view as ``ToolResultLink.extra``. The base orchestration
 * computes stats once when the ``patch_apply_end`` line is recorded;
 * we just read the result here. Returns ``null`` until the link is
 * persisted (live race window or pre-result), so callers fall back
 * to the v4a parser's own counts in that case.
 */
const backendFileStatsByPath = computed(() => {
    const toolState = dataStore.getToolState(props.sessionId, props.toolId)
    if (!toolState?.extra) return null
    let parsed
    try {
        parsed = JSON.parse(toolState.extra)
    } catch {
        return null
    }
    const files = parsed?.files
    if (!Array.isArray(files)) return null
    const out = {}
    for (const f of files) {
        if (f && typeof f.path === 'string') out[f.path] = f
    }
    return out
})

/**
 * Per-file entries to render. The shape mirrors what
 * ``ApplyPatchFileEntry`` consumes.
 *
 * ``diffMode`` is one of:
 *   - 'fragment-update': pre-result update, free-form old/new strings
 *   - 'hunks-update':    post-result update, structured patch with
 *                        real line numbers
 *   - 'add':             new file body, full content
 *   - 'delete':          file body that was removed
 *   - 'pending-delete':  pre-result delete (no body to show yet)
 */
const fileEntries = computed(() => {
    const baseDir = sessionBaseDir.value
    const decorate = (path) => ({
        path,
        displayPath: formatRelativePath(path, baseDir),
        fileIconSrc: fileIconFor(path),
    })
    const backendStats = backendFileStatsByPath.value

    const payload = patchEndPayload.value
    if (payload && payload.changes && typeof payload.changes === 'object') {
        const entries = []
        for (const [path, change] of Object.entries(payload.changes)) {
            if (!change || typeof change !== 'object') continue
            // Stats from the backend's ``ToolResultLink.extra`` when
            // it has reached the store; defaulted to zero otherwise
            // (the real counts arrive on the next tick — Vue reactivity
            // re-runs this computed at that point).
            const stats = backendStats ? backendStats[path] : null
            const base = {
                ...decorate(path),
                movePath: null,
                firstModifiedLine: null,
                linesAdded: stats?.lines_added ?? 0,
                linesRemoved: stats?.lines_removed ?? 0,
            }
            if (change.type === 'update') {
                const hunks = parseUnifiedDiff(change.unified_diff || '')
                const reconstructed = hunks.length ? reconstructFromHunks(hunks) : null
                if (reconstructed) {
                    entries.push({
                        ...base,
                        movePath: change.move_path || null,
                        diffMode: 'hunks-update',
                        original: reconstructed.original,
                        modified: reconstructed.modified,
                        originalLineMap: reconstructed.originalLineMap,
                        modifiedLineMap: reconstructed.modifiedLineMap,
                        firstModifiedLine: hunks[0].newStart ?? null,
                    })
                } else {
                    // Empty diff (e.g. rename without content change).
                    entries.push({
                        ...base,
                        movePath: change.move_path || null,
                        diffMode: 'fragment-update',
                        original: '',
                        modified: '',
                    })
                }
            } else if (change.type === 'add') {
                const content = typeof change.content === 'string' ? change.content : ''
                entries.push({ ...base, diffMode: 'add', modified: content })
            } else if (change.type === 'delete') {
                const content = typeof change.content === 'string' ? change.content : ''
                entries.push({ ...base, diffMode: 'delete', original: content, modified: '' })
            }
        }
        return entries
    }

    // Pre-result fallback: parse the raw v4a envelope. The parser
    // already counts ``+`` / ``-`` lines per file, so we just forward
    // them through.
    const parsed = parseApplyPatchEnvelope(props.input)
    return parsed.map((file) => {
        const base = {
            ...decorate(file.path),
            movePath: file.movePath,
            firstModifiedLine: null,
            linesAdded: file.linesAdded ?? 0,
            linesRemoved: file.linesRemoved ?? 0,
        }
        if (file.action === 'update') {
            return {
                ...base,
                diffMode: 'fragment-update',
                original: file.oldString ?? '',
                modified: file.newString ?? '',
            }
        }
        if (file.action === 'add') {
            return {
                ...base,
                diffMode: 'add',
                modified: file.content ?? '',
            }
        }
        // Delete: v4a body is empty — we won't have the file content
        // until the result arrives. Render a placeholder block.
        return { ...base, diffMode: 'pending-delete' }
    })
})

const showFileHeader = computed(() => fileEntries.value.length > 1)
</script>

<template>
    <div class="apply-patch-content">
        <div v-if="fileEntries.length === 0" class="apply-patch-empty">
            <wa-icon name="circle-info"></wa-icon>
            Patch envelope not parseable yet.
        </div>
        <ApplyPatchFileEntry
            v-for="(entry, idx) in fileEntries"
            :key="entry.path"
            :path="entry.path"
            :display-path="entry.displayPath"
            :file-icon-src="entry.fileIconSrc"
            :move-path="entry.movePath"
            :diff-mode="entry.diffMode"
            :original="entry.original"
            :modified="entry.modified"
            :original-line-map="entry.originalLineMap"
            :modified-line-map="entry.modifiedLineMap"
            :first-modified-line="entry.firstModifiedLine"
            :lines-added="entry.linesAdded"
            :lines-removed="entry.linesRemoved"
            :file-tab-roots="fileTabRoots"
            :show-header="showFileHeader"
            :is-last="idx === fileEntries.length - 1"
        />
    </div>
</template>

<style scoped>
.apply-patch-content {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-m);
}

.apply-patch-empty {
    display: flex;
    align-items: center;
    gap: var(--wa-space-xs);
    color: var(--wa-color-text-quiet);
    font-style: italic;
    padding: var(--wa-space-xs) 0;
}
</style>
