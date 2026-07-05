<script setup>
// Browser pane: embeds a user-chosen URL (typically the project's dev server)
// in a plain iframe. Unlike the Artifacts preview there is NO sandbox, NO CSP
// lockdown and NO network broker — deliberate: the page runs exactly as in a
// normal browser tab (direct network, service workers, HMR websockets), it
// just cannot reach into TwiCC (cross-origin isolation applies both ways).
//
// Cross-origin iframes expose neither their current location nor their
// history. Two modes:
// - Companion mode: the embedded page includes the TwiCC companion script
//   (served at /_twicc/browser-companion.js) and reports real navigation over
//   postMessage; Back/Forward/Refresh drive the page's own history.
// - Fallback: the toolbar keeps its OWN history stack of URLs navigated via
//   the address bar / Back / Forward / Home only; links followed inside the
//   page are invisible and Refresh re-creates the iframe on the last recorded
//   URL. Deliberate, documented limits — see the info tooltip.
import { computed, onBeforeUnmount, onMounted, ref, useId, watch } from 'vue'
import { hostMessage, isCompanionMessage } from '../../browser-companion/protocol'
import { useDataStore } from '../../stores/data'
import { useSettingsStore } from '../../stores/settings'
import { useWorkspacesStore } from '../../stores/workspaces'
import { apiFetch } from '../../utils/api'
import { resolveProjectBrowserUrl } from '../../utils/browserDefaults'
import { looksLocalUrl, normalizeBrowserUrl } from '../../utils/browserUrl'
import { debounce } from '../../utils/debounce'
import AppTooltip from '../ui/AppTooltip.vue'

const props = defineProps({
    sessionId: { type: String, default: null },
    projectId: { type: String, default: null },
    // True while the Browser tab is the shown tab in its region — drives the
    // lazy first load (never fetch a dev server for a tab that was never opened).
    active: { type: Boolean, default: false },
    // Bumped by SessionView on explicit tab activation → focus the address bar.
    focusRequest: { type: Number, default: 0 },
})

const store = useDataStore()
const settingsStore = useSettingsStore()
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
// (fallback mode; named to avoid shadowing window.history in this component).
// `currentUrl` is the pane's display/persist state; the iframe binds the
// SEPARATE `frameSrc` — binding :src to the live currentUrl would re-navigate
// the frame every time the companion reports an in-page URL change.
const currentUrl = ref('')       // URL currently shown ('' = blank state)
const inputUrl = ref('')         // address bar edit buffer
const frameSrc = ref('')         // what the iframe element was last pointed at
const urlHistory = ref([])
const historyIndex = ref(-1)
const frameKey = ref(0)          // bump = recreate the iframe (fallback navigate / refresh)
const loading = ref(false)
const everActivated = ref(false)
const frameEl = ref(null)
const addressFocused = ref(false)

// ── Companion channel. The embedded page may include the TwiCC companion
// script; presence is a tiny state machine driven by its hello/bye messages
// and the iframe load event:
//   absent  — determined: no companion in the current document
//   waiting — undetermined: navigation in flight / grace period running
//   present — handshake done; navigation flows both ways
const companionStatus = ref('absent')
const companionCanGoBack = ref(null)   // null = unknown (no Navigation API)
const companionCanGoForward = ref(null)
let companionOrigin = null
let helloGraceTimer = null
// True once a companion connected: the fallback stack froze during that
// session, so it must be reset when the pane later degrades to fallback mode.
let hadCompanion = false
const HELLO_GRACE_MS = 3000

function sendToCompanion(message) {
    if (!frameEl.value?.contentWindow || !companionOrigin) return
    frameEl.value.contentWindow.postMessage(message, companionOrigin)
}

function clearHelloGraceTimer() {
    if (helloGraceTimer) {
        clearTimeout(helloGraceTimer)
        helloGraceTimer = null
    }
}

function onWindowMessage(event) {
    // Only our own frame's current document, nothing else (other panes, other
    // windows, devtools). contentWindow is re-read live: a frameKey remount
    // invalidates messages from the torn-down document by identity.
    if (!frameEl.value || event.source !== frameEl.value.contentWindow) return
    const data = event.data
    if (!isCompanionMessage(data)) return
    if (data.type === 'hello') {
        companionOrigin = event.origin
        companionStatus.value = 'present'
        hadCompanion = true
        companionCanGoBack.value = null
        companionCanGoForward.value = null
        clearHelloGraceTimer()
        // A connected companion is definitive proof the page is reachable and
        // embeddable — drop any banner a slower/raced probe put up.
        probeResult.value = null
        event.source.postMessage(hostMessage('ack'), event.origin)
        return
    }
    if (event.origin !== companionOrigin) return
    if (data.type === 'state') {
        companionCanGoBack.value = typeof data.canGoBack === 'boolean' ? data.canGoBack : null
        companionCanGoForward.value = typeof data.canGoForward === 'boolean' ? data.canGoForward : null
        // The embedded page is untrusted: only adopt plain http(s) URLs. A
        // hostile page could report a javascript:/data: string that would
        // otherwise reach the UNsandboxed iframe src on a later fallback
        // navigation — i.e. script execution in TwiCC's own origin. The
        // scheme is lowercased so case-sensitive downstream checks (backend
        // validate_browser_url, mixedContentBlocked) can't be sidestepped.
        const url =
            typeof data.url === 'string' && /^https?:\/\//i.test(data.url)
                ? data.url.replace(/^https?/i, (scheme) => scheme.toLowerCase())
                : null
        if (url && url !== currentUrl.value) {
            currentUrl.value = url
            // Never clobber an in-progress address-bar edit.
            if (!addressFocused.value) inputUrl.value = url
            probeResult.value = null // a page just loaded — any diagnosis is stale
            persistUrlDebounced()
        }
    } else if (data.type === 'bye') {
        // Document going away (navigation / reload). Undetermined until the
        // next document says hello or the post-load grace period expires.
        companionStatus.value = 'waiting'
        // The frozen pre-companion stack becomes reachable the moment we
        // leave 'present' — and this waiting window is exactly when users
        // click Back ("page is taking too long"). Reset it NOW; clearing
        // hadCompanion makes the absent-transition reset a pure lost-bye
        // safety net (it must not re-wipe valid toolbar entries the user
        // accumulates during the waiting window).
        hadCompanion = false
        urlHistory.value = currentUrl.value ? [currentUrl.value] : []
        historyIndex.value = urlHistory.value.length - 1
    }
}

onMounted(() => window.addEventListener('message', onWindowMessage))
onBeforeUnmount(() => {
    window.removeEventListener('message', onWindowMessage)
    clearHelloGraceTimer()
    persistUrlDebounced.cancel()
})

const canGoBack = computed(() =>
    companionStatus.value === 'present'
        ? companionCanGoBack.value !== false // null (unknown) keeps the button usable
        : historyIndex.value > 0
)
const canGoForward = computed(() =>
    companionStatus.value === 'present'
        ? companionCanGoForward.value !== false
        : historyIndex.value < urlHistory.value.length - 1
)

// ── Per-session persistence: restore the last URL across page reloads.
// Read once at first activation (it wins over the defaults); written back
// debounced on each navigation — toolbar-initiated or companion-reported.
// Drafts have no backend row — their URL stays transient. The pane state
// lives in the refs above, never on the store's session object, so the
// session_updated echo can't clobber it.
const BROWSER_URL_PERSIST_DEBOUNCE_MS = 1000
let lastPersistedUrl = null
const persistUrlDebounced = debounce(async () => {
    const sessionRow = store.getSession(props.sessionId)
    if (!sessionRow || sessionRow.draft) return
    const url = currentUrl.value
    if (url === lastPersistedUrl) return
    try {
        const response = await apiFetch(`/api/projects/${props.projectId}/sessions/${props.sessionId}/`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ browser_url: url }),
        })
        if (response.ok) lastPersistedUrl = url
    } catch {
        // Transient UI state — losing a write is acceptable.
    }
}, BROWSER_URL_PERSIST_DEBOUNCE_MS)

// An https TwiCC page cannot embed an http iframe (the browser blocks it
// silently as mixed content) — explain instead of showing a dead frame.
const mixedContentBlocked = computed(
    () => window.location.protocol === 'https:' && currentUrl.value.startsWith('http://')
)

// Fallback-mode hard navigation: point the (re-created) iframe at a URL.
function showFrame(url) {
    currentUrl.value = url
    inputUrl.value = url
    frameSrc.value = url
    frameKey.value++
    loading.value = true
    companionStatus.value = 'waiting'
    companionCanGoBack.value = null
    companionCanGoForward.value = null
    clearHelloGraceTimer()
    probeResult.value = null // clear a stale diagnosis right away
    probeCurrentUrl()
    persistUrlDebounced()
}

function navigate(rawInput) {
    const url = normalizeBrowserUrl(rawInput)
    if (!url) return
    if (companionStatus.value === 'present') {
        // Navigate in place — preserves the frame's real session history.
        sendToCompanion(hostMessage('command', { action: 'navigate', url }))
        currentUrl.value = url
        inputUrl.value = url
        loading.value = true
        probeResult.value = null
        persistUrlDebounced()
        return
    }
    // Fallback: truncate forward entries, then push (skip contiguous repeats).
    const stack = urlHistory.value.slice(0, historyIndex.value + 1)
    if (stack[stack.length - 1] !== url) stack.push(url)
    urlHistory.value = stack
    historyIndex.value = stack.length - 1
    showFrame(url)
}

function goBack() {
    if (companionStatus.value === 'present') {
        sendToCompanion(hostMessage('command', { action: 'back' }))
        return
    }
    if (!canGoBack.value) return
    historyIndex.value--
    showFrame(urlHistory.value[historyIndex.value])
}

function goForward() {
    if (companionStatus.value === 'present') {
        sendToCompanion(hostMessage('command', { action: 'forward' }))
        return
    }
    if (!canGoForward.value) return
    historyIndex.value++
    showFrame(urlHistory.value[historyIndex.value])
}

function refresh() {
    if (!currentUrl.value) return
    if (companionStatus.value === 'present') {
        // In-place reload — keeps in-page navigation and session history.
        sendToCompanion(hostMessage('command', { action: 'reload' }))
        loading.value = true
        return
    }
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
    // Presence: a companion's hello normally arrives BEFORE load (deferred
    // scripts run first). If it hasn't by now, give slow injectors a short
    // grace, then declare absence. The probe re-runs on absence so a dead
    // server is diagnosed even on companion-mode navigations (which skip
    // showFrame and its probe call).
    // Staying 'present' here trusts that a cross-document navigation reset us
    // to 'waiting' via the old document's bye first (reliable for normal
    // in-frame navigations). We can't observe load-start on a cross-origin
    // frame, so a genuinely lost bye is the one case this can't self-heal —
    // an accepted limit of the cross-origin model (recovered by a TwiCC reload).
    if (companionStatus.value === 'present') return
    clearHelloGraceTimer()
    helloGraceTimer = setTimeout(() => {
        helloGraceTimer = null
        companionStatus.value = 'absent'
        companionCanGoBack.value = null
        companionCanGoForward.value = null
        if (hadCompanion) {
            // The fallback stack froze during the companion session — its
            // entries predate it, and Back would jump to a long-gone page.
            // Restart from the current URL: an honest degraded state.
            hadCompanion = false
            urlHistory.value = currentUrl.value ? [currentUrl.value] : []
            historyIndex.value = urlHistory.value.length - 1
        }
        probeCurrentUrl()
    }, HELLO_GRACE_MS)
}

// ── Advisory probe: the two silent-blank-frame cases (server down, framing
// refused) are invisible client-side; ask the backend. Failures are ignored —
// the endpoint is advisory.
// probeResult is CLEARED at navigation points (showFrame, companion navigate,
// companion-reported page load) and only ASSIGNED here — never reset at probe
// start. Two probes can race on one navigation (showFrame's immediate one and
// the post-load absence one); both converge on the same value for the same
// URL, so a late response can't flicker an already-displayed banner away.
const probeResult = ref(null)

async function probeCurrentUrl() {
    const url = currentUrl.value
    if (!url) return
    try {
        const response = await apiFetch(`/api/browser-frame-check/?url=${encodeURIComponent(url)}`)
        if (!response.ok) return
        const data = await response.json()
        if (currentUrl.value !== url) return // user navigated meanwhile
        probeResult.value = data.reachable === false || data.embeddable === false ? data : null
    } catch {
        // advisory only
    }
}

// ── Lazy init: on first activation, auto-load the session's persisted URL
// (survives page reloads) or, failing that, the resolved default.
watch(
    () => props.active,
    (active) => {
        if (!active || everActivated.value) return
        everActivated.value = true
        const saved = store.getSession(props.sessionId)?.browser_url || null
        lastPersistedUrl = saved
        const initial = saved || defaultUrl.value
        if (initial) navigate(initial)
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

// ── Companion hint: snippet banner + status icon --------------------------
const snippetDismissed = ref(false)
const snippetCopied = ref(false)
// Prefer the configured External URL (publicBaseUrl) so the snippet stays
// reachable from wherever the dev page runs — a `localhost` src only resolves
// on this machine. Falls back to the current origin when no External URL is set.
const companionSnippet = computed(() => {
    const origin = settingsStore.getPublicBaseUrl || window.location.origin
    return `<script src="${origin}/_twicc/browser-companion.js" defer><\/script>`
})
// Only nag where the snippet is actionable: a loaded local-ish page, no
// higher-priority diagnostic (probe banner) on screen.
const showCompanionHint = computed(
    () =>
        companionStatus.value === 'absent' &&
        !!currentUrl.value &&
        looksLocalUrl(currentUrl.value) &&
        !snippetDismissed.value &&
        !probeResult.value &&
        !mixedContentBlocked.value
)

async function copySnippet() {
    try {
        await navigator.clipboard.writeText(companionSnippet.value)
        snippetCopied.value = true
        setTimeout(() => {
            snippetCopied.value = false
        }, 1500)
    } catch {
        // Clipboard unavailable (permissions) — the snippet is selectable text.
    }
}

const companionTooltip = computed(() => {
    if (companionStatus.value === 'present') {
        return 'Companion connected — the toolbar follows the page’s real navigation and history.'
    }
    if (companionStatus.value === 'waiting') return 'Checking for the TwiCC companion script…'
    return 'No companion script on this page — history is toolbar-only. Click to see how to add it.'
})

function onCompanionIconClick() {
    if (companionStatus.value === 'absent') snippetDismissed.value = false
}

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
            <wa-button appearance="plain" size="small" class="browser-btn" :disabled="!currentUrl" title="Refresh" @click="refresh">
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
                @focus="addressFocused = true"
                @blur="addressFocused = false"
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

            <wa-icon
                :id="`browser-companion-${instanceId}`"
                name="plug"
                class="companion-status"
                :class="companionStatus"
                @click="onCompanionIconClick"
            ></wa-icon>
            <AppTooltip :for="`browser-companion-${instanceId}`">{{ companionTooltip }}</AppTooltip>

            <wa-icon :id="`browser-info-${instanceId}`" name="circle-info" class="browser-info"></wa-icon>
            <AppTooltip :for="`browser-info-${instanceId}`">
                Pages that include the TwiCC companion script report their real
                navigation here — Back/Forward/Refresh drive the page's own
                history. Without it, the toolbar only tracks URLs entered here
                and links followed inside the page are invisible to it. Some
                sites refuse to be embedded (X-Frame-Options) and stay blank;
                logins may not persist inside a frame. Keyboard shortcuts pause
                while the page has focus — click TwiCC's chrome to get them back.
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

        <wa-callout v-if="showCompanionHint" variant="neutral" size="small" class="browser-banner">
            <wa-icon slot="icon" name="plug"></wa-icon>
            <div class="companion-hint">
                <span>
                    This page doesn't include the TwiCC companion script, so
                    Back/Forward only track URLs entered in the toolbar. Add it
                    to your dev page for real history tracking:
                </span>
                <code class="companion-snippet">{{ companionSnippet }}</code>
                <div class="companion-hint-actions">
                    <wa-button size="small" appearance="outlined" @click="copySnippet">
                        {{ snippetCopied ? 'Copied!' : 'Copy snippet' }}
                    </wa-button>
                    <wa-button size="small" appearance="plain" @click="snippetDismissed = true">Dismiss</wa-button>
                </div>
            </div>
        </wa-callout>

        <div class="browser-body">
            <iframe
                v-if="everActivated && currentUrl && !mixedContentBlocked"
                ref="frameEl"
                :key="frameKey"
                :src="frameSrc"
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

.companion-status {
    flex-shrink: 0;
    color: var(--wa-color-text-quiet);
    opacity: 0.55;
    cursor: pointer;
}

.companion-status.present {
    color: var(--wa-color-success-fill-loud);
    opacity: 1;
    cursor: default;
}

.browser-banner {
    margin: var(--wa-space-xs);
    flex-shrink: 0;
}

.companion-hint {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-xs);
    min-width: 0;
}

.companion-snippet {
    font-size: var(--wa-font-size-xs);
    background: var(--wa-color-surface-lowered);
    padding: var(--wa-space-2xs) var(--wa-space-xs);
    border-radius: var(--wa-border-radius-s);
    user-select: all;
    overflow-wrap: anywhere;
}

.companion-hint-actions {
    display: flex;
    gap: var(--wa-space-xs);
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
