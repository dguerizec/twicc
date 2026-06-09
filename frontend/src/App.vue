<script setup>
import { ref, onMounted, onBeforeUnmount, watch, computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Notivue, Notification, lightTheme, slateTheme } from 'notivue'
import { useWebSocket, versionMismatchDetected } from './composables/useWebSocket'
import { useDataStore } from './stores/data'
import { useSettingsStore } from './stores/settings'
import { useAuthStore } from './stores/auth'
import { COLOR_SCHEME, PROCESS_STATE } from './constants'
import { useFavicon } from './composables/useFavicon'
import { useTipScheduler } from './composables/useTipScheduler'
import { toast } from './composables/useToast'
import ProviderAuthToastContent from './components/app/ProviderAuthToastContent.vue'
import { getRegisteredProviders, getProviderHelpers } from './providers'
import ConnectionIndicator from './components/app/ConnectionIndicator.vue'
import CustomNotification from './components/app/CustomNotification.vue'
import CommandPalette from './components/app/CommandPalette.vue'
import SearchOverlay from './components/app/SearchOverlay.vue'
import StopProcessConfirmDialog from './components/app/StopProcessConfirmDialog.vue'
import ProviderActivationDialog from './components/app/ProviderActivationDialog.vue'
import GlobalMediaPreview from './components/media/GlobalMediaPreview.vue'
import ProjectTrustDialog from './components/project/ProjectTrustDialog.vue'
import { registerTrustDialog } from './composables/useTrustGate'
import { initStaticCommands } from './commands/staticCommands'
import {
    pendingConfirmation,
    confirmPendingStop,
    cancelPendingStop,
    stopSessionProcess,
} from './composables/useStopSessionProcess'
import { canStealFocus, hasBlockingOverlay } from './utils/focusGuard'
import { focusChatPrimary } from './utils/focusChat'
import { toggleSearchInActiveCodeMirror } from './composables/useCodeMirror'

const route = useRoute()
const router = useRouter()
initStaticCommands(router)
const authStore = useAuthStore()

// "App is ready" — the user is fully through the login layer and visibly
// off the /login route. Use this for anything that should NOT activate
// during the brief window between a successful authStore.login() and the
// full page reload that follows (during which authStore.authenticated is
// already true but the user is still on /login).
const isAppReady = computed(() => !authStore.needsLogin && route.name !== 'login')
const isConnecting = computed(() => authStore.isConnecting)

// Initialize WebSocket connection for real-time updates.
// Connection is deferred until authenticated (see useWebSocket).
const { wsStatus, openWs, closeWs } = useWebSocket()

// Dynamic favicon: overlays a status badge based on global process state
useFavicon()

// Start the tip scheduler: first tip after FIRST_TIP_DELAY_MS, then
// polling every SCHEDULER_POLL_MS once the per-dismiss cooldown expires.
// Skipped entirely when the app isn't ready (e.g. on /login). LoginView
// triggers a full page reload after login, so this setup re-runs with
// isAppReady true and the scheduler kicks in then.
if (isAppReady.value) {
    useTipScheduler()
}

// Load initial data and connect WebSocket when authenticated
const dataStore = useDataStore()

// Load app data and open the WS only once the app is actually ready
// (auth OK AND not on the /login page). The combined `isAppReady`
// avoids the brief flash where authStore.authenticated has already
// flipped to true but LoginView's full page reload hasn't fired yet.
watch(isAppReady, async (ready) => {
    if (ready) {
        await Promise.all([
            dataStore.loadHomeData(),
            // Preload "sticky" sessions (pinned, unread, running process)
            // from every project so the sidebar can render them cross-filter
            // without waiting for their project to be loaded on demand.
            dataStore.loadStickySessions(),
        ])
        openWs()
    } else {
        closeWs()
    }
}, { immediate: true })

// Sync display mode to body data attribute
const settingsStore = useSettingsStore()
const displayMode = computed(() => settingsStore.getDisplayMode)

// Set initial value and watch for changes
document.body.dataset.displayMode = displayMode.value
watch(displayMode, (newMode) => {
    document.body.dataset.displayMode = newMode
})

// Auto-reload when backend version changes
watch(versionMismatchDetected, (mismatch) => {
    if (mismatch) {
        setTimeout(() => window.location.reload(), 3000)
    }
})

// Persistent toast(s) when a provider's CLI is not authenticated.
// One toast per registered provider whose helpers expose ``getAuthState``;
// providers without an auth gate (default base implementation) are skipped
// entirely. The toast is shown only when the provider is BOTH enabled
// (settingsStore.enabledProviders) AND known to be unauthenticated, so a
// disabled provider never surfaces a banner — and an active banner is
// cleared as soon as the user disables its provider.
const _providerAuthToastItems = new Map()
for (const provider of getRegisteredProviders()) {
    const helpers = getProviderHelpers(provider)
    const authStateGetter = helpers.getAuthState()
    if (!authStateGetter) continue
    const shouldShow = computed(
        () => settingsStore.enabledProviders.includes(provider) && authStateGetter() === false,
    )
    watch(shouldShow, (show) => {
        const existing = _providerAuthToastItems.get(provider)
        if (show && !existing) {
            const item = toast.custom(ProviderAuthToastContent, {
                type: 'warning',
                title: `${helpers.constructor.label} CLI not authenticated`,
                duration: Infinity,
                props: {
                    provider,
                    loginCommand: helpers.getAuthLoginCommand(),
                },
            })
            _providerAuthToastItems.set(provider, item)
        } else if (!show && existing) {
            existing.clear?.()
            _providerAuthToastItems.delete(provider)
        }
    })
}

// ─── Command Palette (Ctrl+K / Cmd+K) & Search (Ctrl+Shift+F / Ctrl+F) ──
const commandPaletteRef = ref(null)
const searchOverlayRef = ref(null)

// Route names where Ctrl+F opens in-session search (main chat tab only)
const SESSION_CHAT_ROUTES = new Set(['session', 'projects-session'])

// Triple-Escape shortcut (emergency stop of the current session's process)
const TRIPLE_ESCAPE_WINDOW_MS = 333  // max gap between two consecutive Escape presses
const TRIPLE_ESCAPE_COOLDOWN_MS = 1000  // after a trigger, ignore Escape for this long
let escapeTimestamps = []  // rolling window of recent Escape presses
let lastTripleEscapeAt = 0

// All session route names (for tab keyboard shortcuts: Alt+Shift+{1-5, ←, →, ↑})
const SESSION_ROUTES = new Set([
    'session', 'session-subagent', 'session-files', 'session-git', 'session-terminal', 'session-orchestration',
    'projects-session', 'projects-session-subagent', 'projects-session-files', 'projects-session-git', 'projects-session-terminal', 'projects-session-orchestration',
])

// Project detail route names (for tab keyboard shortcuts: Alt+Shift+{1-4, ←, →, ↑})
const PROJECT_DETAIL_ROUTES = new Set([
    'project', 'project-files', 'project-git', 'project-terminal',
    'projects-all', 'projects-files', 'projects-git', 'projects-terminal',
])

// Terminal route names (for terminal tab shortcuts: Alt+Ctrl+Shift+{1-9, ←, →, ↑})
const TERMINAL_ROUTES = new Set([
    'session-terminal', 'projects-session-terminal',
    'project-terminal', 'projects-terminal',
])

function handleGlobalKeydown(e) {
    const modKey = settingsStore.isMac ? e.metaKey : e.ctrlKey
    if (modKey && e.key === 'k') {
        e.preventDefault()
        e.stopPropagation()
        commandPaletteRef.value?.open()
    }
    if ((e.ctrlKey || e.metaKey) && e.shiftKey && e.key === 'F') {
        e.preventDefault()
        e.stopPropagation()
        searchOverlayRef.value?.open()
    }
    // Ctrl+F (without Shift):
    // 1. If focus is inside a CodeMirror editor (Files, Git, embedded chat
    //    viewers…), own the shortcut: first press opens the editor's search
    //    panel (prefilled with the selection), second press closes it and
    //    falls through to the browser's native Find bar.
    // 2. Otherwise, on a session's chat tab, toggle the in-session search bar
    //    with the same first-open / second-close-and-fall-through pattern.
    if ((e.ctrlKey || e.metaKey) && !e.shiftKey && e.key === 'f') {
        const cmAction = toggleSearchInActiveCodeMirror()
        if (cmAction) {
            // stopPropagation prevents CodeMirror's own searchKeymap from
            // re-toggling the panel after us (we run in capture phase).
            e.stopPropagation()
            if (cmAction === 'opened') {
                e.preventDefault()  // block browser Find — CM panel just opened
            }
            // 'closed' → leave default so browser Find opens
            return
        }
        if (SESSION_CHAT_ROUTES.has(route.name)) {
            const detail = { handled: false }
            window.dispatchEvent(new CustomEvent('twicc:toggle-session-search', { detail }))
            if (detail.handled) {
                e.preventDefault()
                e.stopPropagation()
            }
        }
    }
    // Alt+Ctrl+Shift+{1-9, ←, →, ↑, ↓}: terminal tab navigation within the terminal panel.
    // Dispatches a custom event handled by the active TerminalPanel instance.
    if (e.altKey && e.shiftKey && e.ctrlKey && !e.metaKey && TERMINAL_ROUTES.has(route.name)) {
        let tabAction = null
        const digitMatch = e.code.match(/^(?:Digit|Numpad)([1-9])$/)
        if (digitMatch) {
            tabAction = { type: 'direct', index: parseInt(digitMatch[1]) }
        } else if (e.key === 'ArrowLeft') {
            tabAction = { type: 'prev' }
        } else if (e.key === 'ArrowRight') {
            tabAction = { type: 'next' }
        } else if (e.key === 'ArrowUp' || e.key === 'ArrowDown') {
            tabAction = { type: 'last-visited' }
        }
        if (tabAction) {
            e.preventDefault()
            e.stopPropagation()
            window.dispatchEvent(new CustomEvent('twicc:terminal-tab-shortcut', { detail: tabAction }))
        }
    }
    // Alt+Shift+{1-5, ←, →, ↑, ↓}: tab navigation within a session or project detail panel.
    // Dispatches a custom event handled by the active SessionView or ProjectDetailPanel instance.
    // (Index 5 is the session-only Orchestration tab; project-detail panels ignore it.)
    if (e.altKey && e.shiftKey && !e.ctrlKey && !e.metaKey && (SESSION_ROUTES.has(route.name) || PROJECT_DETAIL_ROUTES.has(route.name))) {
        let tabAction = null
        // Use e.code (physical key) for digits — e.key depends on keyboard layout
        // and modifiers (e.g. French AZERTY: Alt+Shift+number row produces unexpected e.key values).
        const digitMatch = e.code.match(/^(?:Digit|Numpad)([1-5])$/)
        if (digitMatch) {
            tabAction = { type: 'direct', index: parseInt(digitMatch[1]) }
        } else if (e.key === 'ArrowLeft') {
            tabAction = { type: 'prev' }
        } else if (e.key === 'ArrowRight') {
            tabAction = { type: 'next' }
        } else if (e.key === 'ArrowUp' || e.key === 'ArrowDown') {
            tabAction = { type: 'last-visited' }
        }
        if (tabAction) {
            e.preventDefault()
            e.stopPropagation()
            window.dispatchEvent(new CustomEvent('twicc:tab-shortcut', { detail: tabAction }))
        }
    }
    // Alt+Shift+O: toggle the Agent Settings popover. Only active when focus
    // is in the message input's wa-textarea (open intent) or already inside
    // the open agent-settings popover (close intent). Uses e.code (physical
    // key) because Alt+Shift+O produces a composed character on macOS (Ø)
    // which makes e.key unreliable across layouts.
    if (e.altKey && e.shiftKey && !e.ctrlKey && !e.metaKey && e.code === 'KeyO' && SESSION_CHAT_ROUTES.has(route.name)) {
        const active = document.activeElement
        const inTextarea = active?.tagName === 'WA-TEXTAREA' && active.closest('.message-input') !== null
        // The agent-settings popover is identified by .settings-panel-presets,
        // which the global app SettingsPopover (sharing the .settings-popover
        // class) doesn't render.
        const inAgentPopover = active?.closest('wa-popover')?.querySelector('.settings-panel-presets') != null
        if (inTextarea || inAgentPopover) {
            e.preventDefault()
            e.stopPropagation()
            window.dispatchEvent(new CustomEvent('twicc:toggle-agent-settings'))
        }
    }
    // Alt+Shift+M: focus the message input. Active on any session sub-route
    // (chat, files, git, terminal, subagent); navigates to the chat tab
    // first if needed. Standard focus-steal rules apply (see canStealFocus).
    //
    // Exception: the Agent Settings popover (identified by
    // .settings-panel-presets, since it shares the .settings-popover class
    // with the global app SettingsPopover) is NOT a blocker — instead
    // Alt+Shift+M closes it and refocuses the textarea, same as Alt+Shift+O
    // from inside the popover. This branch deliberately skips the in-editable
    // check (handled by hasBlockingOverlay only) so the shortcut still
    // closes the popover even if focus is inside one of its inputs.
    if (e.altKey && e.shiftKey && !e.ctrlKey && !e.metaKey && e.code === 'KeyM' && SESSION_ROUTES.has(route.name)) {
        const agentPopoverOpen = document.querySelector('wa-popover[open] .settings-panel-presets') !== null

        if (agentPopoverOpen && !hasBlockingOverlay({ allowPopoverContaining: '.settings-panel-presets' })) {
            // Reuse the toggle event — its handler closes the popover and
            // focuses the textarea when the popover was open.
            e.preventDefault()
            e.stopPropagation()
            window.dispatchEvent(new CustomEvent('twicc:toggle-agent-settings'))
        } else if (canStealFocus()) {
            e.preventDefault()
            e.stopPropagation()
            if (SESSION_CHAT_ROUTES.has(route.name)) {
                focusChatPrimary()
            } else {
                const chatRouteName = route.name.startsWith('projects-session') ? 'projects-session' : 'session'
                router.push({ name: chatRouteName, params: route.params }).then(() => focusChatPrimary())
            }
        }
    }
    // Triple-Escape: emergency stop of the current chat session's process.
    // Only active on chat routes (session, projects-session), only when a
    // stoppable process exists. Does NOT preventDefault/stopPropagation —
    // we let the Escape propagate so overlays close naturally.
    //
    // NOTE: the `return` statements below exit handleGlobalKeydown entirely.
    // This is intentional and safe because this IS the last branch in the
    // function. If you add a new shortcut AFTER this block, convert these
    // early returns to not exit the function.
    if (e.key === 'Escape' && !e.repeat) {
        const now = performance.now()

        if (now - lastTripleEscapeAt < TRIPLE_ESCAPE_COOLDOWN_MS) {
            // Swallow counting during cooldown (but still let the event bubble)
            return
        }

        if (!SESSION_CHAT_ROUTES.has(route.name)) {
            escapeTimestamps = []
            return
        }
        const sessionId = route.params.sessionId
        if (!sessionId) return

        const ps = dataStore.getProcessState(sessionId)
        const canStop = ps && !ps.synthetic && ps.state && ps.state !== PROCESS_STATE.DEAD
        if (!canStop) {
            escapeTimestamps = []
            return
        }

        // Reset the sequence if the gap since the previous press exceeded the
        // window. Otherwise append. We measure gap-to-previous, NOT distance-to-first,
        // so a natural tap-tap-tap at ~150 ms/key still triggers (3 gaps of 150 ms
        // each fit within the 200 ms per-gap budget, total span ~300–450 ms).
        const last = escapeTimestamps[escapeTimestamps.length - 1]
        if (last === undefined || now - last < TRIPLE_ESCAPE_WINDOW_MS) {
            escapeTimestamps.push(now)
        } else {
            escapeTimestamps = [now]
        }

        if (escapeTimestamps.length >= 3) {
            lastTripleEscapeAt = now
            escapeTimestamps = []
            // Defer to the next tick so the triggering Escape event finishes
            // propagating BEFORE the confirmation dialog opens. Otherwise the
            // wa-dialog would catch the still-bubbling Escape and close itself
            // immediately (only visible in the crons-confirmation path).
            setTimeout(() => stopSessionProcess(sessionId), 0)
        }
    }
}

const trustDialogRef = ref(null)

onMounted(() => {
    document.addEventListener('keydown', handleGlobalKeydown, { capture: true })
    registerTrustDialog(trustDialogRef.value)
})
onBeforeUnmount(() => {
    document.removeEventListener('keydown', handleGlobalKeydown, { capture: true })
})

// Notivue theme - inverted for contrast (dark theme when app is light, and vice-versa)
const toastTheme = computed(() => {
    const isDark = settingsStore.getEffectiveColorScheme === COLOR_SCHEME.DARK
    // Invert: use light toast theme when app is dark, and vice-versa
    return {
        ...(isDark ? lightTheme : slateTheme),
        '--nv-width': '100%',
        '--nv-min-width': '30rem',
    }
})
</script>

<template>
    <!-- Provider activation: non-dismissible first-run / recovery dialog -->
    <ProviderActivationDialog v-if="isAppReady" />

    <!-- Version mismatch: non-dismissible reload dialog -->
    <wa-dialog :open="versionMismatchDetected || undefined" without-header @wa-hide.prevent>
        <div class="version-reload-content">
            <wa-spinner></wa-spinner>
            <p class="version-reload-text">TwiCC has been updated, reloading…</p>
        </div>
    </wa-dialog>

    <!-- Connecting overlay: shown while waiting for backend during auth check retry -->
    <div v-if="isConnecting" class="connecting-backdrop">
        <div class="connecting-content">
            <wa-spinner></wa-spinner>
            <p class="connecting-text">Connecting to server...</p>
        </div>
    </div>

    <ConnectionIndicator v-if="isAppReady && !isConnecting" :status="wsStatus" />
    <CommandPalette ref="commandPaletteRef" />
    <SearchOverlay ref="searchOverlayRef" />
    <StopProcessConfirmDialog
        :open="pendingConfirmation !== null"
        :mode="pendingConfirmation?.mode ?? 'stop'"
        :cron-count="pendingConfirmation?.cronCount ?? 0"
        @confirm="confirmPendingStop"
        @cancel="cancelPendingStop"
    />
    <!-- Singleton dialog used to preview images & SVGs embedded in markdown
         (and any other "open this media fullscreen" call site that doesn't
         own its own MediaPreviewDialog instance). -->
    <GlobalMediaPreview />
    <!-- Global trust gate dialog, driven by composables/useTrustGate. -->
    <ProjectTrustDialog ref="trustDialogRef" />
    <!-- Prevent browser default drop behavior (e.g. navigating to a dropped image).
         Our specific drop handlers in SessionItemsList call preventDefault themselves;
         this catches any drops that miss those zones. -->
    <div class="app-container" @dragover.prevent @drop.prevent>
        <router-view />
    </div>

    <!-- Toast notification system (theme inverted for contrast) -->
    <Notivue v-slot="item">
        <CustomNotification v-if="item.props?.custom" :item="item" :theme="toastTheme" />
        <Notification v-else :item="item" :theme="toastTheme" />
    </Notivue>
</template>

<style>
.version-reload-content {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: var(--wa-space-m);
    padding: var(--wa-space-l);
    text-align: center;
}

.version-reload-text {
    font-size: var(--wa-font-size-l);
    color: var(--wa-color-text-normal);
    margin: 0;
}

.connecting-backdrop {
    position: fixed;
    inset: 0;
    display: flex;
    align-items: center;
    justify-content: center;
    background: var(--wa-color-surface-default);
    z-index: 10000;
}

.connecting-content {
    text-align: center;
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: var(--wa-space-m);
}

.connecting-text {
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-quiet);
    margin: 0;
}

/*
 * Override WA 3.3's "box-sizing: inherit" from @layer wa-native.
 * Native <details> elements break "inherit" propagation (both Chrome & Firefox):
 * children inside <details> fall back to content-box even when the parent is border-box.
 * This affects all slotted content inside wa-details. Using explicit border-box fixes it.
 * Our unlayered CSS has higher cascade priority than @layer wa-native.
 */
*, *::before, *::after {
    box-sizing: border-box;
}

body {
    margin: 0;
    padding: 0;
}

.app-container {
    min-height: 100dvh;
    background: var(--wa-color-surface-default);
    color: var(--wa-color-text-normal);
}

:root {
    overflow-y: auto;

    --selection-bg-color: oklch(from var(--wa-color-brand-50) l c h / 0.25);
    ::selection {
        background-color: var(--selection-bg-color);
    }

    --base-user-assistant-card-color: var(--wa-color-gray-95);
    --user-card-base-color: oklch(from var(--base-user-assistant-card-color) calc(l + 0.015) c h);
    --assistant-card-base-color: oklch(from var(--base-user-assistant-card-color) calc(l + 0.03) c h);

    --wa-font-size-3xs: round(calc(var(--wa-font-size-2xs) / 1.125), 1px);

    /* --main-header-footer-bg-color: var(--wa-color-surface-raised); */
    --main-header-footer-bg-color: transparent;

    /* Sparkline / heatmap graph colors (light mode) — green (default) */
    --sparkline-project-gradient-color-0: #ebedf0;
    --sparkline-project-gradient-color-1: #aceebb;
    --sparkline-project-gradient-color-2: #4ac26b;
    --sparkline-project-gradient-color-3: #2da44e;
    --sparkline-project-gradient-color-4: #116329;
    --sparkline-project-stroke-color: #8cc665;

    /* Sparkline graph colors — blue (sessions) */
    --sparkline-blue-gradient-color-1: #a8d4ff;
    --sparkline-blue-gradient-color-2: #4da6ff;
    --sparkline-blue-gradient-color-3: #1a7fdb;
    --sparkline-blue-gradient-color-4: #0a4f8a;
    --sparkline-blue-stroke-color: #6ab8f7;

    /* Sparkline graph colors — red (cost) */
    --sparkline-red-gradient-color-1: #ffb3b3;
    --sparkline-red-gradient-color-2: #ff5c5c;
    --sparkline-red-gradient-color-3: #d63333;
    --sparkline-red-gradient-color-4: #8b1a1a;
    --sparkline-red-stroke-color: #f77070;

    /* Sparkline graph colors — green (temporal) */
    --sparkline-green-gradient-color-1: #aceebb;
    --sparkline-green-gradient-color-2: #4ac26b;
    --sparkline-green-gradient-color-3: #2da44e;
    --sparkline-green-gradient-color-4: #116329;
    --sparkline-green-stroke-color: #8cc665;

    /* Sparkline graph colors — orange */
    --sparkline-orange-gradient-color-1: #ffe6b3;
    --sparkline-orange-gradient-color-2: #f5a623;
    --sparkline-orange-gradient-color-3: #d4760a;
    --sparkline-orange-gradient-color-4: #8a4d0f;
    --sparkline-orange-stroke-color: #e89d3f;

    /* Sparkline graph colors — purple (recent rate long) */
    --sparkline-purple-gradient-color-1: #e8d5f5;
    --sparkline-purple-gradient-color-2: #a855f7;
    --sparkline-purple-gradient-color-3: #7c3aed;
    --sparkline-purple-gradient-color-4: #4c1d95;
    --sparkline-purple-stroke-color: #b07ce8;

    /* Sparkline graph colors — pink (recent rate short) */
    --sparkline-pink-gradient-color-1: #fce4ec;
    --sparkline-pink-gradient-color-2: #f06292;
    --sparkline-pink-gradient-color-3: #d81b60;
    --sparkline-pink-gradient-color-4: #880e4f;
    --sparkline-pink-stroke-color: #e57399;

    --divider-size: 1px;
    &[data-theme="awesome"] {
        --divider-size: 4px;
    }

    --main-shadow-size: var(--wa-shadow-offset-y-s);

    /* Diff editor colors (light mode) */
    --diff-removedLineBackground: #FEF1F1;
    --diff-removedTextBackground: #FFC4C3;
    --diff-insertedLineBackground: #C0FFD8;
    --diff-insertedTextBackground: #A7E9B8;
    --diff-selectionBackground: var(--selection-bg-color);
}

.wa-dark {

    --base-user-assistant-card-color: var(--wa-color-surface-raised);
    --wa-color-brand-border-loud: var(--wa-color-brand-50);

    /* Sparkline / heatmap graph colors (dark mode) — green (default) */
    --sparkline-project-gradient-color-0: #151b23;
    --sparkline-project-gradient-color-1: #033a16;
    --sparkline-project-gradient-color-2: #196c2e;
    --sparkline-project-gradient-color-3: #2ea043;
    --sparkline-project-gradient-color-4: #56d364;
    --sparkline-project-stroke-color: #8cc665;

    /* Sparkline graph colors — blue (sessions, dark mode) */
    --sparkline-blue-gradient-color-1: #0a2d4f;
    --sparkline-blue-gradient-color-2: #1a5a8a;
    --sparkline-blue-gradient-color-3: #3a8fd4;
    --sparkline-blue-gradient-color-4: #6abef7;
    --sparkline-blue-stroke-color: #6ab8f7;

    /* Sparkline graph colors — red (cost, dark mode) */
    --sparkline-red-gradient-color-1: #4f0a0a;
    --sparkline-red-gradient-color-2: #8a1a1a;
    --sparkline-red-gradient-color-3: #d44040;
    --sparkline-red-gradient-color-4: #f77070;
    --sparkline-red-stroke-color: #f77070;

    /* Sparkline graph colors — green (temporal, dark mode) */
    --sparkline-green-gradient-color-1: #033a16;
    --sparkline-green-gradient-color-2: #196c2e;
    --sparkline-green-gradient-color-3: #2ea043;
    --sparkline-green-gradient-color-4: #56d364;
    --sparkline-green-stroke-color: #8cc665;

    /* Sparkline graph colors — orange (dark mode) */
    --sparkline-orange-gradient-color-1: #3d1e00;
    --sparkline-orange-gradient-color-2: #7a3c00;
    --sparkline-orange-gradient-color-3: #d4760a;
    --sparkline-orange-gradient-color-4: #f5a623;
    --sparkline-orange-stroke-color: #e89d3f;

    /* Sparkline graph colors — purple (recent rate long, dark mode) */
    --sparkline-purple-gradient-color-1: #2e1065;
    --sparkline-purple-gradient-color-2: #5b21b6;
    --sparkline-purple-gradient-color-3: #8b5cf6;
    --sparkline-purple-gradient-color-4: #c084fc;
    --sparkline-purple-stroke-color: #b07ce8;

    /* Sparkline graph colors — pink (recent rate short, dark mode) */
    --sparkline-pink-gradient-color-1: #4a0e2a;
    --sparkline-pink-gradient-color-2: #9d174d;
    --sparkline-pink-gradient-color-3: #ec4899;
    --sparkline-pink-gradient-color-4: #f9a8d4;
    --sparkline-pink-stroke-color: #e57399;

    /* Diff editor colors (dark mode) */
    --diff-removedLineBackground: #451B1B;
    --diff-removedTextBackground: #5E1B1B;
    --diff-insertedLineBackground: #1B452B;
    --diff-insertedTextBackground: #2A573B;
    --diff-selectionBackground: var(--selection-bg-color);
}

/* Reset Web Awesome button styles inside Notivue notifications */
.Notivue__close {
    all: unset;
    cursor: pointer;
    padding: calc(var(--nv-spacing) / 2);
    margin: var(--nv-spacing) var(--nv-spacing) var(--nv-spacing) 0;
    font-weight: 700;
    line-height: 1;
    font-size: var(--nv-message-size);
    color: var(--nv-fg, var(--nv-global-fg));
    -webkit-tap-highlight-color: transparent;
    position: relative;
    align-self: flex-start;
    /* Without this, the close button (a flex item of .Notivue__notification)
       gets squeezed by longer message content, distorting the X icon's width
       while keeping its height — Notivue applies the same protection on
       .Notivue__icon but forgot it here. */
    flex-shrink: 0;
}

/* Add separator line below title in notifications */
body .Notivue__content-title {
    border-bottom: 1px solid var(--nv-accent, var(--nv-global-accent));
    padding-bottom: var(--nv-spacing);
    margin-bottom: var(--nv-spacing);
}

/* Floating drag-hover indicator (spring-loaded folder pattern) */
.drag-hover-indicator {
    position: fixed;
    z-index: 10000;
    pointer-events: none;
    left: calc(var(--x) * 1px + 16px);
    top: calc(var(--y) * 1px - 16px);
    --size: 20px;
    --track-width: 2.5px;
    --indicator-color: var(--wa-color-success);
    --indicator-transition-duration: 0s;
}

/* Style for low-height buttons */

.reduced-height {
    font-size: var(--wa-font-size-3xs);
    &::part(label) {
        scale: 1.3;
    }
}

/* Keep blockquote normal size */
blockquote {
    font-family: inherit !important;
    font-size: inherit !important;
}

</style>
