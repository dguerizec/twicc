<script setup>
/**
 * CommandPalette — Modal command palette with search, keyboard navigation,
 * and nested sub-selection mode.
 *
 * Opens via `isOpen` from the command registry. Displays commands grouped
 * by category (root mode), filtered by fuzzy search (search mode), or
 * showing sub-items for a parent command (nested mode).
 *
 * Keyboard navigation:
 *   ArrowUp/Down — move selection
 *   Enter — execute or enter sub-level
 *   Escape — go back (nested → root) or close
 *   Home/End — first/last item
 *   PageUp/PageDown — jump ~8 items
 */

import { ref, computed, watch, nextTick, shallowRef } from 'vue'
import { useCommandRegistry } from '../../composables/useCommandRegistry'
import { fuzzyMatch } from '../../utils/fuzzyMatch'
import ProcessIndicator from '../ui/ProcessIndicator.vue'

const { isOpen, availableCommands, commandsByCategory, categoryLabelByKey, openPalette, closePalette } = useCommandRegistry()

const dialogRef = ref(null)
const searchInputRef = ref(null)
const listRef = ref(null)

const query = ref('')
const activeId = ref(null)
const parentCommand = shallowRef(null)

const PAGE_SIZE = 8

// ─── Dialog open/close driven by registry state ─────────────────────────

watch(isOpen, (open) => {
    if (!dialogRef.value) return
    if (open) {
        dialogRef.value.open = true
    } else {
        dialogRef.value.open = false
    }
})

// ─── Search results: fuzzy filtered + scored ─────────────────────────────

const searchResults = computed(() => {
    if (!query.value || parentCommand.value) return []
    const results = []
    for (const cmd of availableCommands.value) {
        const result = fuzzyMatch(query.value, cmd.label)
        if (result.match) {
            results.push({
                cmd,
                score: result.score,
                highlighted: highlightMatches(cmd.label, result.ranges),
                categoryLabel: categoryLabelByKey.value.get(cmd.category) ?? '',
            })
        }
    }
    results.sort((a, b) => b.score - a.score)
    return results
})

// ─── Nested items (when parentCommand is set) ────────────────────────────

const nestedResults = computed(() => {
    if (!parentCommand.value?.items) return []
    const items = parentCommand.value.items()
    if (!query.value) {
        // Always produce escaped HTML for safe v-html rendering
        return items.map((item) => ({
            ...item,
            highlighted: escapeHtml(item.label),
            ...(item.path ? { pathHighlighted: escapeHtml(item.path) } : {}),
            ...(item.worktree ? {
                worktree: { ...item.worktree, parentHighlighted: escapeHtml(item.worktree.parentName) },
            } : {}),
        }))
    }
    // Filter by fuzzy match against the label and, when present, the item's
    // absolute path and (for worktree sub-items) its parent project name. An
    // item passes if any of them matches; the kept score is the best of them.
    const results = []
    for (const item of items) {
        const parentName = item.worktree?.parentName || ''
        const labelResult = fuzzyMatch(query.value, item.label)
        const pathResult = item.path ? fuzzyMatch(query.value, item.path) : null
        const parentResult = parentName ? fuzzyMatch(query.value, parentName) : null
        if (!labelResult.match && !pathResult?.match && !parentResult?.match) continue
        results.push({
            ...item,
            score: Math.max(labelResult.score, pathResult?.score ?? 0, parentResult?.score ?? 0),
            highlighted: labelResult.match
                ? highlightMatches(item.label, labelResult.ranges)
                : escapeHtml(item.label),
            ...(item.path ? {
                pathHighlighted: pathResult?.match
                    ? highlightMatches(item.path, pathResult.ranges)
                    : escapeHtml(item.path),
            } : {}),
            ...(item.worktree ? {
                worktree: {
                    ...item.worktree,
                    parentHighlighted: parentResult?.match
                        ? highlightMatches(parentName, parentResult.ranges)
                        : escapeHtml(parentName),
                },
            } : {}),
        })
    }
    results.sort((a, b) => b.score - a.score)
    return results
})

// ─── Flat list of all currently visible items (for keyboard nav) ─────────

const visibleItems = computed(() => {
    if (parentCommand.value) return nestedResults.value
    if (query.value) return searchResults.value.map((r) => r.cmd)
    // Category mode: flat list of all commands
    const flat = []
    for (const group of commandsByCategory.value) {
        for (const cmd of group.commands) {
            flat.push(cmd)
        }
    }
    return flat
})

// ─── Auto-select first item when visible items change ────────────────────

function selectFirstItem() {
    const items = visibleItems.value
    activeId.value = items.length > 0 ? items[0].id : null
}

watch(visibleItems, selectFirstItem)

// ─── Dialog event handlers ───────────────────────────────────────────────

function onAfterShow() {
    selectFirstItem()
    searchInputRef.value?.focus()
}

function onHide() {
    // Reset state when dialog actually closes
    query.value = ''
    activeId.value = null
    parentCommand.value = null
    closePalette()
}

// ─── Command execution ──────────────────────────────────────────────────

/**
 * Execute an action after the dialog has fully closed.
 * This ensures the dialog doesn't steal focus from the action
 * (e.g., focus commands need the dialog gone first).
 *
 * wa-dialog internally does `setTimeout(() => trigger.focus())` BEFORE
 * dispatching wa-after-hide. So even with rAF in onAfterHide, the queued
 * setTimeout would restore focus to the trigger element and steal it.
 * We clear `originalTrigger` before closing so that restoration is a no-op,
 * then run our action after a setTimeout to stay after any dialog internals.
 */
let pendingAction = null

function executeAfterClose(action) {
    pendingAction = action
    // Neutralize wa-dialog's focus restoration (it checks trigger?.focus)
    if (dialogRef.value) {
        dialogRef.value.originalTrigger = null
    }
    close()
}

function onAfterHide() {
    if (pendingAction) {
        const action = pendingAction
        pendingAction = null
        // Run after any remaining dialog internals (setTimeout-based)
        setTimeout(() => action())
    }
}

function selectCommand(cmd) {
    if (cmd.items) {
        // Enter nested mode
        parentCommand.value = cmd
        query.value = ''
        // activeId will be set by the visibleItems watcher
        nextTick(() => searchInputRef.value?.focus())
    } else {
        executeAfterClose(() => cmd.action?.())
    }
}

function selectNestedItem(item) {
    executeAfterClose(() => item.action?.())
}

// ─── Open / close ────────────────────────────────────────────────────────

function open() {
    // Always go through openPalette() to bump contextVersion
    // (ensures when() guards are freshly evaluated)
    openPalette()
    // The watcher on isOpen will set dialogRef.value.open = true
}

function close() {
    if (dialogRef.value) {
        dialogRef.value.open = false
    }
    // onHide will handle state reset and closePalette()
}

function goBack() {
    if (parentCommand.value) {
        const parentId = parentCommand.value.id
        parentCommand.value = null
        query.value = ''
        nextTick(() => {
            activeId.value = parentId
            scrollIntoView(parentId)
            searchInputRef.value?.focus()
        })
    } else {
        close()
    }
}

// ─── Keyboard navigation ────────────────────────────────────────────────

function handleKeydown(e) {
    const items = visibleItems.value
    if (!items.length && !['Escape', 'ArrowLeft', 'Backspace'].includes(e.key)) return

    switch (e.key) {
        case 'ArrowDown': {
            e.preventDefault()
            const idx = items.findIndex((i) => i.id === activeId.value)
            const next = Math.min(idx + 1, items.length - 1)
            activeId.value = items[next].id
            scrollIntoView(items[next].id)
            break
        }
        case 'ArrowUp': {
            e.preventDefault()
            const idx = items.findIndex((i) => i.id === activeId.value)
            const prev = Math.max(idx - 1, 0)
            activeId.value = items[prev].id
            scrollIntoView(items[prev].id)
            break
        }
        case 'Home': {
            e.preventDefault()
            activeId.value = items[0].id
            scrollIntoView(items[0].id)
            break
        }
        case 'End': {
            e.preventDefault()
            activeId.value = items[items.length - 1].id
            scrollIntoView(items[items.length - 1].id)
            break
        }
        case 'PageDown': {
            e.preventDefault()
            const idx = items.findIndex((i) => i.id === activeId.value)
            const next = Math.min(idx + PAGE_SIZE, items.length - 1)
            activeId.value = items[next].id
            scrollIntoView(items[next].id)
            break
        }
        case 'PageUp': {
            e.preventDefault()
            const idx = items.findIndex((i) => i.id === activeId.value)
            const prev = Math.max(idx - PAGE_SIZE, 0)
            activeId.value = items[prev].id
            scrollIntoView(items[prev].id)
            break
        }
        case 'ArrowRight': {
            // Enter sub-menu if command has items (like Enter)
            if (parentCommand.value) break // already in nested mode
            const activeCmd = items.find((i) => i.id === activeId.value)
            if (activeCmd?.items) {
                e.preventDefault()
                selectCommand(activeCmd)
            }
            break
        }
        case 'ArrowLeft':
        case 'Backspace': {
            // Go back one level when query is empty (nested → root → close)
            if (!query.value) {
                e.preventDefault()
                goBack()
            }
            break
        }
        case 'Enter': {
            e.preventDefault()
            const active = items.find((i) => i.id === activeId.value)
            if (!active) break
            if (parentCommand.value) {
                selectNestedItem(active)
            } else {
                selectCommand(active)
            }
            break
        }
        case 'Escape': {
            e.preventDefault()
            e.stopPropagation()
            goBack()
            break
        }
    }
}

// ─── Scroll active item into view ────────────────────────────────────────

function scrollIntoView(id) {
    nextTick(() => {
        listRef.value?.querySelector(`[data-id="${CSS.escape(id)}"]`)?.scrollIntoView({ block: 'nearest' })
    })
}

// ─── Highlight matched characters ────────────────────────────────────────

function highlightMatches(text, ranges) {
    if (!ranges.length) return escapeHtml(text)
    let result = ''
    let lastIndex = 0
    for (const [start, end] of ranges) {
        result += escapeHtml(text.slice(lastIndex, start))
        result += '<mark>' + escapeHtml(text.slice(start, end + 1)) + '</mark>'
        lastIndex = end + 1
    }
    result += escapeHtml(text.slice(lastIndex))
    return result
}

function escapeHtml(str) {
    return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')
}

// The aggregated activity carrier of a nested item (session / workspace /
// project-or-worktree row), or null. Drives the right-aligned process-state
// indicator / unread flag shared by all three row kinds.
function itemActivity(item) {
    return item.session || item.workspace || item.project || null
}

defineExpose({ open, close })
</script>

<template>
    <wa-dialog ref="dialogRef" without-header light-dismiss @wa-after-show="onAfterShow" @wa-hide="onHide" @wa-after-hide="onAfterHide">
        <div class="command-palette">
            <!-- Header with search -->
            <div class="palette-header">
                <button
                    v-if="parentCommand"
                    type="button"
                    class="breadcrumb breadcrumb-back"
                    @click="goBack"
                    aria-label="Back to previous level"
                >
                    <wa-icon name="chevron-left" class="breadcrumb-back-icon" />
                    <wa-icon :name="parentCommand.icon" />
                    <span>{{ parentCommand.label }}</span>
                    <wa-icon name="chevron-right" />
                </button>
                <input
                    ref="searchInputRef"
                    type="text"
                    v-model="query"
                    :placeholder="parentCommand ? 'Filter...' : 'Type a command...'"
                    @keydown="handleKeydown"
                    autocomplete="off"
                    spellcheck="false"
                />
            </div>
            <wa-divider />
            <!-- Command list -->
            <div ref="listRef" class="palette-list">
                <!-- Root category mode -->
                <template v-if="!query && !parentCommand">
                    <template v-for="group in commandsByCategory" :key="group.key">
                        <div class="category-sticky">
                            <div class="category-label">{{ group.label }}</div>
                        </div>
                        <div
                            v-for="cmd in group.commands"
                            :key="cmd.id"
                            class="command-item"
                            :class="{ active: cmd.id === activeId }"
                            :data-id="cmd.id"
                            @click="selectCommand(cmd)"
                            @pointerenter="activeId = cmd.id"
                        >
                            <wa-icon :name="cmd.icon" class="command-icon" />
                            <template v-if="cmd.target">
                                <template v-for="t in [cmd.target()]" :key="cmd.id + '-target'">
                                    <span v-if="t" class="command-text-col">
                                        <span class="command-label command-target-line">
                                            <span v-if="t.prefix" class="command-target-prefix">{{ t.prefix }}</span>
                                            <span class="palette-project-dot" :style="t.project?.color ? { '--dot-color': t.project.color } : null"></span>
                                            <template v-if="t.worktree?.parentName">
                                                <span class="command-wt-parent">{{ t.worktree.parentName }}</span>
                                                <wa-icon name="code-branch" auto-width class="command-wt-sep"></wa-icon>
                                            </template>
                                            <span class="command-wt-folder">{{ t.label }}</span>
                                            <wa-icon
                                                v-if="t.project?.untrusted"
                                                name="lock"
                                                label="Untrusted project"
                                                title="This project is not trusted"
                                                class="palette-trust-icon"
                                            ></wa-icon>
                                        </span>
                                        <span v-if="t.path" class="command-path">{{ t.path }}</span>
                                    </span>
                                    <span v-else class="command-label">{{ cmd.label }}</span>
                                </template>
                            </template>
                            <span v-else class="command-label">{{ cmd.label }}</span>
                            <span v-if="cmd.toggled" class="command-toggle">
                                <wa-icon v-if="cmd.toggled()" name="check" />
                            </span>
                            <span v-if="cmd.items" class="command-chevron"><wa-icon name="chevron-right" /></span>
                        </div>
                    </template>
                </template>
                <!-- Search results mode -->
                <template v-else-if="!parentCommand">
                    <div
                        v-for="result in searchResults"
                        :key="result.cmd.id"
                        class="command-item"
                        :class="{ active: result.cmd.id === activeId }"
                        :data-id="result.cmd.id"
                        @click="selectCommand(result.cmd)"
                        @pointerenter="activeId = result.cmd.id"
                    >
                        <wa-icon :name="result.cmd.icon" class="command-icon" />
                        <template v-if="result.cmd.target">
                            <template v-for="t in [result.cmd.target()]" :key="result.cmd.id + '-target'">
                                <span v-if="t" class="command-text-col">
                                    <span class="command-label command-target-line">
                                        <span v-if="t.prefix" class="command-target-prefix">{{ t.prefix }}</span>
                                        <span class="palette-project-dot" :style="t.project?.color ? { '--dot-color': t.project.color } : null"></span>
                                        <template v-if="t.worktree?.parentName">
                                            <span class="command-wt-parent">{{ t.worktree.parentName }}</span>
                                            <wa-icon name="code-branch" auto-width class="command-wt-sep"></wa-icon>
                                        </template>
                                        <span class="command-wt-folder">{{ t.label }}</span>
                                    </span>
                                    <span v-if="t.path" class="command-path">{{ t.path }}</span>
                                </span>
                                <span v-else class="command-label" v-html="result.highlighted" />
                            </template>
                        </template>
                        <span v-else class="command-label" v-html="result.highlighted" />
                        <span v-if="result.categoryLabel" class="command-category">{{ result.categoryLabel }}</span>
                        <span v-if="result.cmd.toggled" class="command-toggle">
                            <wa-icon v-if="result.cmd.toggled()" name="check" />
                        </span>
                        <span v-if="result.cmd.items" class="command-chevron"><wa-icon name="chevron-right" /></span>
                    </div>
                </template>
                <!-- Nested mode -->
                <template v-else>
                    <template v-for="(item, i) in nestedResults" :key="item.id">
                        <!-- Inter-group divider (e.g. between cross-filter pinned and natural sessions) -->
                        <wa-divider
                            v-if="i > 0 && item.group && item.group !== nestedResults[i - 1].group"
                            class="palette-group-divider"
                        ></wa-divider>
                        <div
                            class="command-item"
                            :class="{ active: item.id === activeId, 'command-item--session': !!item.session, 'command-item--workspace': !!item.workspace, 'command-item--project': !!item.project }"
                            :data-id="item.id"
                            @click="selectNestedItem(item)"
                            @pointerenter="activeId = item.id"
                        >
                            <!-- Session row: project color dot + pin icon on the left (mirrors sidebar);
                                 a code-branch marker is added when the session lives in a git worktree. -->
                            <template v-if="item.session">
                                <span
                                    class="palette-project-dot"
                                    :style="item.session.projectColor ? { '--dot-color': item.session.projectColor } : null"
                                ></span>
                                <wa-icon
                                    v-if="item.session.pinned"
                                    name="thumbtack"
                                    class="palette-pin-icon"
                                ></wa-icon>
                                <wa-icon
                                    v-if="item.session.isWorktree"
                                    name="code-branch"
                                    class="palette-session-wt-icon"
                                ></wa-icon>
                            </template>
                            <!-- Workspace row: layer-group icon tinted with the workspace color -->
                            <template v-else-if="item.workspace">
                                <wa-icon
                                    name="layer-group"
                                    class="palette-workspace-icon"
                                    :style="item.workspace.color ? { color: item.workspace.color } : null"
                                ></wa-icon>
                            </template>
                            <!-- Project row: colored dot mirroring the sidebar -->
                            <template v-else-if="item.project">
                                <span
                                    class="palette-project-dot"
                                    :style="item.project.color ? { '--dot-color': item.project.color } : null"
                                ></span>
                            </template>
                            <!-- Regular sub-item: active check, icon, or spacer -->
                            <template v-else>
                                <wa-icon v-if="item.active" name="check" class="command-icon active-check" />
                                <wa-icon v-else-if="item.icon" :name="item.icon" class="command-icon" />
                                <span v-else class="command-icon-spacer" />
                            </template>

                            <!-- Project / worktree sub-item: name on top (a worktree
                                 prefixes it with its parent name + a code-branch
                                 icon), absolute path below (muted). -->
                            <span v-if="item.path || item.worktree" class="command-text-col">
                                <span v-if="item.worktree" class="command-label command-wt-line">
                                    <template v-if="item.worktree.parentName">
                                        <span class="command-wt-parent" v-html="item.worktree.parentHighlighted"></span>
                                        <wa-icon name="code-branch" auto-width class="command-wt-sep"></wa-icon>
                                    </template>
                                    <span class="command-wt-folder" v-html="item.highlighted"></span>
                                    <wa-icon
                                        v-if="item.project?.untrusted"
                                        name="lock"
                                        label="Untrusted project"
                                        title="This project is not trusted"
                                        class="palette-trust-icon"
                                    ></wa-icon>
                                </span>
                                <span v-else class="command-label command-name-line">
                                    <span class="command-name-text" v-html="item.highlighted"></span>
                                    <wa-icon
                                        v-if="item.project?.untrusted"
                                        name="lock"
                                        label="Untrusted project"
                                        title="This project is not trusted"
                                        class="palette-trust-icon"
                                    ></wa-icon>
                                </span>
                                <span v-if="item.path" class="command-path" v-html="item.pathHighlighted" />
                            </span>
                            <span v-else class="command-label" v-html="item.highlighted" />

                            <!-- Session / workspace / project (or worktree) row: aggregated
                                 process state or unread flag on the right -->
                            <template v-if="itemActivity(item)">
                                <ProcessIndicator
                                    v-if="itemActivity(item).processState"
                                    :state="itemActivity(item).processState.state"
                                    :has-active-crons="((itemActivity(item).processState.active_crons?.length) || 0) > 0"
                                    size="small"
                                    class="palette-process-indicator"
                                />
                                <wa-icon
                                    v-else-if="itemActivity(item).hasUnread"
                                    name="eye"
                                    class="palette-unread-icon"
                                ></wa-icon>
                            </template>
                        </div>
                    </template>
                </template>
                <!-- Empty state -->
                <div v-if="visibleItems.length === 0" class="palette-empty">No matching commands</div>
            </div>
        </div>
    </wa-dialog>
</template>

<style scoped>
wa-dialog {
    background: var(--wa-color-surface-default);
    --width: min(720px, calc(100vw - 1rem));
}

wa-dialog::part(body) {
    background: var(--wa-color-surface-default);
    padding: 0;
}
wa-dialog::part(overlay) {
    background: rgba(0, 0, 0, 0.4);
}

.palette-header {
    display: flex;
    align-items: center;
    padding: var(--wa-space-s) var(--wa-space-m);
    gap: var(--wa-space-s);
}
.palette-header input {
    flex: 1;
    border: none;
    outline: none;
    background: transparent;
    box-shadow: none;
    color: var(--wa-color-text-normal);
    font-size: var(--wa-font-size-m);
    font-family: inherit;
    min-width: 0;
}
.palette-header input::placeholder {
    color: var(--wa-color-text-muted);
}
.breadcrumb {
    display: flex;
    align-items: center;
    gap: var(--wa-space-2xs);
    color: var(--wa-color-text-muted);
    font-size: var(--wa-font-size-s);
    white-space: nowrap;
}

/* The breadcrumb doubles as a clickable/tappable "back" control: clicking
   anywhere on it returns to the previous level (mirrors Esc / ← / Backspace),
   so navigating back no longer requires the keyboard. */
.breadcrumb-back {
    border: none;
    background: transparent;
    font: inherit;
    font-size: var(--wa-font-size-s);
    cursor: pointer;
    padding: var(--wa-space-2xs) var(--wa-space-xs);
    margin-inline-start: calc(-1 * var(--wa-space-xs));
    border-radius: var(--wa-border-radius-s);
    transition: background-color 0.1s ease, color 0.1s ease;
}
.breadcrumb-back:hover {
    background: var(--wa-color-surface-lowered);
    color: var(--wa-color-text-normal);
}
.breadcrumb-back:active {
    background: var(--wa-color-surface-border);
}
.breadcrumb-back-icon {
    color: var(--wa-color-text-normal);
}

wa-divider {
    --spacing: 0;
}

.palette-list {
    max-height: min(400px, 60dvh);
    overflow-y: auto;
    padding: 0 0 var(--wa-space-xs) 0;
}

/* Sticky wrapper: pins the group header to the top of the scroll area while
   its commands scroll past. `container-type: scroll-state` makes it a query
   container so the inner label can react to being stuck (see the scroll-state
   query below). The wrapper carries the positioning; the label keeps the
   visual styling. */
.category-sticky {
    position: sticky;
    top: 0;
    z-index: 1;
    container-type: scroll-state;
    padding-block: var(--wa-space-xs) var(--wa-space-2xs);
    background: var(--wa-color-surface-default);
}
.category-label {
    padding: var(--wa-space-2xs) var(--wa-space-m);
    background: var(--wa-color-surface-default);
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-text-muted);
    text-transform: uppercase;
    letter-spacing: 0.05em;
    font-weight: 600;
    user-select: none;
    transition: box-shadow 0.15s ease;
}
/* Progressive enhancement: only when the header is actually stuck to the top,
   lift it off the scrolling content with a soft, theme-aware shadow. Where
   scroll-state queries aren't supported the header still pins — just without
   the shadow. */
@container scroll-state(stuck: top) {
    .category-label {
        box-shadow: 0 2px 5px -2px color-mix(in srgb, var(--wa-color-text-normal) 18%, transparent);
    }
}

.command-item {
    display: flex;
    align-items: center;
    padding: var(--wa-space-xs) var(--wa-space-m);
    cursor: pointer;
    gap: var(--wa-space-s);
    border-radius: var(--wa-border-radius-s);
    margin: 1px var(--wa-space-xs);
    user-select: none;
    /* Keyboard nav scrolls the active row into view; reserve the sticky
       category header's height on top so it lands below the header, not under
       it (harmless in search/nested modes, which have no sticky header). */
    scroll-margin-top: 2rem;
}
.command-item.active {
    background: var(--wa-color-surface-lowered);
}
.command-icon {
    width: 1.25em;
    text-align: center;
    color: var(--wa-color-text-muted);
    flex-shrink: 0;
    font-size: 0.9em;
}
.command-icon-spacer {
    width: 1.25em;
    flex-shrink: 0;
}
.command-label {
    flex: 1;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}
.command-label :deep(mark) {
    background: transparent;
    color: var(--wa-color-success-60);
    font-weight: 600;
    padding: 0;
}
/* Project sub-item: stack the name and its absolute path on two lines. The
   column takes the label's flex slot; min-width:0 lets both lines ellipsize. */
.command-text-col {
    flex: 1;
    min-width: 0;
    display: flex;
    flex-direction: column;
    gap: 1px;
    overflow: hidden;
}
.command-text-col .command-label {
    flex: 0 0 auto;
}
.command-path {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-size: var(--wa-font-size-2xs);
    color: var(--wa-color-text-muted);
}
.command-path :deep(mark) {
    background: transparent;
    color: var(--wa-color-success-60);
    font-weight: 600;
    padding: 0;
}
/* Worktree sub-item first line: [parent] [code-branch] [folder], mirroring
   WorktreeBadge. It stands in for the plain label, so it keeps .command-label
   (for mark styling); flex lets the parent and folder names ellipsize while the
   separator icon stays fixed. */
.command-wt-line {
    display: flex;
    align-items: center;
    gap: var(--wa-space-2xs);
    min-width: 0;
    overflow: visible;
    white-space: normal;
}
.command-wt-parent,
.command-wt-folder {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    min-width: 0;
}
.command-wt-sep {
    flex-shrink: 0;
    color: var(--wa-color-text-quiet);
    font-size: 0.85em;
}

/* Untrusted-project marker (mirrors ProjectBadge/WorktreeBadge): faint closed
   lock — normal text colour at low opacity, so it adapts to light/dark and every
   theme on its own (quieter than --wa-color-text-quiet). */
.palette-trust-icon {
    flex-shrink: 0;
    color: var(--wa-color-text-normal);
    opacity: 0.2;
    font-size: 0.85em;
}

/* Plain project nested row: hold the name and the untrusted marker on one line
   while letting the name ellipsize (the marker stays fixed). Keeps .command-label
   so its :deep(mark) highlight styling still applies to the name. */
.command-name-line {
    display: flex;
    align-items: center;
    gap: var(--wa-space-2xs);
    min-width: 0;
}
.command-name-text {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    min-width: 0;
}

/* Top-level command rendered as a project/worktree badge (e.g. "New Session in
   ●Project"): a leading text prefix, the colored dot, then the project name or
   the worktree's parent + code-branch + folder — mirroring the picker rows. */
.command-target-line {
    display: flex;
    align-items: center;
    gap: var(--wa-space-2xs);
    min-width: 0;
    overflow: hidden;
    white-space: nowrap;
}
.command-target-prefix {
    flex-shrink: 0;
}
.command-chevron,
.command-toggle {
    color: var(--wa-color-text-muted);
    flex-shrink: 0;
    font-size: 0.85em;
}

/* Search-mode only: the command's group label, right-aligned and muted, so
   identically-labelled commands (e.g. "Change Default Model…" for Claude vs
   Codex) can be told apart — the category headers shown in root mode are
   absent once results are flattened by the fuzzy search. */
.command-category {
    flex-shrink: 0;
    color: var(--wa-color-text-muted);
    font-size: var(--wa-font-size-xs);
    white-space: nowrap;
    user-select: none;
}
.active-check {
    color: var(--wa-color-success-60);
}

/* Divider rendered between session groups (extra → pinned → active →
   natural) in nested mode; matches the sidebar's in-list divider. */
.palette-group-divider {
    --width: var(--divider-size);
    --spacing: var(--wa-space-2xs);
    margin-inline: var(--wa-space-m);
}

/* Session row: colored project dot on the left (inline with the label). */
.palette-project-dot {
    width: var(--wa-space-s);
    height: var(--wa-space-s);
    border-radius: 50%;
    flex-shrink: 0;
    border: 1px solid;
    box-sizing: border-box;
    background-color: var(--dot-color, transparent);
    border-color: var(--dot-color, var(--wa-color-border-quiet));
}

/* Session row: pin thumbtack, same color/rotation as the sidebar. */
.palette-pin-icon {
    flex-shrink: 0;
    font-size: var(--wa-font-size-2xs);
    color: var(--wa-color-yellow-80) !important;
    transform: rotate(30deg);
}

/* Session row: code-branch marker before the title when the session lives in a
   git worktree (mirrors WorktreeBadge's separator icon). */
.palette-session-wt-icon {
    flex-shrink: 0;
    font-size: var(--wa-font-size-2xs);
    color: var(--wa-color-text-quiet);
}

/* Workspace row: layer-group icon inline with the label; the `style` binding
   tints it with the workspace's configured color when one is set. */
.palette-workspace-icon {
    width: 1.25em;
    text-align: center;
    flex-shrink: 0;
    font-size: 0.9em;
    color: var(--wa-color-text-muted);
}

/* Session row: right-aligned process state indicator. */
.palette-process-indicator {
    margin-left: auto;
    flex-shrink: 0;
    font-size: var(--wa-font-size-xs);
}

/* Session row: right-aligned unread flag (shown when no process state). */
.palette-unread-icon {
    margin-left: auto;
    flex-shrink: 0;
    color: var(--wa-color-warning-60);
    font-size: var(--wa-font-size-xs);
}

.palette-empty {
    padding: var(--wa-space-l) var(--wa-space-m);
    text-align: center;
    color: var(--wa-color-text-muted);
    font-style: italic;
}
</style>
