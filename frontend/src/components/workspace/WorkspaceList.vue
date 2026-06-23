<script setup>
// WorkspaceList.vue - Displays workspace cards on the Home page with archived toggle
import { computed } from 'vue'
import { useSettingsStore } from '../../stores/settings'
import { useWorkspacesStore } from '../../stores/workspaces'
import WorkspaceCard from './WorkspaceCard.vue'
import AppTooltip from '../ui/AppTooltip.vue'
import HelpIconButton from '../help/HelpIconButton.vue'

const settingsStore = useSettingsStore()
const workspacesStore = useWorkspacesStore()

const emit = defineEmits(['select', 'menu-select', 'manage', 'create'])

const showArchivedWorkspaces = computed(() => settingsStore.isShowArchivedWorkspaces)
const hasArchivedWorkspaces = computed(() => workspacesStore.hasArchivedWorkspaces)

const visibleWorkspaces = computed(() =>
    workspacesStore.getAllWorkspaces.filter(ws =>
        showArchivedWorkspaces.value || !ws.archived
    )
)
</script>

<template>
    <div>
        <div class="section-header">
            <span class="section-label">Workspaces <HelpIconButton help-key="workspaces" label="What's a workspace?" /></span>
            <div class="header-actions">
                <wa-switch
                    v-if="hasArchivedWorkspaces"
                    size="small"
                    class="show-archived-toggle"
                    :checked="showArchivedWorkspaces"
                    @change="(e) => settingsStore.setShowArchivedWorkspaces(e.target.checked)"
                >
                    Show archived
                </wa-switch>
                <wa-button
                    id="ws-list-manage-btn"
                    variant="neutral"
                    appearance="plain"
                    size="small"
                    @click="emit('manage')"
                >
                    <wa-icon name="gear"></wa-icon>
                </wa-button>
                <AppTooltip for="ws-list-manage-btn">Manage workspaces</AppTooltip>
                <wa-button
                    id="ws-list-create-btn"
                    variant="neutral"
                    appearance="plain"
                    size="small"
                    @click="emit('create')"
                >
                    <wa-icon name="plus"></wa-icon>
                </wa-button>
                <AppTooltip for="ws-list-create-btn">New workspace</AppTooltip>
            </div>
        </div>
        <wa-callout v-if="!visibleWorkspaces.length" variant="brand" class="workspace-hint">
            <wa-icon slot="icon" name="lightbulb"></wa-icon>
            Workspaces let you group projects together so you can focus on a subset of your work.
            Create one with the <wa-icon name="plus" auto-width></wa-icon> button above.
        </wa-callout>
        <div v-else class="workspace-cards">
            <WorkspaceCard
                v-for="ws in visibleWorkspaces"
                :key="ws.id"
                :workspace="ws"
                @select="emit('select', $event)"
                @menu-select="(event, workspace) => emit('menu-select', event, workspace)"
            />
        </div>
    </div>
</template>

<style scoped>
.section-header {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: space-between;
    column-gap: var(--wa-space-m);
    font-size: var(--wa-font-size-s);
    font-weight: 600;
    color: var(--wa-color-text-quiet);
}

.show-archived-toggle {
    font-size: var(--wa-font-size-s);
    text-transform: none;
    letter-spacing: normal;
    font-weight: normal;
    margin-right: var(--wa-space-s);
}

.section-label {
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: var(--wa-color-text-normal);
}

.header-actions {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    margin-left: auto;
}

.workspace-cards {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-m);
}
</style>
