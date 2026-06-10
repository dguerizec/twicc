<script setup>
/**
 * SessionList - Virtual-scrolled list of sessions for a project.
 *
 * Handles list-level concerns: virtual scrolling, pagination (load more),
 * search filtering, keyboard navigation. Each session item is rendered
 * by SessionListItem, which owns its own store lookups (computed).
 */
import { ref, computed, watch, nextTick } from 'vue'
import { useRoute } from 'vue-router'
import { useDataStore, ALL_PROJECTS_ID } from '../../../stores/data'
import { useWorkspacesStore } from '../../../stores/workspaces'
import { useSessionSelectionStore } from '../../../stores/sessionSelection'
import { isWorkspaceProjectId, extractWorkspaceId } from '../../../utils/workspaceIds'
import { computeSidebarSessionBlocks } from '../../../utils/sidebarSessions'
import VirtualScroller from '../../virtual-scroller/VirtualScroller.vue'
import SessionListItem from './SessionListItem.vue'

const props = defineProps({
    projectId: {
        type: String,
        required: true
    },
    sessionId: {
        type: String,
        default: null
    },
    showProjectName: {
        type: Boolean,
        default: false
    },
    searchQuery: {
        type: String,
        default: ''
    },
    showArchived: {
        type: Boolean,
        default: false
    },
    showArchivedProjects: {
        type: Boolean,
        default: true
    },
    compactView: {
        type: Boolean,
        default: false
    },
    // When true, sessions with a running Claude SDK process or unread content
    // are always surfaced in the sidebar — even when they belong to a project
    // outside the current filter/workspace.
    showActiveAcrossFilters: {
        type: Boolean,
        default: false
    }
})

const store = useDataStore()
const workspacesStore = useWorkspacesStore()
const selectionStore = useSessionSelectionStore()
const route = useRoute()

// Natural scope project IDs for the current sidebar filter, passed down so
// SessionListItem can flag any session whose project falls outside it as
// cross-filter (and show its project badge accordingly). null = all-projects
// mode (no scope restriction).
const scopeProjectIds = computed(() => {
    if (isWorkspaceProjectId(props.projectId)) {
        const wsId = extractWorkspaceId(props.projectId)
        return workspacesStore.getVisibleProjectIds(wsId)
    }
    if (props.projectId === ALL_PROJECTS_ID) return null
    // A main repo's scope also covers its worktrees, so their sessions aren't
    // flagged as cross-filter and the project badge is shown to tell them apart.
    return store.getProjectScopeIds(props.projectId)
})

// The single real project the sidebar is filtering on, or null in workspace /
// all-projects mode. Passed down so SessionListItem can hide the worktree
// marker only when we're filtering directly on that worktree (where every
// session already belongs to it, so the marker would be noise).
const filterProjectId = computed(() => {
    if (isWorkspaceProjectId(props.projectId)) return null
    if (props.projectId === ALL_PROJECTS_ID) return null
    return props.projectId
})

// The currently "active" workspace for cross-filter `workspace`-mode pins. Set
// when the sidebar is on a workspace view (projectId `workspace:X`) OR when a
// single-project view preserves the workspace via the `?workspace=X` query.
const activeWorkspaceId = computed(() => {
    if (isWorkspaceProjectId(props.projectId)) {
        return extractWorkspaceId(props.projectId)
    }
    return route.query.workspace || null
})

// All four sidebar blocks, computed in one shot by the shared helper so the
// command palette's "Go to Session…" sub-picker can use the exact same
// ordering / filtering / grouping logic.
const sessionBlocks = computed(() => computeSidebarSessionBlocks({
    data: store,
    workspaces: workspacesStore,
    effectiveProjectId: props.projectId,
    activeWorkspaceId: activeWorkspaceId.value,
    sessionId: props.sessionId,
    showArchived: props.showArchived,
    showArchivedProjects: props.showArchivedProjects,
    showActiveAcrossFilters: props.showActiveAcrossFilters,
}))

const extraSessionId = computed(() => sessionBlocks.value.extra?.id ?? null)

/**
 * Ids of the last item of each top block that should be followed by a divider.
 * A divider is only rendered when the block it terminates has a non-empty
 * block somewhere below it — so the bottom-most non-empty block never gets
 * a trailing divider.
 */
const dividerAfterIds = computed(() => {
    const { extra, crossFilterPinned, crossFilterActive, natural } = sessionBlocks.value
    const ids = new Set()
    if (extra && (crossFilterPinned.length || crossFilterActive.length || natural.length)) {
        ids.add(extra.id)
    }
    if (crossFilterPinned.length && (crossFilterActive.length || natural.length)) {
        ids.add(crossFilterPinned[crossFilterPinned.length - 1].id)
    }
    if (crossFilterActive.length && natural.length) {
        ids.add(crossFilterActive[crossFilterActive.length - 1].id)
    }
    return ids
})

// Flat list consumed by the virtual scroller:
//   [extra?, ...crossFilterPinned, ...crossFilterActive, ...natural]
// Dividers live in the template, keyed off `dividerAfterIds`.
const allSessions = computed(() => {
    const { extra, crossFilterPinned, crossFilterActive, natural } = sessionBlocks.value
    const result = []
    if (extra) result.push(extra)
    result.push(...crossFilterPinned)
    result.push(...crossFilterActive)
    result.push(...natural)
    return result
})

/**
 * Check if a query matches a text using subsequence matching.
 * All characters from query must appear in text, in order, but not necessarily consecutive.
 * Case-insensitive.
 *
 * Examples:
 *   matchSubsequence("vs", "virtual scroller") -> true (v...irtual s...croller)
 *   matchSubsequence("vscr", "virtual scroller") -> true (v...irtual scr...oller)
 *   matchSubsequence("xyz", "virtual scroller") -> false
 *
 * @param {string} query - The search query
 * @param {string} text - The text to search in
 * @returns {boolean} True if query is a subsequence of text
 */
function matchSubsequence(query, text) {
    const lowerQuery = query.toLowerCase()
    const lowerText = text.toLowerCase()

    let queryIndex = 0
    for (let i = 0; i < lowerText.length && queryIndex < lowerQuery.length; i++) {
        if (lowerText[i] === lowerQuery[queryIndex]) {
            queryIndex++
        }
    }
    return queryIndex === lowerQuery.length
}

/**
 * Resolve a sidebar filter query against a display string.
 *
 * - Queries starting with `"` or `'` switch to case-insensitive substring
 *   matching. An optional trailing matching quote is stripped, so both
 *   `"foo` and `"foo"` look for the literal substring `foo`.
 * - Anything else uses the default subsequence (fuzzy) matching.
 *
 * The backend mirrors this dispatch in `_match_session_query` (views.py)
 * so the bulk-archive scope matches the sidebar exactly.
 */
function matchSessionQuery(query, text) {
    const first = query[0]
    if (first === '"' || first === "'") {
        let needle = query.slice(1)
        if (needle.endsWith(first)) needle = needle.slice(0, -1)
        if (!needle) return true
        return text.toLowerCase().includes(needle.toLowerCase())
    }
    return matchSubsequence(query, text)
}

// Filtered sessions based on the search query. Fuzzy by default; exact
// substring when the query is wrapped/prefixed with `"` or `'`.
const sessions = computed(() => {
    const query = props.searchQuery.trim()
    if (!query) return allSessions.value

    return allSessions.value.filter(session => {
        const displayName = (session.draft && !session.title)
            ? 'New session'
            : (session.title || session.id)
        return matchSessionQuery(query, displayName)
    })
})

// Pagination state
const hasMore = computed(() => store.hasMoreSessions(props.projectId))
const isLoading = computed(() => store.areSessionsLoading(props.projectId))

// Local error state for "load more" failures (not initial load)
const loadMoreError = ref(false)

// Virtual scroller configuration
// Session items have relatively uniform height (~80-100px normal, ~35-40px compact)
const minSessionHeight = computed(() => props.compactView ? 35 : 70)
const SCROLLER_BUFFER = 300

// Reference to the VirtualScroller component
const scrollerRef = ref(null)

// Keyboard navigation: highlighted item index (-1 = none)
const highlightedIndex = ref(-1)

// Number of items to jump for PageUp/PageDown
const PAGE_SIZE = 10

// Load more sessions when approaching the end of the list
async function loadMore() {
    if (isLoading.value || !hasMore.value || loadMoreError.value) return

    try {
        loadMoreError.value = false
        await store.loadSessions(props.projectId)
    } catch {
        // Only show error if we already have some sessions (not initial load)
        if (sessions.value.length > 0) {
            loadMoreError.value = true
        }
    }
}

// Retry after error
async function handleRetry() {
    loadMoreError.value = false
    await loadMore()
}

/**
 * Handle virtual scroller update event.
 * Triggers loading more sessions when user scrolls near the end.
 */
function onScrollerUpdate({ visibleEndIndex }) {
    // Load more when within 10 items of the end
    if (hasMore.value && !isLoading.value && sessions.value.length - visibleEndIndex < 10) {
        loadMore()
    }
}

// Reset scroll to top and highlight when project changes
watch(() => props.projectId, () => {
    loadMoreError.value = false
    highlightedIndex.value = -1
    if (scrollerRef.value) {
        scrollerRef.value.scrollToTop()
    }
})

// Reset highlight when search query changes
watch(() => props.searchQuery, () => {
    highlightedIndex.value = -1
})

// Reset highlight when selected session changes.
watch(() => props.sessionId, (newSessionId) => {
    highlightedIndex.value = -1
})

// Scroll to the selected session after DOM update.
// Uses flush:'post' because the VirtualScroller has :key="projectId" — when both
// projectId and sessionId change simultaneously (e.g., navigating from search results),
// the scroller is destroyed and recreated. A pre-flush watcher would scroll the OLD
// scroller. flush:'post' ensures the new scroller is mounted and has run its
// onMounted/syncScrollPosition before we attempt to scroll.
// Uses immediate:true because navigating from single-project (/project/X) to all-projects
// (/projects/X/session/Y) remounts the entire component tree (different route branches).
// Without immediate, the watcher wouldn't fire for the initial sessionId value on mount.
watch(() => props.sessionId, (newSessionId) => {
    if (newSessionId) {
        scrollToSession(newSessionId)
    }
}, { flush: 'post', immediate: true })

// Sessions that leave the visible list (filter change, archived away, …)
// are dropped from the multi-select selection.
watch(sessions, (list) => {
    if (!selectionStore.active) return
    selectionStore.prune(new Set(list.map(s => s.id)))
})

/**
 * Scroll the session list to make a session visible.
 * Retries a few times because the VirtualScroller may be recreated (via :key)
 * when projectId changes simultaneously with sessionId, and the new scroller
 * needs time to mount and measure items.
 */
function scrollToSession(targetSessionId, attempt = 0) {
    const MAX_ATTEMPTS = 5
    const RETRY_DELAY = 50

    if (!sessions.value.some(s => s.id === targetSessionId)) {
        // Session not in list yet (data loading). Retry a few times.
        if (attempt < MAX_ATTEMPTS) {
            setTimeout(() => scrollToSession(targetSessionId, attempt + 1), RETRY_DELAY)
        }
        return
    }

    if (!scrollerRef.value) {
        // Scroller not mounted yet (recreated via :key). Retry.
        if (attempt < MAX_ATTEMPTS) {
            setTimeout(() => scrollToSession(targetSessionId, attempt + 1), RETRY_DELAY)
        }
        return
    }

    // Use the VirtualScroller's scrollToKey which has a robust "jump, settle, correct"
    // loop: it scrolls to the item, waits for ResizeObserver height measurements to
    // stabilize, then verifies visibility and re-scrolls if needed. This handles all
    // timing issues when the scroller was just recreated (via :key on projectId change).
    scrollerRef.value.scrollToKey(targetSessionId, { align: 'center' })
}

const emit = defineEmits(['select', 'drop-data', 'focus-search'])

function handleSelect(session) {
    // In multi-select mode, opening a session also anchors it: a following
    // Shift+click ranges from the last clicked item (file-manager semantics),
    // even though the plain click doesn't touch the selection itself.
    if (selectionStore.active) {
        selectionStore.setAnchor(session.id)
    }
    emit('select', session)
}

function handleDropData(data) {
    emit('drop-data', data)
}

/**
 * Handle a modifier click forwarded by SessionListItem while the
 * multi-select mode is active. Ranges follow the visual order of the
 * filtered flat list (`sessions`), crossing block dividers.
 *
 * Semantics (standard Windows/macOS list selection):
 * - Plain click (open)        → sets the anchor without touching the selection (see handleSelect)
 * - Ctrl/Cmd+click            → toggle the item, it becomes the anchor
 * - Shift+click               → select anchor→target range, REPLACING the selection
 * - Ctrl/Cmd+Shift+click      → ADD the anchor→target range to the selection
 * - Shift+click with no anchor → select the item alone and anchor it
 */
function handleSelectionClick({ session, shift, ctrl }) {
    if (!shift) {
        selectionStore.toggle(session.id)
        return
    }
    const ids = sessions.value.map(s => s.id)
    const targetIndex = ids.indexOf(session.id)
    if (targetIndex === -1) return
    const anchorIndex = selectionStore.anchorId ? ids.indexOf(selectionStore.anchorId) : -1
    if (anchorIndex === -1) {
        // No usable anchor (never set, or pruned out of the visible list):
        // anchor the clicked item. Ctrl keeps the existing selection (adds),
        // plain Shift replaces it.
        selectionStore.setSelection(
            ctrl ? [...selectionStore.selectedIds, session.id] : [session.id],
            { anchor: session.id },
        )
        return
    }
    const [from, to] = anchorIndex <= targetIndex
        ? [anchorIndex, targetIndex]
        : [targetIndex, anchorIndex]
    const range = ids.slice(from, to + 1)
    if (ctrl) selectionStore.addSelection(range)
    else selectionStore.setSelection(range)
}

/**
 * Get the starting index for keyboard navigation.
 * If a session is highlighted, use that. Otherwise, use the selected session's index.
 * Returns -1 if neither is available.
 */
function getNavigationStartIndex() {
    if (highlightedIndex.value >= 0) {
        return highlightedIndex.value
    }
    // No highlight - try to start from selected session
    if (props.sessionId) {
        const selectedIndex = sessions.value.findIndex(s => s.id === props.sessionId)
        if (selectedIndex >= 0) {
            return selectedIndex
        }
    }
    return -1
}

/**
 * Handle keyboard navigation from the search input or the list itself.
 * Navigates through sessions with arrow keys and selects with Enter.
 *
 * @param {KeyboardEvent} event - The keyboard event
 * @param {Object} [options] - Navigation options
 * @param {boolean} [options.fromSearch=false] - True when called from the search input.
 *   When true, navigation ignores the selected session and always starts from scratch
 *   (e.g., ArrowDown goes to the first item, not relative to the active session).
 * @returns {boolean} True if the event was handled (should preventDefault)
 */
function handleKeyNavigation(event, { fromSearch = false } = {}) {
    // Escape priority: exit multi-select mode > clear highlight > (parent:
    // clear search). Checked before the empty-list early return so the mode
    // can be exited even when the filter matches nothing.
    if (event.key === 'Escape' && selectionStore.active) {
        selectionStore.exit()
        return true
    }

    const count = sessions.value.length
    if (count === 0) return false

    const key = event.key
    // When coming from the search input with no highlight, always start from
    // scratch (-1) so that ArrowDown goes to the first item, not relative to
    // the currently selected session.
    const startIndex = (fromSearch && highlightedIndex.value < 0) ? -1 : getNavigationStartIndex()
    let newIndex = highlightedIndex.value

    switch (key) {
        case 'ArrowDown':
            // Move down from current position, or start at first item
            newIndex = startIndex < 0 ? 0 : Math.min(startIndex + 1, count - 1)
            break

        case 'ArrowUp':
            // If already at the first item, move focus back to the search input
            if (startIndex === 0) {
                highlightedIndex.value = -1
                emit('focus-search')
                return true
            }
            // Move up from current position, or start at last item
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
            // If already at the first item, move focus back to the search input
            if (startIndex === 0) {
                highlightedIndex.value = -1
                emit('focus-search')
                return true
            }
            newIndex = startIndex < 0 ? 0 : Math.max(startIndex - PAGE_SIZE, 0)
            break

        case 'Enter':
            // Select the highlighted session
            if (highlightedIndex.value >= 0 && highlightedIndex.value < count) {
                handleSelect(sessions.value[highlightedIndex.value])
                return true
            }
            return false

        case 'Escape':
            // Clear highlight if any, otherwise let parent handle it (e.g., clear search)
            if (highlightedIndex.value >= 0) {
                highlightedIndex.value = -1
                return true
            }
            return false

        default:
            return false
    }

    // Update highlight and scroll to it
    if (newIndex !== highlightedIndex.value) {
        highlightedIndex.value = newIndex
        if (scrollerRef.value) {
            // For Home/End, use the scroller's native methods which work better
            // For other navigation, scroll to make the item visible
            if (key === 'Home') {
                scrollerRef.value.scrollToTop()
            } else if (key === 'End') {
                // scrollToBottom() uses estimated heights for unmeasured items,
                // which may not scroll far enough. After the initial scroll,
                // wait for items to be rendered AND measured by ResizeObserver.
                // ResizeObserver is async and not tied to Vue's nextTick, so we use
                // a small timeout to allow measurements to complete.
                scrollerRef.value.scrollToBottom()
                setTimeout(() => {
                    scrollerRef.value?.scrollToIndex(newIndex, { align: 'end' })
                }, 50)
            } else if (key === 'PageDown' || key === 'PageUp') {
                // Page navigation may jump to unmeasured items, use delayed correction
                scrollToIndexIfNeeded(newIndex, { delayedCorrection: true })
            } else {
                // For arrow keys, items are usually already measured (adjacent to visible)
                scrollToIndexIfNeeded(newIndex)
            }

            // Ensure focus stays on the list after scroll (items may be re-rendered)
            // Use nextTick to wait for Vue to update the DOM
            nextTick(() => {
                scrollerRef.value?.$el?.focus()
            })
        }
    }
    return true
}

/**
 * Scroll to an index only if it's not already fully visible in the viewport.
 * Uses align 'start' or 'end' depending on scroll direction.
 *
 * @param {number} index - The item index to scroll to
 * @param {Object} [options] - Options
 * @param {boolean} [options.delayedCorrection=false] - If true, re-scroll after a delay
 *        to account for items that weren't measured yet (heights were estimated)
 */
function scrollToIndexIfNeeded(index, { delayedCorrection = false } = {}) {
    if (!scrollerRef.value) return

    // Get the actual visible range from the scroller (based on measured heights)
    // visibleEnd is exclusive and may include a partially visible item at the bottom
    const { start: visibleStart, end: visibleEnd } = scrollerRef.value.getVisibleRange()

    let scrolled = false
    let align = null

    if (index < visibleStart) {
        // Item is above the viewport
        align = 'start'
        scrollerRef.value.scrollToIndex(index, { align })
        scrolled = true
    } else if (index >= visibleEnd) {
        // Item is below the viewport
        align = 'end'
        scrollerRef.value.scrollToIndex(index, { align })
        scrolled = true
    }

    // If we scrolled and correction is requested, re-scroll after items are measured
    // This handles the case where we scroll to unmeasured items with estimated heights
    if (scrolled && delayedCorrection && align) {
        setTimeout(() => {
            scrollerRef.value?.scrollToIndex(index, { align })
        }, 50)
    }
}

/**
 * Handle keydown events directly on the session list container.
 * This allows keyboard navigation when focus is in the list (not just the search input).
 *
 * @param {KeyboardEvent} event
 */
function handleListKeydown(event) {
    // Only handle navigation keys
    const navigationKeys = ['ArrowDown', 'ArrowUp', 'Home', 'End', 'PageUp', 'PageDown', 'Enter', 'Escape']
    if (!navigationKeys.includes(event.key)) return

    const handled = handleKeyNavigation(event)
    if (handled) {
        event.preventDefault()
    }
}

// Expose methods for parent component access via ref
defineExpose({
    handleKeyNavigation,
})
</script>

<template>
    <div class="session-list-container" :class="{ 'session-list-container--compact': compactView }">
        <!-- Empty state: no sessions at all -->
        <div v-if="allSessions.length === 0 && !isLoading" class="empty-state">
            No sessions
        </div>

        <!-- Empty state: no matching sessions (search returned nothing) -->
        <div v-else-if="sessions.length === 0 && !isLoading" class="empty-state">
            No matching sessions
        </div>

        <!-- Session list with virtual scroller -->
        <VirtualScroller
            v-else
            ref="scrollerRef"
            :key="projectId"
            :items="sessions"
            :item-key="session => session.id"
            :min-item-height="minSessionHeight"
            :buffer="SCROLLER_BUFFER"
            :unload-buffer="SCROLLER_BUFFER * 1.5"
            class="session-list"
            tabindex="0"
            @update="onScrollerUpdate"
            @keydown="handleListKeydown"
        >
            <template #default="{ item: session, index }">
                <SessionListItem
                    :session="session"
                    :active="session.id === sessionId"
                    :highlighted="index === highlightedIndex"
                    :compact-view="compactView"
                    :show-project-name="showProjectName"
                    :scope-project-ids="scopeProjectIds"
                    :filter-project-id="filterProjectId"
                    @select="handleSelect"
                    @drop-data="handleDropData"
                    @selection-click="handleSelectionClick"
                />
                <wa-divider
                    v-if="dividerAfterIds.has(session.id)"
                    class="session-list-group-divider"
                ></wa-divider>
            </template>
        </VirtualScroller>

        <!-- Error state for load more (shown after the scroller) -->
        <div v-if="loadMoreError" class="load-more-error">
            <wa-callout variant="danger">
                <span>Failed to load more sessions</span>
                <wa-button
                    slot="footer"
                    variant="danger"
                    appearance="outlined"
                    size="small"
                    :loading="isLoading"
                    @click="handleRetry"
                >
                    <wa-icon name="arrow-rotate-right" slot="start"></wa-icon>
                    Retry
                </wa-button>
            </wa-callout>
        </div>

        <!-- Loading indicator (shown at bottom when loading more) -->
        <div v-if="isLoading && sessions.length > 0" class="load-more-indicator">
            <wa-spinner></wa-spinner>
        </div>

    </div>
</template>

<style scoped>
.session-list-container {
    display: flex;
    flex-direction: column;
    height: 100%;
    min-height: 0;
    overflow: hidden;
    container-type: inline-size;
    container-name: session-list;
}

.session-list {
    flex: 1;
    min-height: 0;
    padding-block: var(--wa-space-2xs);
}

/* Remove default focus outline on the list - we show highlight on items instead */
.session-list:focus {
    outline: none;
}

/* Gap between items (non-compact mode only).
   Targets the child component's root element via Vue's scoped CSS inheritance. */
.session-list-container:not(.session-list-container--compact) :deep(.session-item-wrapper) {
    margin-block: var(--wa-space-3xs);
}

/* Divider rendered below the "extra" selected session at the very top of the
   list, and below the cross-filter pinned block (pinned sessions not
   naturally in scope) when either is present. */
.session-list-group-divider {
    --width: var(--divider-size);
    --spacing: var(--wa-space-2xs);
}

.load-more-indicator {
    display: flex;
    justify-content: center;
    padding: var(--wa-space-s);
    flex-shrink: 0;
}

.load-more-error {
    padding: var(--wa-space-s);
    flex-shrink: 0;
}

.load-more-error wa-callout {
    --wa-callout-padding: var(--wa-space-s);
}

.load-more-error wa-callout span {
    font-size: var(--wa-font-size-s);
}

.empty-state {
    flex: 1;
    display: flex;
    align-items: center;
    justify-content: center;
    text-align: center;
    padding: var(--wa-space-l);
    color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-l);
}
</style>
