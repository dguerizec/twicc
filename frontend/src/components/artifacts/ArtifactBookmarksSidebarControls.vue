<script setup>
// ArtifactBookmarksSidebarControls.vue — the sidebar's second header row in Artifacts
// mode: a trimmed options dropdown ("Switch to sessions" + "Compact view") and
// an artifact filter input. No advanced (full-text) search button — that
// indexes session history, not bookmarks.
//
// Mirrors SessionsSidebarControls' contract:
//  - update:searchQuery : v-model passthrough for the filter input
//  - optionSelect       : the @wa-select event from the options dropdown
//                         (only "compact-view" reaches ProjectView's handler)
//  - searchKeydown      : the keydown event from the filter input (drives
//                         ArtifactBookmarkList keyboard navigation, like Sessions mode)
//  - switchToSessions   : the "Switch to sessions" option
import { ref } from 'vue'
import AppTooltip from '../ui/AppTooltip.vue'

defineProps({
    searchQuery: { type: String, default: '' },
    compactView: { type: Boolean, default: false },
})

const emit = defineEmits([
    'update:searchQuery',
    'optionSelect',
    'searchKeydown',
    'switchToSessions',
])

const searchInputRef = ref(null)

function onOptionSelect(event) {
    if (event.detail?.item?.value === 'switch-sessions') {
        emit('switchToSessions')
        return
    }
    emit('optionSelect', event)
}

function focus() {
    searchInputRef.value?.focus()
}

defineExpose({ focus })
</script>

<template>
    <div class="sidebar-header-row">
        <!-- Artifacts list options dropdown (trimmed) -->
        <wa-dropdown
            placement="bottom-end"
            class="session-options-dropdown"
            @wa-select="onOptionSelect"
        >
            <wa-button
                id="bookmark-options-button"
                slot="trigger"
                variant="neutral"
                appearance="filled-outlined"
                size="small"
            >
                <wa-icon name="sliders"></wa-icon>
            </wa-button>
            <wa-dropdown-item
                type="checkbox"
                value="compact-view"
                :checked="compactView"
            >
                Compact view
            </wa-dropdown-item>
            <wa-divider></wa-divider>
            <wa-dropdown-item value="switch-sessions">
                <wa-icon slot="icon" name="comments"></wa-icon>
                Switch to sessions list
            </wa-dropdown-item>
        </wa-dropdown>
        <AppTooltip for="bookmark-options-button">Artifacts list options</AppTooltip>

        <!-- Filter input -->
        <wa-input
            ref="searchInputRef"
            :value="searchQuery"
            @input="emit('update:searchQuery', $event.target.value)"
            placeholder="Filter artifacts..."
            size="small"
            with-clear
            class="session-search"
            @keydown="emit('searchKeydown', $event)"
        >
            <wa-icon slot="start" name="magnifying-glass"></wa-icon>
        </wa-input>
    </div>
</template>

<style scoped>
.sidebar-header-row {
    width: 100%;
    display: flex;
    justify-content: stretch;
    align-items: center;
    gap: var(--wa-space-s);
}

.session-search {
    flex: 1;
    min-width: 3.5rem;
    max-width: 100%;
    &:hover {
        z-index: 10;
        min-width: min(10rem, calc(100vw - 100px));
    }
}

.session-options-dropdown {
    flex-shrink: 0;
    &::part(menu) {
        max-width: 90vw !important;
        width: auto;
    }
}
</style>
