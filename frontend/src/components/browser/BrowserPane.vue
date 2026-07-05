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
//
// The iframe itself is rendered through PersistentFrame (frames/), so it lives
// in the app-level FrameHost and is NOT reloaded by session switches (KeepAlive)
// or dock moves — only an explicit remount (frameKey bump on hard navigation /
// refresh in fallback mode) re-creates it.
import { computed, inject, onBeforeUnmount, onMounted, ref, useId, watch } from 'vue'
import { hostMessage, isCompanionMessage } from '../../browser-companion/protocol'
import { useDataStore } from '../../stores/data'
import { useSettingsStore } from '../../stores/settings'
import { useWorkspacesStore } from '../../stores/workspaces'
import { apiFetch } from '../../utils/api'
import { resolveProjectBrowserUrl } from '../../utils/browserDefaults'
import { looksLocalUrl, normalizeBrowserUrl } from '../../utils/browserUrl'
import { debounce } from '../../utils/debounce'
import PersistentFrame from '../frames/PersistentFrame.vue'
import ProjectBadge from '../project/ProjectBadge.vue'
import WorktreeBadge from '../project/WorktreeBadge.vue'
import TextSelectionComment from '../session/detail/TextSelectionComment.vue'
import AppTooltip from '../ui/AppTooltip.vue'

// Stable identity — an inline literal would churn the frame descriptor watch.
const BROWSER_FRAME_ATTRS = { allow: 'clipboard-read; clipboard-write; fullscreen', title: 'Browser' }

const props = defineProps({
    sessionId: { type: String, default: null },
    projectId: { type: String, default: null },
    // True while the Browser tab is the shown tab in its region — drives the
    // lazy first load (never fetch a dev server for a tab that was never opened).
    active: { type: Boolean, default: false },
    // Bumped by SessionView on explicit tab activation → focus the address bar.
    focusRequest: { type: Number, default: 0 },
    // True while this pane's frame is shown inside the docking overlay — raises
    // the pooled iframe above the overlay panel.
    frameElevated: { type: Boolean, default: false },
})

// 'interact': a real user interaction inside the embedded page (companion
// 'focus' message) — SessionView treats it like a click in the pane body and
// claims the route for the Browser tab.
const emit = defineEmits(['interact'])

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
// The PersistentFrame component; frameEl resolves to the live <iframe> element
// it manages (in the pool, or inline in fallback contexts). Companion messaging
// reads frameEl.value.contentWindow through it.
const persistentFrameRef = ref(null)
const frameEl = computed(() => persistentFrameRef.value?.frameEl ?? null)
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
        // A hello means a fresh document — any select-area overlay died with
        // the old one, so the host flag must not claim the mode is still on.
        selectAreaActive.value = false
        selectState.value = null
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
    } else if (data.type === 'select-state') {
        // Capabilities of the currently highlighted element (select-area
        // mode) — drives the select toolbar's navigation buttons. Ignored
        // when the mode is off (a late message from a just-disabled overlay).
        if (selectAreaActive.value) {
            selectState.value = {
                hasSelection: data.hasSelection === true,
                locked: data.locked === true,
                canParent: data.canParent === true,
                canFirstChild: data.canFirstChild === true,
                canPrevSibling: data.canPrevSibling === true,
                canNextSibling: data.canNextSibling === true,
            }
        }
    } else if (data.type === 'select-describe') {
        // The companion described the highlighted element (opening tag + text
        // + ancestor chain) — open the comment widget on it.
        if (selectAreaActive.value && typeof data.chain === 'string') {
            const lines = []
            if (typeof data.openingTag === 'string' && data.openingTag) lines.push(`Element: ${data.openingTag}`)
            if (typeof data.text === 'string' && data.text) lines.push(`Text: "${data.text}"`)
            lines.push(`Path: ${data.chain}`)
            openSelectComment(lines.join('\n'))
        }
    } else if (data.type === 'focus') {
        // Real user input inside the embedded page — the cross-origin
        // equivalent of DockRegion's click-to-focus. A hidden frame can't
        // receive input (visibility:hidden + pointer-events:none), but guard
        // on `active` anyway against stale messages mid-teardown.
        if (props.active) emit('interact')
    } else if (data.type === 'bye') {
        // Document going away (navigation / reload). Undetermined until the
        // next document says hello or the post-load grace period expires.
        companionStatus.value = 'waiting'
        selectAreaActive.value = false
        selectState.value = null
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

// ── Full-window: the WHOLE pane (toolbar + frame) expands in place via a
// position:fixed class — the same mechanism as FilePane's preview fullscreen.
// The toolbar stays on top and fully usable (Back/Forward, address, companion)
// while the embedded page fills the window. Never teleported: moving the iframe
// would reload it; the pooled frame just follows its placeholder's new rect and
// switches to the 'fullscreen' z-tier so it layers above the fixed pane.
const isFullscreen = ref(false)
const fullscreenButtonId = `browser-fullscreen-${instanceId}`
// Provided by ProjectView: drops .main-content's container-type and lifts it
// above the sidebar/divider so the fixed overlay covers the whole window.
// Absent outside a project view → graceful no-op (overlay clamps to the pane).
const expandPreviewHost = inject('expandPreviewHost', null)

function toggleFullscreen() {
    isFullscreen.value = !isFullscreen.value
}

// Escape exits full-window. Capture phase + stopPropagation so it unwraps here
// before any ancestor Escape handler reacts.
function onFullscreenKeydown(event) {
    if (event.key === 'Escape') {
        // An open select-comment widget has priority: this capture-phase
        // listener would otherwise exit fullscreen on the same keypress the
        // widget uses to close itself.
        if (selectCommentPosition.value) return
        event.stopPropagation()
        isFullscreen.value = false
    }
}

watch(isFullscreen, (on) => {
    expandPreviewHost?.(on)
    if (on) window.addEventListener('keydown', onFullscreenKeydown, true)
    else window.removeEventListener('keydown', onFullscreenKeydown, true)
})

onMounted(() => window.addEventListener('message', onWindowMessage))
onBeforeUnmount(() => {
    window.removeEventListener('message', onWindowMessage)
    window.removeEventListener('keydown', onFullscreenKeydown, true)
    if (isFullscreen.value) expandPreviewHost?.(false)
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

// ── Select-area mode: toggled from the companion menu; the actual picking
// (interaction-blocking overlay + outline on the hovered/tapped element)
// happens inside the embedded page, driven by the companion. The flag only
// mirrors what we asked for — it resets on hello/bye above, since the overlay
// lives and dies with the page's document. `selectState` is the companion's
// report on the highlighted element (null until one is highlighted).
const selectAreaActive = ref(false)
const selectState = ref(null)

function setSelectArea(enabled) {
    if (selectAreaActive.value === enabled) return
    selectAreaActive.value = enabled
    selectState.value = null
    // Drop any open comment widget — its v-if also gates on selectAreaActive,
    // but resetting avoids a stale one flashing back on the next mode entry.
    selectCommentPosition.value = null
    sendToCompanion(hostMessage('command', { action: 'select-mode', enabled }))
}

function onCompanionMenuSelect(event) {
    if (event.detail?.item?.value !== 'select-area') return
    setSelectArea(!selectAreaActive.value)
}

function selectNav(direction) {
    sendToCompanion(hostMessage('command', { action: 'select-nav', direction }))
}

function selectClear() {
    sendToCompanion(hostMessage('command', { action: 'select-clear' }))
}

function selectComment() {
    sendToCompanion(hostMessage('command', { action: 'select-describe' }))
}

// ── Select-area comment widget: reuses the text-selection comment window,
// quoting the companion's element description instead of a DOM selection.
// Initial position: horizontally centered, top edge right below the select
// toolbar — the user can drag it anywhere afterwards, as usual.
const selectToolbarRef = ref(null)
const selectCommentText = ref('')
const selectCommentPosition = ref(null)
// Rendered into the formatted message header ("Comment on selected text …:")
// so the agent knows which page the element lives on.
const selectCommentSourceLabel = computed(() => `from the browser page at ${currentUrl.value}`)

function openSelectComment(description) {
    const rect = selectToolbarRef.value?.getBoundingClientRect()
    if (!rect) return
    selectCommentText.value = description
    selectCommentPosition.value = { top: rect.bottom, left: rect.left + rect.width / 2, above: false }
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
    <div class="browser-pane" :class="{ 'browser-pane--fullscreen': isFullscreen }">
        <div class="browser-toolbar">
            <div class="browser-toolbar-left">
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
            </div>

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

            <div class="browser-toolbar-right">
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
                    <!-- Level markers mirror the badges used everywhere else: the
                         current project shows as a WorktreeBadge (parent · branch ·
                         folder) when it is a worktree, else a plain ProjectBadge;
                         its main repository (worktree case) is a ProjectBadge; each
                         member workspace keeps the layer-group badge. -->
                    <wa-dropdown-item value="project" :disabled="project?.default_browser_url === currentUrl">
                        <WorktreeBadge v-if="mainRepoProject" :project-id="props.projectId" />
                        <ProjectBadge v-else :project-id="props.projectId" />
                        <span v-if="project?.default_browser_url === currentUrl" class="save-menu-saved">saved</span>
                    </wa-dropdown-item>
                    <wa-dropdown-item
                        v-if="mainRepoProject"
                        value="main-repo"
                        :disabled="mainRepoProject.default_browser_url === currentUrl"
                    >
                        <ProjectBadge :project-id="mainRepoProject.id" />
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
                            <span class="save-menu-ws">
                                <wa-icon name="layer-group" :style="ws.color ? { color: ws.color } : null"></wa-icon>
                                {{ ws.name }}
                            </span>
                            <span v-if="ws.browserUrl === currentUrl" class="save-menu-saved">saved</span>
                        </wa-dropdown-item>
                    </template>
                </wa-dropdown>

                <wa-button appearance="plain" size="small" class="browser-btn" :disabled="!currentUrl" title="Open in a new browser tab" @click="openExternal">
                    <wa-icon name="arrow-up-right-from-square"></wa-icon>
                </wa-button>

                <wa-button
                    :id="fullscreenButtonId"
                    appearance="plain"
                    size="small"
                    class="browser-btn"
                    @click="toggleFullscreen"
                >
                    <wa-icon :name="isFullscreen ? 'compress' : 'expand'"></wa-icon>
                </wa-button>
                <AppTooltip :for="fullscreenButtonId">
                    {{ isFullscreen ? 'Exit full screen' : 'Full screen' }}
                </AppTooltip>

                <!-- Companion control. Until connected it is a plain status
                     button (a tap reveals the how-to-add hint); once connected
                     it becomes a dropdown (with-caret) exposing companion
                     actions. WA custom events are stopped from bubbling so a
                     nested wa-show/wa-hide never reaches a same-named ancestor. -->
                <wa-dropdown
                    v-if="companionStatus === 'present'"
                    placement="bottom-end"
                    @click.stop
                    @wa-select.stop="onCompanionMenuSelect"
                    @wa-show.stop
                    @wa-hide.stop
                    @wa-after-show.stop
                    @wa-after-hide.stop
                >
                    <wa-button
                        :id="`browser-companion-${instanceId}`"
                        slot="trigger"
                        appearance="plain"
                        size="small"
                        class="browser-btn companion-status present"
                        with-caret
                    >
                        <wa-icon name="plug"></wa-icon>
                    </wa-button>
                    <wa-dropdown-item value="select-area">
                        <wa-icon slot="icon" name="arrow-pointer"></wa-icon>
                        {{ selectAreaActive ? 'Stop selecting' : 'Select area' }}
                    </wa-dropdown-item>
                </wa-dropdown>
                <wa-button
                    v-else
                    :id="`browser-companion-${instanceId}`"
                    appearance="plain"
                    size="small"
                    class="browser-btn companion-status"
                    :class="companionStatus"
                    @click="onCompanionIconClick"
                >
                    <wa-icon name="plug"></wa-icon>
                </wa-button>
                <!-- force + click trigger: the tooltip is hover-only and hidden
                     on touch by default, but here a tap must reveal the companion
                     status/help on mobile too (no hover there). In the connected
                     dropdown case, click is dropped from the trigger so the tap
                     opens the menu instead of racing the tooltip. -->
                <AppTooltip
                    :for="`browser-companion-${instanceId}`"
                    force
                    :trigger="companionStatus === 'present' ? 'hover focus' : 'hover focus click'"
                >{{ companionTooltip }}</AppTooltip>
            </div>
        </div>

        <!-- Select-area toolbar: shown while the picking mode is on. The
             navigation buttons walk the page's DOM from the highlighted
             element — every step is executed by the companion in the page. -->
        <div v-if="selectAreaActive" ref="selectToolbarRef" class="select-toolbar">
            <span class="select-toolbar-label">Select area</span>
            <wa-button :id="`browser-select-clear-${instanceId}`" appearance="plain" size="small" class="browser-btn" :disabled="!selectState?.locked" @click="selectClear">
                <wa-icon name="ban"></wa-icon>
            </wa-button>
            <AppTooltip :for="`browser-select-clear-${instanceId}`">Clear the selection</AppTooltip>
            <wa-button :id="`browser-select-parent-${instanceId}`" appearance="plain" size="small" class="browser-btn" :disabled="!selectState?.canParent" @click="selectNav('parent')">
                <wa-icon name="arrow-up"></wa-icon>
            </wa-button>
            <AppTooltip :for="`browser-select-parent-${instanceId}`">Select the parent</AppTooltip>
            <wa-button :id="`browser-select-child-${instanceId}`" appearance="plain" size="small" class="browser-btn" :disabled="!selectState?.canFirstChild" @click="selectNav('first-child')">
                <wa-icon name="arrow-down"></wa-icon>
            </wa-button>
            <AppTooltip :for="`browser-select-child-${instanceId}`">Select the first child</AppTooltip>
            <wa-button :id="`browser-select-prev-${instanceId}`" appearance="plain" size="small" class="browser-btn" :disabled="!selectState?.canPrevSibling" @click="selectNav('prev-sibling')">
                <wa-icon name="arrow-left"></wa-icon>
            </wa-button>
            <AppTooltip :for="`browser-select-prev-${instanceId}`">Select the previous sibling</AppTooltip>
            <wa-button :id="`browser-select-next-${instanceId}`" appearance="plain" size="small" class="browser-btn" :disabled="!selectState?.canNextSibling" @click="selectNav('next-sibling')">
                <wa-icon name="arrow-right"></wa-icon>
            </wa-button>
            <AppTooltip :for="`browser-select-next-${instanceId}`">Select the next sibling</AppTooltip>
            <wa-button :id="`browser-select-comment-${instanceId}`" appearance="plain" size="small" class="browser-btn" :disabled="!selectState?.hasSelection" @click="selectComment">
                <wa-icon name="comment" variant="regular"></wa-icon>
            </wa-button>
            <AppTooltip :for="`browser-select-comment-${instanceId}`">Comment on the selection</AppTooltip>
            <wa-button :id="`browser-select-close-${instanceId}`" appearance="plain" size="small" class="browser-btn select-toolbar-close" @click="setSelectArea(false)">
                <wa-icon name="xmark"></wa-icon>
            </wa-button>
            <AppTooltip :for="`browser-select-close-${instanceId}`">Exit select mode</AppTooltip>
        </div>

        <!-- Select-area comment widget (teleported to body like every other
             consumer — avoids overflow clipping and sits above fullscreen).
             No source selection to clear: the "quote" is the companion's
             element description. Hidden with the mode (v-if on both). -->
        <Teleport to="body">
            <TextSelectionComment
                v-if="selectAreaActive && selectCommentPosition"
                :selected-text="selectCommentText"
                :position="selectCommentPosition"
                :source-label="selectCommentSourceLabel"
                subject="selected area"
                auto-expand
                :clear-source-selection="() => {}"
                @close="selectCommentPosition = null"
            />
        </Teleport>

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
            <PersistentFrame
                v-if="everActivated && currentUrl && !mixedContentBlocked"
                ref="persistentFrameRef"
                :frame-id="`browser:${instanceId}`"
                :src="frameSrc"
                :remount-key="frameKey"
                :attrs="BROWSER_FRAME_ATTRS"
                :elevated="props.frameElevated"
                :fullscreen="isFullscreen"
                class="browser-frame"
                @load="onFrameLoad"
            />
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

/* Full-window: the whole pane expands IN PLACE (position:fixed) rather than
   teleporting — moving the iframe would reload it. The toolbar stays on top; the
   pooled frame follows its placeholder in the body and switches to the
   fullscreen z-tier (layered above this fixed pane, geometrically only over the
   body). ProjectView drops .main-content's containment and lifts it above the
   sidebar/divider while expanded (via the injected setter), so this fixed
   overlay covers the whole window. */
.browser-pane--fullscreen {
    position: fixed;
    inset: 0;
    z-index: 1000;
    background: var(--wa-color-surface-default);
}

.browser-toolbar {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    padding: var(--wa-space-2xs);
    border-bottom: 1px solid var(--wa-color-border-quiet);
    flex-shrink: 0;
}

/* Nav buttons before the address, controls after it — grouped so the toolbar
   has three flex children (left · address · right) that wrap as units. */
.browser-toolbar-left,
.browser-toolbar-right {
    display: flex;
    align-items: center;
    flex-shrink: 0;
}

/* Keep the controls flush right, even once the address has grown or the row
   has wrapped them onto their own line. */
.browser-toolbar-right {
    margin-left: auto;
}

.browser-btn {
    flex-shrink: 0;
}

.browser-address {
    flex: 1;
    min-width: min(10rem, 90%);
}

/* Companion status is a normal toolbar button now; the plug's colour + dim
   reflect the connection state (icon targeted directly — currentColor glyph). */
.companion-status {
    opacity: 0.55;
}

.companion-status wa-icon {
    color: var(--wa-color-text-quiet);
}

.companion-status.present {
    opacity: 1;
}

.companion-status.present wa-icon {
    color: var(--wa-color-success-fill-loud);
}

.select-toolbar {
    display: flex;
    align-items: center;
    gap: var(--wa-space-2xs);
    padding: var(--wa-space-2xs);
    border-bottom: 1px solid var(--wa-color-border-quiet);
    flex-shrink: 0;
}

.select-toolbar-label {
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-text-quiet);
    padding-inline: var(--wa-space-2xs);
}

/* Exit button pinned to the far right, away from the selection controls. */
.select-toolbar-close {
    margin-left: auto;
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

/* Workspace level marker: layer-group badge + name inline, so it lines up with
   the color dot the project/worktree badges render at the same start position. */
.save-menu-ws {
    display: inline-flex;
    align-items: center;
    gap: var(--wa-space-xs);
    min-width: 0;
}

.browser-body {
    flex: 1;
    min-height: 0;
    display: flex;
}

/* Placeholder sizing only — the pooled iframe (in FrameHost) carries the
   border/background; PersistentFrame positions it over this box. */
.browser-frame {
    flex: 1;
    width: 100%;
    height: 100%;
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
