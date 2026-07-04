<script setup>
// Browser pane: embeds a user-chosen URL (typically the project's dev server)
// in a plain iframe. Unlike the Artifacts preview there is NO sandbox, NO CSP
// lockdown and NO network broker — deliberate: the page runs exactly as in a
// normal browser tab (direct network, service workers, HMR websockets), it
// just cannot reach into TwiCC (cross-origin isolation applies both ways).
//
// Cross-origin iframes expose neither their current location nor their
// history, so the toolbar keeps its OWN history: it records the URLs
// navigated via the address bar / Back / Forward / Home only. Links followed
// inside the page are invisible to it, and Refresh re-creates the iframe on
// the last recorded URL (in-page navigation is lost). Deliberate, documented
// limits — see the info tooltip.
import { computed, ref, useId, watch } from 'vue'
import { useDataStore } from '../../stores/data'
import { useWorkspacesStore } from '../../stores/workspaces'
import { apiFetch } from '../../utils/api'
import { resolveProjectBrowserUrl } from '../../utils/browserDefaults'
import { normalizeBrowserUrl } from '../../utils/browserUrl'
import AppTooltip from '../ui/AppTooltip.vue'

const props = defineProps({
    projectId: { type: String, default: null },
    // True while the Browser tab is the shown tab in its region — drives the
    // lazy first load (never fetch a dev server for a tab that was never opened).
    active: { type: Boolean, default: false },
    // Bumped by SessionView on explicit tab activation → focus the address bar.
    focusRequest: { type: Number, default: 0 },
})

const store = useDataStore()
const workspacesStore = useWorkspacesStore()
const instanceId = useId()

// ── Default URL: project chain first, then the first non-archived workspace
// containing the project (worktree-aware) that carries a browserUrl.
const projectDefaultUrl = computed(() => resolveProjectBrowserUrl(props.projectId, store.projects))
const workspaceDefaultUrl = computed(() => {
    if (!props.projectId) return null
    const ws = workspacesStore.workspaces.find(
        (w) => !w.archived && w.browserUrl && workspacesStore.workspaceContainsProject(w.id, props.projectId)
    )
    return ws?.browserUrl || null
})
const defaultUrl = computed(() => projectDefaultUrl.value || workspaceDefaultUrl.value)

// ── Navigation state. `urlHistory` holds address-bar-level navigations only
// (named to avoid shadowing window.history inside this component).
const currentUrl = ref('')       // URL the iframe was last pointed at ('' = blank state)
const inputUrl = ref('')         // address bar edit buffer
const urlHistory = ref([])
const historyIndex = ref(-1)
const frameKey = ref(0)          // bump = recreate the iframe (navigate / refresh)
const loading = ref(false)
const everActivated = ref(false)

const canGoBack = computed(() => historyIndex.value > 0)
const canGoForward = computed(() => historyIndex.value < urlHistory.value.length - 1)

// An https TwiCC page cannot embed an http iframe (the browser blocks it
// silently as mixed content) — explain instead of showing a dead frame.
const mixedContentBlocked = computed(
    () => window.location.protocol === 'https:' && currentUrl.value.startsWith('http://')
)

function showFrame(url) {
    currentUrl.value = url
    inputUrl.value = url
    frameKey.value++
    loading.value = true
    probeCurrentUrl()
}

function navigate(rawInput) {
    const url = normalizeBrowserUrl(rawInput)
    if (!url) return
    // Truncate forward entries, then push (skip contiguous repeats).
    const stack = urlHistory.value.slice(0, historyIndex.value + 1)
    if (stack[stack.length - 1] !== url) stack.push(url)
    urlHistory.value = stack
    historyIndex.value = stack.length - 1
    showFrame(url)
}

function goBack() {
    if (!canGoBack.value) return
    historyIndex.value--
    showFrame(urlHistory.value[historyIndex.value])
}

function goForward() {
    if (!canGoForward.value) return
    historyIndex.value++
    showFrame(urlHistory.value[historyIndex.value])
}

function refresh() {
    if (!currentUrl.value) return
    showFrame(currentUrl.value)
}

function goHome() {
    if (defaultUrl.value) navigate(defaultUrl.value)
}

function openExternal() {
    if (currentUrl.value) window.open(currentUrl.value, '_blank', 'noopener')
}

function onAddressSubmit() {
    navigate(inputUrl.value)
}

function onFrameLoad() {
    // Fires even for framing-refused pages (the error document loads) — it
    // only means "network settled", not "content visible".
    loading.value = false
}

// ── Advisory probe: the two silent-blank-frame cases (server down, framing
// refused) are invisible client-side; ask the backend. Failures are ignored —
// the endpoint is advisory.
const probeResult = ref(null)

async function probeCurrentUrl() {
    probeResult.value = null
    const url = currentUrl.value
    if (!url) return
    const key = frameKey.value
    try {
        const response = await apiFetch(`/api/browser-frame-check/?url=${encodeURIComponent(url)}`)
        if (!response.ok) return
        const data = await response.json()
        if (frameKey.value !== key) return // user navigated meanwhile
        if (data.reachable === false || data.embeddable === false) probeResult.value = data
    } catch {
        // advisory only
    }
}

// ── Lazy init: on first activation, auto-load the resolved default.
watch(
    () => props.active,
    (active) => {
        if (!active || everActivated.value) return
        everActivated.value = true
        if (defaultUrl.value) navigate(defaultUrl.value)
    },
    { immediate: true }
)

// ── Focus the address bar on explicit tab activation (keyboard / tab click),
// mirroring the other ACTIVATION_FOCUS_TABS panels.
const addressInputRef = ref(null)
watch(
    () => props.focusRequest,
    () => {
        requestAnimationFrame(() => addressInputRef.value?.focus())
    }
)

// ── Save-URL menu -------------------------------------------------------------
const project = computed(() => store.getProject(props.projectId))
const mainRepoProject = computed(() =>
    project.value?.worktree_of ? store.getProject(project.value.worktree_of) : null
)
const memberWorkspaces = computed(() =>
    workspacesStore.workspaces.filter(
        (w) => !w.archived && workspacesStore.workspaceContainsProject(w.id, props.projectId)
    )
)
const canSave = computed(() => !!currentUrl.value && !!project.value)
const saveError = ref('')

async function saveToProject(projectId) {
    try {
        const response = await apiFetch(`/api/projects/${projectId}/`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ default_browser_url: currentUrl.value }),
        })
        if (!response.ok) {
            const data = await response.json().catch(() => ({}))
            throw new Error(data.error || `Failed to save (${response.status})`)
        }
        store.updateProject(await response.json())
    } catch (e) {
        saveError.value = e.message || 'Failed to save URL'
    }
}

function onSaveSelect(event) {
    const value = event.detail?.item?.value
    if (!value || !currentUrl.value) return
    saveError.value = ''
    if (value === 'project') {
        saveToProject(props.projectId)
    } else if (value === 'main-repo') {
        saveToProject(project.value.worktree_of)
    } else if (value.startsWith('ws:')) {
        workspacesStore.updateWorkspace(value.slice(3), { browserUrl: currentUrl.value })
    }
}
</script>

<template>
    <div class="browser-pane">
        <div class="browser-toolbar">
            <wa-button appearance="plain" size="small" class="browser-btn" :disabled="!canGoBack" title="Back" @click="goBack">
                <wa-icon name="arrow-left"></wa-icon>
            </wa-button>
            <wa-button appearance="plain" size="small" class="browser-btn" :disabled="!canGoForward" title="Forward" @click="goForward">
                <wa-icon name="arrow-right"></wa-icon>
            </wa-button>
            <wa-button appearance="plain" size="small" class="browser-btn" :disabled="!currentUrl" title="Refresh (reloads the last entered URL)" @click="refresh">
                <wa-icon name="rotate-right"></wa-icon>
            </wa-button>
            <wa-button appearance="plain" size="small" class="browser-btn" :disabled="!defaultUrl" :title="defaultUrl ? `Home — ${defaultUrl}` : 'Home (no saved URL for this project)'" @click="goHome">
                <wa-icon name="house"></wa-icon>
            </wa-button>

            <wa-input
                ref="addressInputRef"
                class="browser-address"
                size="small"
                autocomplete="off"
                placeholder="Enter a URL — e.g. localhost:5173"
                :value="inputUrl"
                @input="inputUrl = $event.target.value"
                @keydown.enter.prevent="onAddressSubmit"
            >
                <wa-spinner v-if="loading" slot="start"></wa-spinner>
                <wa-icon v-else slot="start" name="globe"></wa-icon>
            </wa-input>

            <!-- Save current URL as a project / workspace default. WA custom
                 events are stopped from bubbling (a nested dropdown's wa-show /
                 wa-hide would otherwise reach same-named ancestor handlers). -->
            <wa-dropdown
                placement="bottom-end"
                @click.stop
                @wa-select.stop="onSaveSelect"
                @wa-show.stop
                @wa-hide.stop
                @wa-after-show.stop
                @wa-after-hide.stop
            >
                <wa-button slot="trigger" appearance="plain" size="small" class="browser-btn" :disabled="!canSave" title="Save this URL as a default…">
                    <wa-icon name="bookmark"></wa-icon>
                </wa-button>
                <wa-dropdown-item disabled class="save-menu-header">Save current URL as default for…</wa-dropdown-item>
                <wa-dropdown-item value="project" :disabled="project?.default_browser_url === currentUrl">
                    <wa-icon slot="icon" name="folder"></wa-icon>
                    {{ store.getProjectDisplayName(props.projectId) }}
                    <span v-if="project?.default_browser_url === currentUrl" class="save-menu-saved">saved</span>
                </wa-dropdown-item>
                <wa-dropdown-item
                    v-if="mainRepoProject"
                    value="main-repo"
                    :disabled="mainRepoProject.default_browser_url === currentUrl"
                >
                    <wa-icon slot="icon" name="folder-tree"></wa-icon>
                    {{ store.getProjectDisplayName(mainRepoProject.id) }} (main repository)
                    <span v-if="mainRepoProject.default_browser_url === currentUrl" class="save-menu-saved">saved</span>
                </wa-dropdown-item>
                <template v-if="memberWorkspaces.length">
                    <wa-divider></wa-divider>
                    <wa-dropdown-item
                        v-for="ws in memberWorkspaces"
                        :key="ws.id"
                        :value="`ws:${ws.id}`"
                        :disabled="ws.browserUrl === currentUrl"
                    >
                        <wa-icon slot="icon" name="layer-group" :style="ws.color ? { color: ws.color } : null"></wa-icon>
                        {{ ws.name }}
                        <span v-if="ws.browserUrl === currentUrl" class="save-menu-saved">saved</span>
                    </wa-dropdown-item>
                </template>
            </wa-dropdown>

            <wa-button appearance="plain" size="small" class="browser-btn" :disabled="!currentUrl" title="Open in a new browser tab" @click="openExternal">
                <wa-icon name="arrow-up-right-from-square"></wa-icon>
            </wa-button>

            <wa-icon :id="`browser-info-${instanceId}`" name="circle-info" class="browser-info"></wa-icon>
            <AppTooltip :for="`browser-info-${instanceId}`">
                Embedded pages are isolated: Back/Forward/Refresh only track URLs
                entered here — links followed inside the page are invisible to this
                toolbar, and Refresh reloads the last entered URL. Some sites refuse
                to be embedded (X-Frame-Options) and stay blank; logins may not
                persist inside a frame. Keyboard shortcuts pause while the page has
                focus — click TwiCC's chrome to get them back.
            </AppTooltip>
        </div>

        <wa-callout v-if="saveError" variant="danger" size="small" class="browser-banner">
            <wa-icon slot="icon" name="triangle-exclamation"></wa-icon>
            {{ saveError }}
        </wa-callout>

        <wa-callout v-if="probeResult" variant="warning" size="small" class="browser-banner">
            <wa-icon slot="icon" name="triangle-exclamation"></wa-icon>
            <template v-if="probeResult.reachable === false">
                The server did not respond ({{ probeResult.reason }}) — is it running?
            </template>
            <template v-else>
                This site refuses to be embedded ({{ probeResult.reason }}) — the frame
                below will likely stay blank. Use "Open in a new browser tab" instead.
            </template>
        </wa-callout>

        <wa-callout v-if="mixedContentBlocked" variant="warning" size="small" class="browser-banner">
            <wa-icon slot="icon" name="triangle-exclamation"></wa-icon>
            TwiCC is served over https, so the browser blocks embedding this http://
            URL (mixed content). Open it in a new tab instead, or serve it over https.
        </wa-callout>

        <div class="browser-body">
            <iframe
                v-if="everActivated && currentUrl && !mixedContentBlocked"
                :key="frameKey"
                :src="currentUrl"
                class="browser-frame"
                allow="clipboard-read; clipboard-write; fullscreen"
                title="Browser"
                @load="onFrameLoad"
            ></iframe>
            <div v-else-if="!currentUrl" class="browser-empty">
                <wa-icon name="globe" class="browser-empty-icon"></wa-icon>
                <p>Enter a URL above to preview your project — e.g. your dev server.</p>
                <p class="browser-empty-hint">
                    Use the <wa-icon name="bookmark"></wa-icon> menu to save it as the
                    default for this project or one of its workspaces.
                </p>
            </div>
        </div>
    </div>
</template>

<style scoped>
.browser-pane {
    display: flex;
    flex-direction: column;
    height: 100%;
    min-height: 0;
    overflow: hidden;
}

.browser-toolbar {
    display: flex;
    align-items: center;
    gap: var(--wa-space-2xs);
    padding: var(--wa-space-2xs) var(--wa-space-xs);
    border-bottom: 1px solid var(--wa-color-border-quiet);
    flex-shrink: 0;
}

.browser-btn {
    flex-shrink: 0;
}

.browser-address {
    flex: 1;
    min-width: 6rem;
}

.browser-info {
    flex-shrink: 0;
    color: var(--wa-color-text-quiet);
    margin-inline: var(--wa-space-2xs);
}

.browser-banner {
    margin: var(--wa-space-xs);
    flex-shrink: 0;
}

.save-menu-header::part(label) {
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-text-quiet);
}

.save-menu-saved {
    margin-left: var(--wa-space-xs);
    font-size: var(--wa-font-size-2xs);
    color: var(--wa-color-text-quiet);
}

.browser-body {
    flex: 1;
    min-height: 0;
    display: flex;
}

/* White canvas: most pages assume a light default background, and a
   transparent iframe over TwiCC's dark theme renders them unreadable. */
.browser-frame {
    flex: 1;
    width: 100%;
    height: 100%;
    border: none;
    background: #fff;
}

.browser-empty {
    flex: 1;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: var(--wa-space-s);
    color: var(--wa-color-text-quiet);
    text-align: center;
    padding: var(--wa-space-l);
}

.browser-empty-icon {
    font-size: 2.5rem;
    opacity: 0.5;
}

.browser-empty p {
    margin: 0;
}

.browser-empty-hint {
    font-size: var(--wa-font-size-s);
}
</style>
