<script setup>
/**
 * FileTree - Recursive tree component for displaying directory structures.
 *
 * Displays a nested file/directory tree with file-type-specific icons from
 * vscode-icons (served via the Iconify CDN).
 *
 * Directories can be opened/closed by clicking on them. The root directory
 * is always open and cannot be closed. Non-root directories start closed.
 * Closing a parent does NOT cascade to children — they keep their own state,
 * so reopening a parent restores the previous expanded layout.
 *
 * Directories with `loaded: false` are lazy-loaded: their children are fetched
 * from the backend on first open.
 *
 * Compact folders: when a directory's only child is another directory, they are
 * visually merged into a single node (e.g. "src/utils/helpers"). The icon and
 * toggle apply to the last directory in the chain. This stops at `loaded: false`
 * boundaries since we can't know what's inside yet.
 *
 * Props:
 *   node: { name, type, loaded?, children? } — the tree node data (mutated on lazy-load)
 *   path: absolute filesystem path of this node (used for lazy-load API calls)
 *   projectId: project ID (used for lazy-load API calls)
 *   sessionId: session ID (used for lazy-load API calls, scoped to the session)
 *   depth: nesting depth (used for indentation), defaults to 0
 *   isRoot: whether this is the root node (always open), defaults to false
 *   allOpen: if true, all directories are forced open (used for search results)
 *   focusedPath: the path of the currently keyboard-focused node (managed by parent)
 *   revealedPaths: Set of absolute paths that should be forced open (for reveal-in-tree)
 *   selectedPath: absolute path of the selected file (highlights file + ancestor dirs)
 *
 * Events:
 *   select(path): emitted when a file is activated (click or Enter/Space)
 *   focus(path): emitted when any node is clicked (file or directory), for focus tracking
 */

import { ref, computed, watch, useId } from 'vue'
import { apiFetch } from '../../utils/api'
import { getIconUrl, getFileIconId, getFolderIconId } from '../../utils/fileIcons'
import CodeCommentsIndicator from '../ui/CodeCommentsIndicator.vue'
import AppTooltip from '../ui/AppTooltip.vue'

const props = defineProps({
    node: {
        type: Object,
        required: true,
    },
    path: {
        type: String,
        required: true,
    },
    projectId: {
        type: String,
        default: null,
    },
    sessionId: {
        type: String,
        default: null,
    },
    depth: {
        type: Number,
        default: 0,
    },
    isRoot: {
        type: Boolean,
        default: false,
    },
    // Display label for the root node only (defaults to its path). Lets callers
    // show a friendly name (e.g. "Artifacts") without altering the real path.
    rootLabel: {
        type: String,
        default: null,
    },
    allOpen: {
        type: Boolean,
        default: false,
    },
    focusedPath: {
        type: String,
        default: null,
    },
    extraQuery: {
        type: String,
        default: '',
    },
    revealedPaths: {
        type: Set,
        default: () => new Set(),
    },
    selectedPath: {
        type: String,
        default: null,
    },
    isDraft: {
        type: Boolean,
        default: false,
    },
    mode: {
        type: String,
        default: 'files',  // 'files' | 'git'
    },
    directoriesOnly: {
        type: Boolean,
        default: false,
    },
    compactFolders: {
        type: Boolean,
        default: true,
    },
    lazyLoadFn: {
        type: Function,
        default: null,
    },
    /** Set of absolute paths (files + ancestor dirs) that have code comments. */
    commentedPaths: {
        type: Set,
        default: () => new Set(),
    },
})

const emit = defineEmits(['select', 'focus', 'context-menu'])

// API prefix: project-level for drafts, session-level otherwise
const apiPrefix = computed(() => {
    if (props.isDraft) {
        return `/api/projects/${props.projectId}`
    }
    return `/api/projects/${props.projectId}/sessions/${props.sessionId}`
})

/**
 * Compact folders: walk down single-child directory chains.
 *
 * Returns { displayName, effectiveNode, effectivePath } where:
 * - displayName: combined name like "A/B/C"
 * - effectiveNode: the last directory node in the chain (owns the children)
 * - effectivePath: the absolute path to that last directory
 *
 * Stops compacting when:
 * - the node is not a directory
 * - the directory has != 1 child
 * - the single child is not a directory
 * - the directory has loaded: false (not yet fetched)
 * - the node is the root (root is never compacted with its children)
 */
/**
 * Get children relevant for compact folder logic.
 * In directoriesOnly mode, only directory children are considered.
 */
function getCompactableChildren(node) {
    if (!node.children) return []
    if (props.directoriesOnly) {
        return node.children.filter(c => c.type === 'directory')
    }
    return node.children
}

const compact = computed(() => {
    if (props.node.type !== 'directory' || props.isRoot || !props.compactFolders) {
        const displayName = props.isRoot ? (props.rootLabel || props.path) : props.node.name
        return { displayName, effectiveNode: props.node, effectivePath: props.path }
    }

    let current = props.node
    let currentPath = props.path
    const nameParts = [current.name]

    while (true) {
        const children = getCompactableChildren(current)
        if (
            current.type !== 'directory' ||
            current.loaded === false ||
            children.length !== 1 ||
            children[0].type !== 'directory'
        ) break
        current = children[0]
        currentPath = `${currentPath}/${current.name}`
        nameParts.push(current.name)
    }

    return { displayName: nameParts.join('/'), effectiveNode: current, effectivePath: currentPath }
})

/**
 * Visible children of the effective node.
 * In directoriesOnly mode, file nodes are filtered out.
 */
const visibleChildren = computed(() => {
    const children = compact.value.effectiveNode.children
    if (!children) return []
    if (props.directoriesOnly) {
        return children.filter(child => child.type === 'directory')
    }
    return children
})

// Directories: root and allOpen start open, others start closed.
// In git mode, all directories start open (the tree is fully loaded and small).
// Also open if this node's effective path is in the revealedPaths set (for reveal-in-tree).
const isOpen = ref(
    props.isRoot || props.allOpen || props.mode === 'git' || (props.node.type === 'directory' && props.revealedPaths.has(compact.value.effectivePath))
)
const isLoading = ref(false)

// React to revealedPaths changes: open this node if its effective path appears in the set
watch(
    () => props.revealedPaths,
    (paths) => {
        if (props.node.type === 'directory' && paths.has(compact.value.effectivePath)) {
            isOpen.value = true
        }
    }
)

function handleClick() {
    emit('focus', nodePath.value)
    if (props.node.type === 'directory') {
        toggleOpen()
        // In directoriesOnly mode, clicking a directory also selects it
        if (props.directoriesOnly) {
            emit('select', compact.value.effectivePath)
        }
    } else {
        emit('select', props.path)
    }
}

async function toggleOpen() {
    if (props.node.type !== 'directory') return

    const { effectiveNode, effectivePath } = compact.value

    // If opening a not-yet-loaded directory, fetch its children first.
    // In git mode all data is already loaded, so skip the API call.
    if (!isOpen.value && effectiveNode.loaded === false && props.mode !== 'git') {
        isLoading.value = true
        try {
            let data
            if (props.lazyLoadFn) {
                data = await props.lazyLoadFn(effectivePath)
            } else {
                const res = await apiFetch(
                    `${apiPrefix.value}/directory-tree/?path=${encodeURIComponent(effectivePath)}${props.extraQuery}`
                )
                if (res.ok) {
                    data = await res.json()
                }
            }
            if (data) {
                // Mutate the node in-place: inject fetched children
                effectiveNode.children = data.children || []
                effectiveNode.loaded = true
            }
        } catch {
            // Silently fail — the folder just won't open
        } finally {
            isLoading.value = false
        }
    }

    isOpen.value = !isOpen.value
}

const iconUrl = computed(() => {
    const { effectiveNode } = compact.value
    if (effectiveNode.type === 'directory') {
        return getIconUrl(getFolderIconId(effectiveNode.name, isOpen.value))
    }
    return getIconUrl(getFileIconId(effectiveNode.name))
})

/**
 * Build the absolute path for a child node.
 */
function childPath(childName) {
    return `${compact.value.effectivePath}/${childName}`
}

/**
 * The effective path of this node (after compact resolution).
 * Used as the data-path attribute for keyboard navigation.
 */
const nodePath = computed(() => compact.value.effectivePath)

/**
 * Whether this node is the keyboard-focused node.
 */
const isFocused = computed(() => props.focusedPath === nodePath.value)

/**
 * Whether this node is on the selected file's path.
 * True for the selected file itself AND all its ancestor directories.
 * A directory is "on the path" if the selected file's absolute path starts
 * with this node's effective path followed by a "/".
 */
const isSelected = computed(() => {
    if (!props.selectedPath) return false
    const ep = nodePath.value
    return props.selectedPath === ep || props.selectedPath.startsWith(ep + '/')
})

/**
 * Map a normalized git status to its badge letter and CSS colour class (by change type).
 */
const STATUS_MAP = {
    modified:  { letter: 'M', cls: 'git-badge-modified' },
    added:     { letter: 'A', cls: 'git-badge-added' },
    deleted:   { letter: 'D', cls: 'git-badge-deleted' },
    renamed:   { letter: 'R', cls: 'git-badge-renamed' },
    copied:    { letter: 'C', cls: 'git-badge-added' },
    untracked: { letter: 'U', cls: 'git-badge-untracked' },
}

/** Human label for the unmerged (conflict) two-letter porcelain codes. */
const CONFLICT_LABELS = {
    DD: 'both deleted',
    AU: 'added by us',
    UD: 'deleted by them',
    UA: 'added by them',
    DU: 'deleted by us',
    AA: 'both added',
    UU: 'both modified',
}

/** One slot of the staged|unstaged code: a type-coloured letter, or a dim placeholder. */
function badgeSlot(status) {
    if (!status) return { ch: '·', cls: 'git-badge-slot-empty' }
    const entry = STATUS_MAP[status]
    return entry
        ? { ch: entry.letter, cls: entry.cls }
        : { ch: status[0].toUpperCase(), cls: 'git-badge-modified' }
}

const capitalize = (s) => (s ? s.charAt(0).toUpperCase() + s.slice(1) : s)

/** Stable per-node id so the git badge can anchor an AppTooltip. */
const gitBadgeId = useId()

/** Per-file +added / −removed line counts (git index view only), or null. */
const diffStat = computed(() => {
    if (props.mode !== 'git' || props.node.type !== 'file') return null
    const add = props.node.additions
    const del = props.node.deletions
    const a = typeof add === 'number' && add > 0 ? add : null
    const d = typeof del === 'number' && del > 0 ? del : null
    if (a === null && d === null) return null
    return { additions: a, deletions: d }
})

/**
 * Git status badge for files in git mode.
 *
 * Renders the two-column porcelain status (X = staged, Y = unstaged) as a
 * two-letter code, each letter coloured by its change type, with a dim "·" for
 * an empty side — so staged vs unstaged reads from position, like `git status`.
 * Untracked → "??"; conflicts → the raw unmerged code in a dedicated colour.
 * Commit files keep a single status letter. A tooltip spells the code out.
 *
 * Returns `{ parts: [{ ch, cls? }], tooltip, conflict? }` or null.
 */
const gitBadge = computed(() => {
    if (props.mode !== 'git' || props.node.type !== 'file') return null

    const node = props.node

    // Commit files: a single status letter.
    if (node.status) {
        const entry = STATUS_MAP[node.status] || { letter: node.status[0].toUpperCase(), cls: 'git-badge-modified' }
        return { parts: [{ ch: entry.letter, cls: entry.cls }], tooltip: capitalize(node.status) }
    }

    // Conflicted (unmerged) files: surface the raw XY code in the conflict colour.
    if (node.conflicted) {
        const code = node.conflict_code || 'UU'
        return {
            conflict: true,
            parts: [{ ch: code[0] }, { ch: code[1] }],
            tooltip: 'Conflict — ' + (CONFLICT_LABELS[code] || 'unmerged'),
        }
    }

    // Index files: staged_status (X) / unstaged_status (Y).
    const staged = node.staged_status
    const unstaged = node.unstaged_status
    if (!staged && !unstaged) return null

    // Untracked: git shows "??".
    if (unstaged === 'untracked' && !staged) {
        return {
            parts: [{ ch: '?', cls: 'git-badge-untracked' }, { ch: '?', cls: 'git-badge-untracked' }],
            tooltip: 'Untracked',
        }
    }

    const tip = []
    if (staged) tip.push(`Staged: ${staged}`)
    if (unstaged) tip.push(`Unstaged: ${unstaged}`)
    return {
        parts: [badgeSlot(staged), badgeSlot(unstaged)],
        tooltip: tip.join(' · '),
    }
})

const commentCount = computed(() => {
    if (props.commentedPaths.size === 0) return 0
    return props.commentedPaths.has(nodePath.value) ? 1 : 0
})

// ─── Context menu (right-click + long press for iOS) ────────────────────────

function handleContextMenu(event) {
    event.preventDefault()
    emitContextMenu(event.clientX, event.clientY)
}

function emitContextMenu(x, y) {
    const effectiveNode = compact.value.effectiveNode
    const node = effectiveNode.type ? effectiveNode : props.node
    emit('context-menu', {
        path: nodePath.value,
        name: node.name || props.node.name,
        type: node.type || props.node.type,
        x,
        y,
        stagedStatus: node.staged_status || null,
        unstagedStatus: node.unstaged_status || null,
        status: node.status || null,
    })
}

let longPressTimer = null
let longPressTriggered = false

function onTouchStart(event) {
    if (event.touches.length !== 1) return
    longPressTriggered = false
    const touch = event.touches[0]
    const x = touch.clientX
    const y = touch.clientY
    longPressTimer = setTimeout(() => {
        longPressTriggered = true
        emitContextMenu(x, y)
    }, 500)
}

function onTouchMove() {
    clearTimeout(longPressTimer)
    longPressTimer = null
}

function onTouchEnd(event) {
    clearTimeout(longPressTimer)
    longPressTimer = null
    if (longPressTriggered) {
        event.preventDefault()
    }
}
</script>

<template>
    <div class="file-tree-node" :class="{ 'is-root': isRoot }">
        <!-- Node label -->
        <div
            class="node-label"
            :class="[
                node.type === 'directory' ? 'is-directory' : 'is-file',
                { 'is-toggle': node.type === 'directory' },
                { 'is-clickable': node.type === 'file' },
                { 'is-focused': isFocused },
                { 'is-selected': isSelected },
                { 'has-git-badge': gitBadge },
            ]"
            :style="{ '--level': depth }"
            :data-path="nodePath"
            :data-type="node.type"
            :data-open="node.type === 'directory' ? (isOpen ? 'true' : 'false') : undefined"
            role="treeitem"
            :tabindex="isFocused ? 0 : -1"
            @click="handleClick"
            @contextmenu="handleContextMenu"
            @touchstart.passive="onTouchStart"
            @touchmove.passive="onTouchMove"
            @touchend="onTouchEnd"
        >
            <!-- Loading spinner (replaces icon while fetching) -->
            <wa-spinner v-if="isLoading" class="node-spinner"></wa-spinner>
            <img
                v-else
                :src="iconUrl"
                :alt="compact.displayName"
                class="node-icon"
                loading="lazy"
                width="16"
                height="16"
            />
            <span class="node-name">{{ compact.displayName }}</span>
            <CodeCommentsIndicator :count="commentCount" :show-tooltip="false" class="comment-badge" />
            <span v-if="gitBadge" class="git-meta">
                <span v-if="diffStat" class="git-diffstat">
                    <span v-if="diffStat.additions !== null" class="diffstat-add">+{{ diffStat.additions }}</span>
                    <span v-if="diffStat.deletions !== null" class="diffstat-del">−{{ diffStat.deletions }}</span>
                </span>
                <span
                    :id="gitBadgeId"
                    class="git-badge"
                    :class="{ 'git-badge-conflict': gitBadge.conflict }"
                ><span v-for="(part, i) in gitBadge.parts" :key="i" :class="part.cls">{{ part.ch }}</span></span>
            </span>
            <AppTooltip v-if="gitBadge" :for="gitBadgeId">{{ gitBadge.tooltip }}</AppTooltip>
        </div>

        <!-- Children (only rendered when directory is open) -->
        <div
            v-if="isOpen && compact.effectiveNode.type === 'directory' && visibleChildren.length"
            class="node-children"
            role="group"
        >
            <FileTree
                v-for="child in visibleChildren"
                :key="child.name"
                :node="child"
                :path="childPath(child.name)"
                :project-id="projectId"
                :session-id="sessionId"
                :depth="depth + 1"
                :all-open="allOpen"
                :focused-path="focusedPath"
                :extra-query="extraQuery"
                :revealed-paths="revealedPaths"
                :selected-path="selectedPath"
                :is-draft="isDraft"
                :mode="mode"
                :directories-only="directoriesOnly"
                :compact-folders="compactFolders"
                :lazy-load-fn="lazyLoadFn"
                :commented-paths="commentedPaths"
                @select="(path) => emit('select', path)"
                @focus="(path) => emit('focus', path)"
                @context-menu="(data) => emit('context-menu', data)"
            />
        </div>
    </div>
</template>

<style scoped>
.file-tree-node {
    user-select: none;
    font-size: var(--wa-font-size-m);
    display: flex;
    flex-direction: column;
    align-items: stretch;
    /* Only way I found to have the git badges stick on the right regardless of the width of the node content */
    width: 1000%;
}

.node-children {
    display: flex;
    flex-direction: column;
    align-items: stretch;
}

.node-label {
    display: flex;
    align-items: center;
    gap: var(--wa-space-2xs);
    padding: var(--wa-space-3xs) var(--wa-space-xs) var(--wa-space-3xs) calc(var(--level) * var(--wa-space-m) + var(--wa-space-2xs));
    line-height: 1.6;
    cursor: default;
    white-space: nowrap;
    width: fit-content;
    min-width: 100%;
    outline: none;
    position: relative;
    --node-bg-color: var(--wa-color-surface-default);
    background-color: var(--node-bg-color);
}

.node-label:hover {
    --node-bg-color: var(--wa-color-surface-raised);
}

.node-label.is-selected .node-name {
    text-decoration: underline;
}

.node-label.is-selected:hover {
    --node-bg-color: var(--wa-color-surface-lowered);
}

.node-label.is-focused {
    --node-bg-color: var(--wa-color-surface-lowered);
}

.node-label.is-toggle,
.node-label.is-clickable {
    cursor: pointer;
}

.node-label.is-directory {
    color: var(--wa-color-text-normal);
    font-weight: 500;
}

.node-label.is-file {
    color: var(--wa-color-text-quiet);
}

.node-icon {
    flex-shrink: 0;
    width: var(--wa-space-m);
    height: var(--wa-space-m);
}

.node-spinner {
    flex-shrink: 0;
    font-size: var(--wa-font-size-s);
    --indicator-color: var(--wa-color-text-quiet);
    --track-width: 2px;
}

.node-name {
}

/* ----- Comment badge ----- */

.comment-badge {
    flex-shrink: 0;
    font-size: 0.7em;
}

/* ----- Git status badge (git mode only) ----- */

/* The git badge and its line-stat sit in one sticky group pinned to the right
   edge, so they stay visible regardless of the node name's width. */
.git-meta {
    position: sticky;
    right: 0;
    flex-shrink: 0;
    margin-left: auto;
    display: inline-flex;
    align-items: center;
    gap: .65rem;
    background-color: var(--node-bg-color);
    padding-left: .4rem;
}

.git-diffstat {
    display: inline-flex;
    gap: .35rem;
    font-size: var(--wa-font-size-xs);
    font-family: var(--wa-font-family-code);
    font-variant-numeric: tabular-nums;
    line-height: 1.6;
    white-space: nowrap;
}

.diffstat-add {
    color: #3a9a28;
}

.diffstat-del {
    color: #e5484d;
}

.git-badge {
    flex-shrink: 0;
    font-size: var(--wa-font-size-xs);
    font-weight: 600;
    font-family: var(--wa-font-family-code);
    line-height: 1.6;
    padding-block: .15rem;
    padding-inline: 0 .25rem;
    letter-spacing: .5px;
}

.git-badge-modified {
    color: #c4841d;
}

.git-badge-added {
    color: #3a9a28;
}

.git-badge-deleted {
    color: #e5484d;
}

.git-badge-renamed {
    color: #6e56cf;
}

.git-badge-untracked {
    color: #7c8594;
}

.git-badge-slot-empty {
    /* dim placeholder for the "clean" side of the staged|unstaged code */
    color: #5b6472;
}

/* Unmerged/conflicted entries: the whole raw XY code in a dedicated colour. */
.git-badge-conflict {
    color: #ff5c5c;
}

</style>
