<script setup>
// ArtifactBookmarkButton.vue — an icon-only bookmark toggle, styled like the
// session pin button (coloured when bookmarked, quiet/grey otherwise). Hosts the
// create/edit dialog. Placed in the file-path header (desktop) and the mobile
// files-panel header — never in a dedicated bar.
import { ref, computed, useId } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useDataStore } from '../../stores/data'
import ArtifactBookmarkDialog from './ArtifactBookmarkDialog.vue'
import AppTooltip from '../ui/AppTooltip.vue'
import { ARTIFACT_ICON } from '../../utils/artifactBookmark'

const props = defineProps({
    sessionId: { type: String, required: true },
    relativePath: { type: String, required: true },
})

// Multi-root template (view + toggle buttons, their tooltips, the dialog) → Vue
// can't auto-inherit a fallthrough class onto a single root, and warns. Consumers
// (the file-path header, the mobile files-panel header) pass a positioning class,
// so route $attrs explicitly onto the primary (toggle) button — the always-present,
// right-most element, which is also where a trailing margin belongs.
defineOptions({ inheritAttrs: false })

const store = useDataStore()
const route = useRoute()
const router = useRouter()
const dialogRef = ref(null)
const buttonId = `artifact-bookmark-${useId()}`
const viewButtonId = `artifact-view-${useId()}`

const bookmark = computed(() => store.artifactBookmarkFor(props.sessionId, props.relativePath))

const BOOKMARK_TOOLTIP = {
    project: 'Bookmarked in project',
    workspace: 'Bookmarked in workspace',
    all: 'Bookmarked everywhere',
}
const bookmarkTooltip = computed(() =>
    bookmark.value
        ? (BOOKMARK_TOOLTIP[bookmark.value.scope] || 'Bookmarked')
        : 'Bookmark this artifact',
)

function openDialog() {
    dialogRef.value?.open(bookmark.value)
}

// Open this bookmarked artifact in the Artifacts list view, keeping the current
// scope (all-projects + workspace, or the current single project).
function viewInArtifacts() {
    const id = bookmark.value?.id
    if (id == null) return
    if (route.name?.startsWith('projects-')) {
        const query = route.query.workspace ? { workspace: route.query.workspace } : {}
        router.push({ name: 'projects-artifacts', params: { bookmarkId: String(id) }, query })
    } else {
        router.push({
            name: 'project-artifacts',
            params: { projectId: route.params.projectId, bookmarkId: String(id) },
        })
    }
}
</script>

<template>
    <!-- View this bookmarked artifact in the Artifacts list (only when bookmarked),
         to the left of the bookmark toggle. -->
    <wa-button
        v-if="bookmark"
        :id="viewButtonId"
        appearance="plain"
        size="small"
        variant="neutral"
        class="bookmark-button reduced-height"
        @click.stop="viewInArtifacts"
    >
        <wa-icon :name="ARTIFACT_ICON" label="Open in the artifacts list"></wa-icon>
    </wa-button>
    <AppTooltip v-if="bookmark" :for="viewButtonId">Open in the artifacts list</AppTooltip>
    <wa-button
        v-bind="$attrs"
        :id="buttonId"
        appearance="plain"
        size="small"
        :variant="bookmark ? 'brand' : 'neutral'"
        :class="['bookmark-button', 'reduced-height', { 'bookmark-button--active': bookmark }]"
        @click.stop="openDialog"
    >
        <wa-icon name="bookmark" :variant="bookmark ? 'solid' : 'regular'" label="Bookmark"></wa-icon>
    </wa-button>
    <AppTooltip :for="buttonId">{{ bookmarkTooltip }}</AppTooltip>
    <ArtifactBookmarkDialog ref="dialogRef" :session-id="sessionId" :relative-path="relativePath" />
</template>

<style scoped>
.bookmark-button {
    opacity: 0.55;
    transition: opacity 0.15s;
    flex-shrink: 0;
}
.bookmark-button:hover {
    opacity: 1;
}
.bookmark-button--active {
    opacity: 1;
    &::part(base) {
        color: var(--wa-color-brand-60);
    }
}
</style>
