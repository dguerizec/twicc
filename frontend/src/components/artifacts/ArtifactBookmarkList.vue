<script setup>
// ArtifactBookmarkList.vue — the sidebar list in Artifacts mode. The
// artifacts-mode counterpart of SessionList: a flat, recency-sorted list of
// bookmarked artifacts visible in the current sidebar scope (project /
// workspace / all), resolved by computeArtifactBookmarkList (worktree-aware,
// mirroring session pins) and
// filtered by the shared matchQuery util on name + relative_path.
//
// Rows reuse the exact session-list styling: each row is a wa-button rendered
// `plain`/`neutral` by default and `outlined`/`brand` when it is the artifact
// currently open in the main pane (--active), with a focus-ring --highlighted
// state for keyboard navigation. Bookmark lists are small, so there is no
// virtual scroller — but the keyboard-navigation contract mirrors SessionList
// (handleKeyNavigation exposed, focus-search emitted on ArrowUp from the top).
import { computed, ref, watch, nextTick } from 'vue'
import { useDataStore } from '../../stores/data'
import { useWorkspacesStore } from '../../stores/workspaces'
import { useSettingsStore } from '../../stores/settings'
import { computeArtifactBookmarkList } from '../../utils/sidebarArtifactBookmarks'
import { matchQuery } from '../../utils/textFilter'
import { artifactTypeIcon } from '../../utils/artifactBookmark'
import { formatDate } from '../../utils/date'
import { SESSION_TIME_FORMAT } from '../../constants'
import ProjectBadge from '../project/ProjectBadge.vue'
import WorktreeBadge from '../project/WorktreeBadge.vue'
import AppTooltip from '../ui/AppTooltip.vue'

const props = defineProps({
    effectiveProjectId: { type: String, default: null },
    activeWorkspaceId: { type: String, default: null },
    searchQuery: { type: String, default: '' },
    compactView: { type: Boolean, default: false },
    // The bookmark currently open in the main pane (route param). Marks the
    // matching row as --active (outlined/brand), like the selected session.
    activeBookmarkId: { type: [String, Number], default: null },
})

const emit = defineEmits(['select', 'focus-search'])

const dataStore = useDataStore()
const workspacesStore = useWorkspacesStore()
const settingsStore = useSettingsStore()

const list = computed(() => {
    const projectScopeIds = dataStore.getProjectScopeIds(props.effectiveProjectId)
    let rows = computeArtifactBookmarkList({
        bookmarks: dataStore.artifactBookmarks,
        workspaces: workspacesStore,
        effectiveProjectId: props.effectiveProjectId,
        activeWorkspaceId: props.activeWorkspaceId,
        projectScopeIds,
    })
    const q = props.searchQuery.trim()
    if (q) rows = rows.filter(b => matchQuery(q, b.name) || matchQuery(q, b.relative_path))
    return rows
})

/** Whether a bookmark is the one currently open in the main pane. */
function isActive(b) {
    return props.activeBookmarkId != null && String(b.id) === String(props.activeBookmarkId)
}

/** Whether a bookmark's owning project is a git worktree. */
function isWorktree(b) {
    return !!dataStore.getProject(b.project_id)?.worktree_of
}

/**
 * Color for the compact-mode project dot. A worktree with no color of its own
 * inherits its main repository's color (matching SessionListItem).
 */
function dotColor(b) {
    const p = dataStore.getProject(b.project_id)
    if (!p) return null
    if (p.worktree_of) return p.color || dataStore.getProject(p.worktree_of)?.color || null
    return p.color || null
}

// Last-update time — rendered exactly like the session list (clock icon +
// relative or smart-absolute time, per the same user setting).
const sessionTimeFormat = computed(() => settingsStore.getSessionTimeFormat)
const useRelativeTime = computed(() =>
    sessionTimeFormat.value === SESSION_TIME_FORMAT.RELATIVE_SHORT ||
    sessionTimeFormat.value === SESSION_TIME_FORMAT.RELATIVE_NARROW,
)
const relativeTimeFormat = computed(() =>
    sessionTimeFormat.value === SESSION_TIME_FORMAT.RELATIVE_SHORT ? 'short' : 'narrow',
)
function updatedDate(b) {
    return b.updated_at ? new Date(b.updated_at) : new Date()
}
function updatedTs(b) {
    return b.updated_at ? Math.floor(new Date(b.updated_at).getTime() / 1000) : 0
}

function onSelect(b) {
    emit('select', b)
}

// ═══════════════════════════════════════════════════════════════════════════
// Keyboard navigation (mirrors SessionList, without the virtual scroller)
// ═══════════════════════════════════════════════════════════════════════════

const listRef = ref(null)
const highlightedIndex = ref(-1)
const PAGE_SIZE = 10

/** Scroll the row at `index` into view (no virtual scroller — plain DOM). */
function scrollRowIntoView(index) {
    if (index < 0) return
    nextTick(() => {
        const rows = listRef.value?.querySelectorAll(':scope > .bookmark-item')
        rows?.[index]?.scrollIntoView({ block: 'nearest' })
    })
}

/**
 * Starting index for keyboard navigation: the current highlight if any,
 * otherwise the active (open) bookmark, otherwise none.
 */
function getNavigationStartIndex() {
    if (highlightedIndex.value >= 0) return highlightedIndex.value
    if (props.activeBookmarkId != null) {
        const i = list.value.findIndex(b => isActive(b))
        if (i >= 0) return i
    }
    return -1
}

/**
 * Handle keyboard navigation from the filter input or the list itself.
 *
 * @param {KeyboardEvent} event
 * @param {Object} [options]
 * @param {boolean} [options.fromSearch=false] - True when called from the
 *   filter input: navigation always starts from scratch (ArrowDown → first row)
 *   rather than relative to the active bookmark.
 * @returns {boolean} True if the event was handled (caller should preventDefault).
 */
function handleKeyNavigation(event, { fromSearch = false } = {}) {
    const count = list.value.length
    if (count === 0) return false

    const key = event.key
    const startIndex = (fromSearch && highlightedIndex.value < 0) ? -1 : getNavigationStartIndex()
    let newIndex = highlightedIndex.value

    switch (key) {
        case 'ArrowDown':
            newIndex = startIndex < 0 ? 0 : Math.min(startIndex + 1, count - 1)
            break

        case 'ArrowUp':
            // At the first row, ArrowUp returns focus to the filter input.
            if (startIndex === 0) {
                highlightedIndex.value = -1
                emit('focus-search')
                return true
            }
            newIndex = startIndex < 0 ? count - 1 : Math.max(startIndex - 1, 0)
            break

        case 'Home':
            newIndex = 0
            break

        case 'End':
            newIndex = count - 1
            break

        case 'PageDown':
            newIndex = startIndex < 0 ? PAGE_SIZE - 1 : Math.min(startIndex + PAGE_SIZE, count - 1)
            break

        case 'PageUp':
            if (startIndex === 0) {
                highlightedIndex.value = -1
                emit('focus-search')
                return true
            }
            newIndex = startIndex < 0 ? 0 : Math.max(startIndex - PAGE_SIZE, 0)
            break

        case 'Enter':
            if (highlightedIndex.value >= 0 && highlightedIndex.value < count) {
                onSelect(list.value[highlightedIndex.value])
                return true
            }
            return false

        case 'Escape':
            if (highlightedIndex.value >= 0) {
                highlightedIndex.value = -1
                return true
            }
            return false

        default:
            return false
    }

    if (newIndex !== highlightedIndex.value) {
        highlightedIndex.value = newIndex
        // Keep focus on the list so subsequent keys keep navigating, and bring
        // the highlighted row into view.
        nextTick(() => {
            scrollRowIntoView(newIndex)
            listRef.value?.focus()
        })
    }
    return true
}

/** Keydown handler bound to the list container (focus is in the list). */
function handleListKeydown(event) {
    const navigationKeys = ['ArrowDown', 'ArrowUp', 'Home', 'End', 'PageUp', 'PageDown', 'Enter', 'Escape']
    if (!navigationKeys.includes(event.key)) return
    if (handleKeyNavigation(event)) event.preventDefault()
}

// Reset the highlight whenever the list contents change out from under it.
watch(() => props.searchQuery, () => { highlightedIndex.value = -1 })
watch(() => props.effectiveProjectId, () => { highlightedIndex.value = -1 })
watch(() => props.activeWorkspaceId, () => { highlightedIndex.value = -1 })

// When the open bookmark changes, drop any keyboard highlight and reveal the
// newly-active row (mirrors SessionList scrolling to the selected session).
watch(() => props.activeBookmarkId, (id) => {
    highlightedIndex.value = -1
    if (id == null) return
    const i = list.value.findIndex(b => isActive(b))
    if (i >= 0) scrollRowIntoView(i)
}, { immediate: true })

defineExpose({ handleKeyNavigation })
</script>

<template>
    <div class="bookmark-list-container">
        <div v-if="list.length === 0" class="empty-state">
            <template v-if="searchQuery.trim()">No artifacts in this scope match your filter.</template>
            <template v-else>No bookmarked artifacts in this scope yet — bookmark an artifact from a session's Artifacts tab.</template>
        </div>
        <div
            v-else
            ref="listRef"
            class="bookmark-list"
            tabindex="0"
            @keydown="handleListKeydown"
        >
            <wa-button
                v-for="(b, index) in list"
                :key="b.id"
                :appearance="isActive(b) ? 'outlined' : 'plain'"
                :variant="isActive(b) ? 'brand' : 'neutral'"
                class="bookmark-item"
                :class="{
                    'bookmark-item--active': isActive(b),
                    'bookmark-item--highlighted': index === highlightedIndex,
                    'bookmark-item--compact': compactView,
                }"
                @click="onSelect(b)"
            >
                <div class="bookmark-name-row">
                    <!-- Compact: inline project color dot (worktree inherits its parent's color) -->
                    <span
                        v-if="compactView"
                        :id="`bookmark-dot-${b.id}`"
                        class="bookmark-dot"
                        :style="dotColor(b) ? { '--dot-color': dotColor(b) } : null"
                    ></span>
                    <AppTooltip v-if="compactView" :for="`bookmark-dot-${b.id}`">
                        <WorktreeBadge v-if="isWorktree(b)" :project-id="b.project_id" :dot="false" />
                        <ProjectBadge v-else :project-id="b.project_id" />
                    </AppTooltip>
                    <!-- File-type icon, before the name (both modes) -->
                    <wa-icon class="bookmark-type" :name="artifactTypeIcon(b.file_ext)"></wa-icon>
                    <span class="bookmark-name">{{ b.name }}</span>
                </div>
                <!-- Non-compact: project badge (left) + last-update time (right) -->
                <div v-if="!compactView" class="bookmark-meta">
                    <WorktreeBadge v-if="isWorktree(b)" :project-id="b.project_id" class="bookmark-project" />
                    <ProjectBadge v-else :project-id="b.project_id" class="bookmark-project" />
                    <span class="bookmark-time">
                        <wa-icon auto-width name="clock" variant="regular"></wa-icon>
                        <wa-relative-time
                            v-if="useRelativeTime"
                            :date.prop="updatedDate(b)"
                            :format="relativeTimeFormat"
                            numeric="always"
                            sync
                        ></wa-relative-time>
                        <template v-else>{{ formatDate(updatedTs(b), { smart: true }) }}</template>
                    </span>
                </div>
            </wa-button>
        </div>
    </div>
</template>

<style scoped>
.bookmark-list-container {
    flex: 1;
    min-height: 0;
    width: 100%;
    display: flex;
    flex-direction: column;
    overflow: hidden;
    container-type: inline-size;
    container-name: bookmark-list;
}

.bookmark-list {
    flex: 1;
    min-height: 0;
    overflow-y: auto;
    padding: var(--wa-space-2xs);
    display: flex;
    flex-direction: column;
}

/* Highlight is shown on the items, not the list itself. */
.bookmark-list:focus {
    outline: none;
}

.bookmark-item {
    width: 100%;
}

/* Gap between rows (non-compact only), matching the session list. */
.bookmark-item:not(.bookmark-item--compact) {
    margin-block: var(--wa-space-3xs);
}

.bookmark-item::part(base) {
    padding: var(--wa-space-xs);
    height: auto;
    /* Reserve the outlined-appearance shadow offset so the active row doesn't
       shift the others (matches SessionListItem). */
    margin-bottom: var(--wa-shadow-offset-y-s);
}

.bookmark-item--compact::part(base) {
    padding-block: var(--wa-space-2xs);
}

.bookmark-item::part(label) {
    width: 100%;
    text-align: left;
}

/* Keyboard navigation highlight */
.bookmark-item--highlighted::part(base) {
    outline: var(--wa-focus-ring);
    outline-offset: var(--wa-focus-ring-offset);
}

.bookmark-name-row {
    display: flex;
    align-items: center;
    justify-content: flex-start;
    gap: var(--wa-space-xs);
    width: 100%;
    min-width: 0;
}

/* Compact-mode project color dot (mirrors SessionListItem's compact dot). */
.bookmark-dot {
    width: var(--wa-space-s);
    height: var(--wa-space-s);
    border-radius: 50%;
    flex-shrink: 0;
    border: 1px solid;
    box-sizing: border-box;
    background-color: var(--dot-color, transparent);
    border-color: var(--dot-color, var(--wa-color-border-quiet));
}

.bookmark-type {
    flex-shrink: 0;
    color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-s);
}

.bookmark-name {
    font-size: var(--wa-font-size-s);
    font-weight: 700;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    min-width: 0;
}

.bookmark-meta {
    display: flex;
    align-items: center;
    justify-content: flex-start;
    gap: var(--wa-space-xs);
    width: 100%;
    min-width: 0;
    margin-top: var(--wa-space-3xs);
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-text-quiet);
    font-weight: var(--wa-font-weight-body);
}

.bookmark-project {
    min-width: 0;
    overflow: hidden;
}

.bookmark-time {
    display: flex;
    align-items: center;
    gap: var(--wa-space-2xs);
    flex-shrink: 0;
    margin-left: auto;
}

.empty-state {
    flex: 1;
    display: flex;
    align-items: center;
    justify-content: center;
    text-align: center;
    padding: var(--wa-space-l);
    color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-m);
}
</style>
