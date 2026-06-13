<script setup>
// HybridTerminalBlock.vue - Embedded terminal for hybrid CLI sessions.
//
// Sits in the session footer, above the composer textarea. Shows the live
// claude TUI of the session's dedicated tmux session (attach-only 'h:'
// terminal context). Three sizes like the pending-request form: normal
// (capped height), minimized (collapsed bar), maximized (fills the session
// area). A pulsing badge appears when the TUI is blocked on a prompt the
// user must answer inside the terminal (the single PermissionRequest hook).
import { ref, computed, watch, watchEffect, useId, provide, onBeforeUnmount, markRaw } from 'vue'
import { useDataStore } from '../../stores/data'
import AppTooltip from '../ui/AppTooltip.vue'
import CollapsedBar from './CollapsedBar.vue'
import TerminalInstance from '../terminal/TerminalInstance.vue'

const props = defineProps({
    sessionId: {
        type: String,
        required: true
    }
})

const emit = defineEmits(['expand', 'show-pending-form'])

// Clicking the badge while the form is available brings it back if the user
// minimized it (no-op otherwise — handled by the parent wiring), and
// minimizes the terminal block: the user chose the form as their surface.
function onBadgeClick() {
    if (!pendingIsAnswerable.value) return
    emit('show-pending-form')
    minimize()
}

const store = useDataStore()

// ── Size state (window-controls style, single enum) ─────────────────────────
// Minimized by default: the composer keeps the room, and the CLI is steered
// from it without needing the terminal on screen. Safe for sizing — the CLI's
// 80-column width comes from the backend tmux session's default size (created
// detached at 80x24), not from the embedded xterm; opening the terminal later
// only ever widens it (the font-shrink floor keeps the attached view >= 80).
const viewState = ref('minimized')
const isMinimized = computed(() => viewState.value === 'minimized')
const isMaximized = computed(() => viewState.value === 'maximized')
const minimizeToggleId = useId()
const maximizeToggleId = useId()
const refreshBtnId = useId()
const badgeId = useId()

function minimize() {
    viewState.value = 'minimized'
}
function restore() {
    viewState.value = 'normal'
    emit('expand')
}
function toggleMaximized() {
    viewState.value = isMaximized.value ? 'normal' : 'maximized'
    if (isMaximized.value) emit('expand')
}
defineExpose({ minimize })

// ── Manual terminal refresh ──────────────────────────────────────────────────
// A purely front-side xterm.js freeze (renderer stuck, or a zombie WebSocket
// that still reads as connected so reconnect() would no-op) leaves the CLI
// running fine in the backend tmux but frozen/blank in the UI. Remounting the
// TerminalInstance through this key is the reliable cure: Vue disposes the old
// xterm and closes its socket, then a fresh instance mounts, refits and
// re-attaches to the same tmux session — which repaints the whole TUI.
const terminalRefreshKey = ref(0)
function refresh() {
    terminalRefreshKey.value++
}

// ── Process / terminal lifecycle ─────────────────────────────────────────────
// The CLI is launched lazily at the first send: before that there is no tmux
// session to attach to, so show a placeholder. Once a process exists, keep
// the terminal mounted while it lives — and drop back to the placeholder
// when the session is STOPPED (see the watchEffect below): the tmux session
// is killed along with the CLI, so the "Terminal disconnected / Reconnect"
// overlay would offer a reconnect to nothing.
const processState = computed(() => store.getProcessState(props.sessionId))
const hasProcess = computed(() => !!processState.value)
const everHadProcess = ref(false)
watch(hasProcess, (v) => { if (v) everHadProcess.value = true }, { immediate: true })

// Capture the embedded TerminalInstance's API (it registers itself through
// the same provide/inject contract as the Terminal panel) so we can drive
// reconnects when the CLI is relaunched.
// markRaw is REQUIRED: storing the api object in a ref would wrap it in a
// reactive proxy that auto-unwraps its nested refs — ``api.started.value``
// would then read ``.value`` on a plain boolean and silently yield
// undefined, breaking both watchers below. Reading the kept-intact refs
// inside watch/watchEffect still tracks them.
const termApi = ref(null)
provide('registerTerminal', (_index, api) => { termApi.value = markRaw(api) })
provide('unregisterTerminal', () => { termApi.value = null })

// Auto-reconnect: when a process (re)appears while the terminal sits
// disconnected, retry attaching a few times — the tmux session is created
// just AFTER the STARTING broadcast, so the first attempt can race it.
let reconnectTimers = []
function clearReconnectTimers() {
    reconnectTimers.forEach(clearTimeout)
    reconnectTimers = []
}
watch(processState, (state, oldState) => {
    if (!state || oldState) return
    clearReconnectTimers()
    for (const delay of [800, 2500, 6000]) {
        reconnectTimers.push(setTimeout(() => {
            const api = termApi.value
            if (!api) return
            if (!hasProcess.value) return
            if (api.started.value && !api.isConnected.value) api.reconnect()
        }, delay))
    }
})
onBeforeUnmount(clearReconnectTimers)

// Session stopped → back to the placeholder. Requires BOTH signals: the
// process is gone AND the attach explicitly exited (pty_exited is never set
// on network cuts, where the tmux may well be alive and the reconnect
// overlay is the right UI). A watchEffect covers both event orders —
// pty_exited can land before or after the process_state dead broadcast.
watchEffect(() => {
    if (!everHadProcess.value || hasProcess.value) return
    const api = termApi.value
    if (api && api.started.value && !api.isConnected.value && api.ptyExited.value) {
        everHadProcess.value = false
    }
})

// ── Pending-in-terminal badge ────────────────────────────────────────────────
// Any pending request means a prompt is up inside the TUI: answerable ones
// also render the PendingRequestForm widget (dual surface — first responder
// wins), so the badge steers the user to the widget first, terminal as the
// alternative. Requests degraded to `hybrid_terminal` (GUI channel expired)
// only have the terminal, and the badge says so.
const hybridPending = computed(() =>
    (store.getPendingRequests(props.sessionId) || [])[0] || null
)
// Same discriminant as SessionItemsList's PendingRequestForm gating: an
// answerable head request means the widget IS rendered above the composer.
const pendingIsAnswerable = computed(() =>
    !!hybridPending.value && hybridPending.value.request_type !== 'hybrid_terminal'
)
// Steer the user to the widget whenever it is available; only degraded
// badge-only requests (GUI channel expired) point at the terminal.
const badgeLabel = computed(() => {
    if (!hybridPending.value) return ''
    return pendingIsAnswerable.value ? 'Prefer answering in the UI' : 'Answer in the terminal'
})

// ── Terminal-blocked badge ───────────────────────────────────────────────────
// Backend-sourced live flag (process ``extra.terminal_blocked``): TwiCC tried
// to paste and the composer wasn't free — a TUI dialog is open or the user has
// text typed in the terminal. Because it rides on the process state, it is in
// the initial snapshot too, so any client (reloaded, or opened after the
// block happened) sees it. Surfaced only when there's no pending request —
// that case already has its own badge steering to the terminal/widget.
const terminalBlocked = computed(() => !!processState.value?.extra?.terminal_blocked)
</script>

<template>
    <wa-divider></wa-divider>
    <div class="hybrid-terminal-block" :class="{ maximized: isMaximized, minimized: isMinimized }">
        <!-- Minimized: single-line bar, same look as the composer's collapsed bar -->
        <CollapsedBar
            v-if="isMinimized"
            icon="terminal"
            label="Claude CLI terminal"
            expand-tooltip="Expand the terminal"
            @expand="restore"
        >
            <template #trailing>
                <!-- Only badges that require opening the terminal surface on the
                     minimized bar. "Prefer answering in the UI" (answerable
                     pending) is skipped here: its answer surface is the widget
                     above the composer, so the bar badge would be noise. No
                     @click — the whole collapsed bar expands the terminal. -->
                <span
                    v-if="hybridPending && !pendingIsAnswerable"
                    class="hybrid-pending-badge terminal-only"
                    role="status"
                >{{ badgeLabel }}</span>
                <span
                    v-else-if="terminalBlocked"
                    class="hybrid-pending-badge blocked"
                    role="status"
                >Terminal blocked — open it</span>
            </template>
        </CollapsedBar>
        <!-- Normal header (window controls) -->
        <div v-else class="hybrid-terminal-header">
            <wa-icon name="terminal" class="hybrid-terminal-icon"></wa-icon>
            <span class="hybrid-terminal-title">Claude CLI terminal</span>
            <span
                v-if="hybridPending"
                class="hybrid-pending-badge"
                :class="{ 'terminal-only': !pendingIsAnswerable, clickable: pendingIsAnswerable }"
                :id="badgeId"
                role="status"
                @click.stop="onBadgeClick"
            >{{ badgeLabel }}</span>
            <!-- Terminal already open: the block to resolve is visible below. -->
            <span
                v-else-if="terminalBlocked"
                class="hybrid-pending-badge blocked"
                role="status"
            >Terminal blocked</span>
            <wa-button
                v-if="everHadProcess"
                variant="neutral"
                appearance="plain"
                size="small"
                class="size-toggle-btn"
                :id="refreshBtnId"
                @click="refresh"
            >
                <wa-icon name="arrows-rotate" variant="classic"></wa-icon>
            </wa-button>
            <AppTooltip v-if="everHadProcess" :for="refreshBtnId">Refresh the terminal</AppTooltip>
            <wa-button
                variant="neutral"
                appearance="plain"
                size="small"
                class="size-toggle-btn"
                :id="minimizeToggleId"
                @click="minimize"
            >
                <wa-icon name="window-minimize" variant="classic"></wa-icon>
            </wa-button>
            <AppTooltip :for="minimizeToggleId">Minimize</AppTooltip>
            <wa-button
                variant="neutral"
                appearance="plain"
                size="small"
                class="size-toggle-btn"
                :id="maximizeToggleId"
                @click="toggleMaximized"
            >
                <wa-icon :name="isMaximized ? 'compress' : 'expand'" variant="classic"></wa-icon>
            </wa-button>
            <AppTooltip :for="maximizeToggleId">{{ isMaximized ? 'Restore' : 'Maximize' }}</AppTooltip>
        </div>

        <!-- Terminal area: placeholder until the CLI runs (and again after a
             stop — the tmux is gone, nothing to reconnect to), then the live
             TUI. Hidden by CSS while minimized so the xterm state survives. -->
        <div class="hybrid-terminal-body">
            <div v-if="!everHadProcess" class="hybrid-terminal-placeholder">
                <wa-icon name="terminal"></wa-icon>
                <span>The Claude CLI starts here when you send a message.</span>
            </div>
            <TerminalInstance
                v-else
                :key="terminalRefreshKey"
                :context-key="'h:' + sessionId"
                :session-id="sessionId"
                :active="!isMinimized"
            />
        </div>
    </div>
</template>

<style scoped>
wa-divider {
    --width: var(--divider-size);
    --spacing: 0;
}

.hybrid-terminal-block {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-2xs);
    padding: var(--wa-space-2xs) var(--wa-space-s);
    background: var(--wa-color-surface-default);
    height: 40dvh;
    &.maximized {
        height: unset;
        position: absolute;
        inset: 0;
        /* Above the composer (itself position: relative), below the
           drag/drop overlay at z-index 100. */
        z-index: 2;
    }
}

.hybrid-terminal-block.minimized {
    padding: 0;
    gap: 0;
    height: auto;
}
/* Keep the terminal mounted (xterm buffer survives) but hidden. */
.hybrid-terminal-block.minimized > .hybrid-terminal-body {
    display: none;
}

.hybrid-terminal-header {
    display: flex;
    align-items: center;
    gap: var(--wa-space-s);
    flex-shrink: 0;
}

.hybrid-terminal-icon {
    color: var(--wa-color-brand-fill-loud);
}

.hybrid-terminal-title {
    font-weight: var(--wa-font-weight-semibold);
    font-size: var(--wa-font-size-s);
    flex: 1;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}

.hybrid-pending-badge {
    flex-shrink: 0;
    font-size: var(--wa-font-size-s);
    font-weight: var(--wa-font-weight-semibold);
    color: var(--wa-color-warning-on-quiet);
    background: var(--wa-color-warning-fill-quiet);
    border: 1px solid var(--wa-color-warning-border-quiet);
    border-radius: var(--wa-border-radius-pill);
    padding: 0 var(--wa-space-s);
    line-height: 1.6;
}

/* Pulse only when the terminal is the ONLY answer surface (degraded
   badge-only request) — when the form is shown above, the badge is a quiet
   hint, not a call to action. */
.hybrid-pending-badge.terminal-only {
    animation: hybrid-badge-pulse 1.6s ease-in-out infinite;
}

/* When the form is available, the badge brings it back if minimized. */
.hybrid-pending-badge.clickable {
    cursor: pointer;
}

/* Terminal blocked: a problem that needs attention — danger colors + pulse.
   Distinct from the warning-toned pending badges (which are actionable, not
   broken). On the collapsed bar the whole bar expands the terminal on click. */
.hybrid-pending-badge.blocked {
    color: var(--wa-color-danger-on-quiet);
    background: var(--wa-color-danger-fill-quiet);
    border-color: var(--wa-color-danger-border-quiet);
    animation: hybrid-badge-pulse 1.6s ease-in-out infinite;
}

@keyframes hybrid-badge-pulse {
    50% { opacity: 0.55; }
}

.size-toggle-btn {
    flex-shrink: 0;
}

.hybrid-terminal-body {
    flex: 1;
    min-height: 0;
    display: flex;
    flex-direction: column;
}

.hybrid-terminal-placeholder {
    flex: 1;
    display: flex;
    align-items: center;
    justify-content: center;
    gap: var(--wa-space-s);
    color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-s);
    border: 1px dashed var(--wa-color-surface-border);
    border-radius: var(--wa-border-radius-m);
}
</style>
