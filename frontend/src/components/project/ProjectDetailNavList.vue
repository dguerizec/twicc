<script setup>
// ProjectDetailNavList.vue - Horizontal navigation list of workspaces and/or projects.
//
// Quick navigation displayed in header panels:
// - "All Projects" mode: workspaces (getSelectableWorkspaces order) + all projects (named first, then unnamed, mtime desc)
// - Workspace mode: ↑ All Projects + projects in the workspace's custom order
// - Single project mode: ↑ All Projects + ↑ workspaces the project belongs to (store order, respecting archived setting)
//
// Each item shows an icon (layer-group for workspaces, colored dot for projects),
// the name, and aggregated indicators (CodeCommentsIndicator + AggregatedProcessIndicator).

import { computed } from 'vue'
import { RouterLink } from 'vue-router'
import { useDataStore, ALL_PROJECTS_ID } from '../../stores/data'
import { useSettingsStore } from '../../stores/settings'
import { useWorkspacesStore } from '../../stores/workspaces'
import { isWorkspaceProjectId, extractWorkspaceId } from '../../utils/workspaceIds'
import ProjectBadge from './ProjectBadge.vue'
import AggregatedProcessIndicator from '../ui/AggregatedProcessIndicator.vue'
import CodeCommentsIndicator from '../ui/CodeCommentsIndicator.vue'

const props = defineProps({
    projectId: {
        type: String,
        required: true,
    },
})

const dataStore = useDataStore()
const settingsStore = useSettingsStore()
const workspacesStore = useWorkspacesStore()

const isAllProjectsMode = computed(() => props.projectId === ALL_PROJECTS_ID)
const isWorkspaceMode = computed(() => isWorkspaceProjectId(props.projectId))
const workspaceId = computed(() => isWorkspaceMode.value ? extractWorkspaceId(props.projectId) : null)

const items = computed(() => {
    const result = []

    if (isAllProjectsMode.value) {
        // 1. Workspaces in getSelectableWorkspaces order
        for (const ws of workspacesStore.getSelectableWorkspaces) {
            result.push({
                type: 'workspace',
                id: ws.id,
                name: ws.name,
                color: ws.color,
                projectIds: workspacesStore.getVisibleProjectIds(ws.id),
                to: { name: 'projects-all', query: { workspace: ws.id } },
            })
        }

        // Divider between workspaces and projects (only if there are workspaces)
        if (result.length > 0) {
            result.push({ type: 'divider' })
        }

        // 2. All visible projects: named first, then unnamed (both in mtime desc from store).
        //    Worktrees are excluded from the project links (getListableProjects).
        const showArchived = settingsStore.isShowArchivedProjects
        const projects = dataStore.getListableProjects.filter(p => showArchived || !p.archived)

        const namedProjects = projects.filter(p => p.name !== null)
        const unnamedProjects = projects.filter(p => p.name === null && p.directory)

        for (const p of namedProjects) {
            result.push({
                type: 'project',
                id: p.id,
                projectIds: [p.id],
                to: { name: 'project', params: { projectId: p.id } },
            })
        }
        if (namedProjects.length > 0 && unnamedProjects.length > 0) {
            result.push({ type: 'divider' })
        }
        for (const p of unnamedProjects) {
            result.push({
                type: 'project',
                id: p.id,
                projectIds: [p.id],
                to: { name: 'project', params: { projectId: p.id } },
            })
        }
    } else if (isWorkspaceMode.value) {
        // Go-up item: All Projects
        result.push({
            type: 'up',
            id: 'all-projects',
            label: 'All Projects',
            to: { name: 'projects-all' },
        })

        // Projects in workspace's custom order (worktree links excluded)
        const projectIds = workspacesStore.getVisibleProjectIds(workspaceId.value)
            .filter(pid => !dataStore.getProject(pid)?.worktree_of)
        if (projectIds.length) {
            result.push({ type: 'divider' })
        }
        for (const pid of projectIds) {
            const p = dataStore.getProject(pid)
            if (!p) continue
            result.push({
                type: 'project',
                id: p.id,
                projectIds: [p.id],
                to: { name: 'project', params: { projectId: p.id } },
            })
        }
    } else {
        // Go-up item: All Projects
        result.push({
            type: 'up',
            id: 'all-projects',
            label: 'All Projects',
            to: { name: 'projects-all' },
        })

        // Single project mode: workspaces containing this project (go up one level)
        const showArchivedWs = settingsStore.isShowArchivedWorkspaces
        const wsItems = []
        for (const ws of workspacesStore.workspaces) {
            if (!showArchivedWs && ws.archived) continue
            if (!ws.projectIds.includes(props.projectId)) continue
            wsItems.push({
                type: 'workspace',
                isUp: true,
                id: ws.id,
                name: ws.name,
                color: ws.color,
                projectIds: workspacesStore.getVisibleProjectIds(ws.id),
                to: { name: 'projects-all', query: { workspace: ws.id } },
            })
        }
        if (wsItems.length) {
            result.push({ type: 'divider' })
            result.push(...wsItems)
        }
    }

    return result
})

</script>

<template>
    <div v-if="items.length" class="project-nav-list">
        <template v-for="(item, index) in items" :key="item.type === 'divider' ? `divider-${index}` : `${item.type}-${item.id}`">
            <span v-if="item.type === 'divider'" class="nav-divider"></span>
            <RouterLink
                v-else
                :to="item.to"
                class="nav-item"
            >
            <template v-if="item.type === 'up'">
                <wa-icon name="arrow-up" auto-width class="nav-up-icon"></wa-icon>
                <span class="nav-item-name">{{ item.label }}</span>
            </template>
            <template v-else-if="item.type === 'workspace'">
                <wa-icon v-if="item.isUp" name="arrow-up" auto-width class="nav-up-icon"></wa-icon>
                <wa-icon
                    name="layer-group"
                    auto-width
                    class="nav-item-icon"
                    :style="item.color ? { color: item.color } : null"
                ></wa-icon>
                <span class="nav-item-name">{{ item.name }}</span>
            </template>
            <ProjectBadge v-else :project-id="item.id" use-directory-for-unnamed gap="var(--wa-space-2xs)" />

            <template v-if="item.projectIds">
                <CodeCommentsIndicator :project-ids="item.projectIds" :show-tooltip="false" />
                <AggregatedProcessIndicator :project-ids="item.projectIds" size="small" />
            </template>
            </RouterLink>
        </template>
    </div>
</template>

<style scoped>
.project-nav-list {
    display: flex;
    flex-wrap: wrap;
    column-gap: var(--wa-space-l);
    row-gap: var(--wa-space-2xs);
}

.nav-item {
    display: inline-flex;
    align-items: center;
    gap: var(--wa-space-xs);
    color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-s);
    text-decoration: none;
    transition: background-color 0.15s, color 0.15s;
}

.nav-item:hover {
    background: var(--wa-color-surface-alt);
    color: var(--wa-color-text-normal);
}

.nav-divider {
    width: 1px;
    align-self: stretch;
    background: var(--wa-color-surface-border);
}

.nav-up-icon {
    font-size: var(--wa-font-size-xs);
}

.nav-item-icon {
    font-size: var(--wa-font-size-s);
}

.nav-item-name {
    white-space: nowrap;
}
</style>
