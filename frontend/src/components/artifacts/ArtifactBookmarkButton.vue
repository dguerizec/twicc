<script setup>
// ArtifactBookmarkButton.vue — an icon-only bookmark toggle, styled like the
// session pin button (coloured when bookmarked, quiet/grey otherwise). Hosts the
// create/edit dialog. Placed in the file-path header (desktop) and the mobile
// files-panel header — never in a dedicated bar.
import { ref, computed, useId } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useDataStore } from '../../stores/data'
import { useSettingsStore } from '../../stores/settings'
import { useSharesStore } from '../../stores/shares'
import { isShareOutdated } from '../../utils/shareStatus'
import { useNetworkDenials } from '../../composables/useNetworkDenials'
import { toast } from '../../composables/useToast'
import ArtifactBookmarkDialog from './ArtifactBookmarkDialog.vue'
import AppTooltip from '../ui/AppTooltip.vue'
import { ARTIFACT_ICON, suggestArtifactBookmarkName } from '../../utils/artifactBookmark'

const props = defineProps({
    sessionId: { type: String, required: true },
    relativePath: { type: String, required: true },
    // Fed to the dialog / share path to auto-suggest a bookmark name (abs path
    // for the on-demand HTML fetch; content when the host already has it).
    fileAbsPath: { type: String, default: null },
    htmlContent: { type: String, default: null },
})

// Multi-root template (view + toggle buttons, their tooltips, the dialog) → Vue
// can't auto-inherit a fallthrough class onto a single root, and warns. Consumers
// (the file-path header, the mobile files-panel header) pass a positioning class,
// so route $attrs explicitly onto the primary (toggle) button — the always-present,
// right-most element, which is also where a trailing margin belongs.
defineOptions({ inheritAttrs: false })

const store = useDataStore()
const settingsStore = useSettingsStore()
const sharesStore = useSharesStore()
const route = useRoute()
const router = useRouter()
const dialogRef = ref(null)
const buttonId = `artifact-bookmark-${useId()}`
const viewButtonId = `artifact-view-${useId()}`
const shareButtonId = `artifact-share-${useId()}`

const bookmark = computed(() => store.artifactBookmarkFor(props.sessionId, props.relativePath))

// Broker hosts a shared/previewed artifact tried to reach that the owner hasn't
// allowed or denied yet → the bookmark toggle (which opens the edit dialog's
// Network access section) warns + pulses so the owner acts.
const { pendingCount } = useNetworkDenials(bookmark)

const BOOKMARK_TOOLTIP = {
    project: 'Bookmarked in project',
    workspace: 'Bookmarked in workspace',
    all: 'Bookmarked everywhere',
}
const bookmarkTooltip = computed(() => {
    if (pendingCount.value > 0) {
        return `${pendingCount.value} network host${pendingCount.value > 1 ? 's' : ''} awaiting your decision`
    }
    return bookmark.value
        ? (BOOKMARK_TOOLTIP[bookmark.value.scope] || 'Bookmarked')
        : 'Bookmark this artifact'
})

function openDialog() {
    dialogRef.value?.open(bookmark.value)
}

// Share entry point. A share targets a bookmark, so an as-yet-unbookmarked
// artifact is bookmarked first (name = the suggested name, project scope) and
// then shared. Routes through the globally-mounted artifact ShareDialog
// (ProjectView) via the shared window event.
const sharingEnabled = computed(() => !!settingsStore.getShareBaseUrl)
const activeShareCount = computed(() =>
    bookmark.value ? sharesStore.activeCountForBookmark(bookmark.value.id) : 0
)
// Links serving a stale copy (the live files changed after their snapshot): the
// share button warns so the owner remembers to push the update.
const outdatedShareCount = computed(() =>
    bookmark.value ? sharesStore.forBookmark(bookmark.value.id).filter(isShareOutdated).length : 0
)
const shareVariant = computed(() =>
    outdatedShareCount.value > 0 ? 'warning' : (activeShareCount.value > 0 ? 'brand' : 'neutral')
)
const shareTooltip = computed(() => {
    if (!sharingEnabled.value) return 'Configure a share host in Settings → Sharing to create links'
    if (outdatedShareCount.value > 0) {
        return `Share artifact — ${outdatedShareCount.value} update${outdatedShareCount.value > 1 ? 's' : ''} to push`
    }
    if (activeShareCount.value > 0) {
        return `Share artifact (${activeShareCount.value} active link${activeShareCount.value > 1 ? 's' : ''})`
    }
    return bookmark.value ? 'Share artifact' : 'Share artifact (bookmarks it first)'
})
async function shareArtifact() {
    if (!sharingEnabled.value) return
    let b = bookmark.value
    if (!b) {
        const name = suggestArtifactBookmarkName({
            relativePath: props.relativePath,
            htmlContent: props.htmlContent,
        })
        try {
            b = await store.createArtifactBookmark({
                sessionId: props.sessionId, relativePath: props.relativePath, name, scope: 'project',
            })
        } catch (err) {
            toast.error(err?.message || 'Failed to bookmark artifact')
            return
        }
    }
    window.dispatchEvent(new CustomEvent('twicc:open-share-dialog', {
        detail: { bookmarkId: b.id, allowedHosts: b.allowed_hosts || {}, title: b.name || '' },
    }))
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
    <!-- Share this artifact (only when a share host is configured). Left of the
         bookmark toggle so the toggle stays right-most (where $attrs + the
         trailing margin land). -->
    <wa-button
        v-if="sharingEnabled"
        :id="shareButtonId"
        appearance="plain"
        size="small"
        :variant="shareVariant"
        :class="['bookmark-button', 'reduced-height', {
            'bookmark-button--active': activeShareCount > 0 && outdatedShareCount === 0,
            'bookmark-button--attention': outdatedShareCount > 0 }]"
        @click.stop="shareArtifact"
    >
        <wa-icon name="share-nodes" label="Share artifact"></wa-icon>
    </wa-button>
    <AppTooltip v-if="sharingEnabled" :for="shareButtonId">{{ shareTooltip }}</AppTooltip>
    <wa-button
        v-bind="$attrs"
        :id="buttonId"
        appearance="plain"
        size="small"
        :variant="pendingCount > 0 ? 'warning' : (bookmark ? 'brand' : 'neutral')"
        :class="['bookmark-button', 'reduced-height', {
            'bookmark-button--active': bookmark && pendingCount === 0,
            'bookmark-button--attention': pendingCount > 0 }]"
        @click.stop="openDialog"
    >
        <wa-icon name="bookmark" :variant="bookmark ? 'solid' : 'regular'" label="Bookmark"></wa-icon>
    </wa-button>
    <AppTooltip :for="buttonId">{{ bookmarkTooltip }}</AppTooltip>
    <ArtifactBookmarkDialog
        ref="dialogRef"
        :session-id="sessionId"
        :relative-path="relativePath"
        :file-abs-path="fileAbsPath"
        :html-content="htmlContent"
    />
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
/* Something needs the owner's attention → warning tint + pulse (matches the
   pending-request indicators elsewhere). Shared by the share button (updates to
   push) and the bookmark toggle (network hosts awaiting a decision). */
.bookmark-button--attention {
    opacity: 1;
    animation: pending-pulse 1.5s ease-in-out infinite;
    &::part(base) {
        color: var(--wa-color-warning-60);
    }
}
@keyframes pending-pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.3; }
}
</style>
