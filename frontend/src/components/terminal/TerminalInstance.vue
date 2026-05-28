<script setup>
import { inject, onUnmounted, watch } from 'vue'
import { useTerminal } from '../../composables/useTerminal'

const emit = defineEmits(['disconnected'])

const props = defineProps({
    contextKey: {
        type: String,
        required: true,
    },
    sessionId: {
        type: String,
        default: null,
    },
    projectId: {
        type: String,
        default: null,
    },
    cwd: {
        type: String,
        default: null,
    },
    terminalIndex: {
        type: Number,
        default: 0,
    },
    active: {
        type: Boolean,
        default: false,
    },
})

const {
    containerRef, isConnected, isReady, started, ptyExited, start, reconnect, disconnect, focus,
    touchMode, hasSelection, copySelection, getSelectionText,
    paneAlternate,
    canScrollUp, canScrollDown,
    scrollToEdge, scrollingToEdge, cancelScrollToEdge,
    activeModifiers, lockedModifiers,
    handleExtraKeyInput, handleExtraKeyModifierToggle, handleExtraKeyPaste,
    handleComboPress, handleSnippetPress,
} = useTerminal(props.contextKey, props.terminalIndex, {
    sessionId: props.sessionId,
    projectId: props.projectId,
    cwd: props.cwd,
})

// Register terminal API with parent (TerminalPanel) for toolbar + ExtraKeysBar routing
const registerTerminal = inject('registerTerminal', null)
const unregisterTerminal = inject('unregisterTerminal', null)

const terminalApi = {
    // ExtraKeysBar handlers
    activeModifiers,
    lockedModifiers,
    handleExtraKeyInput,
    handleExtraKeyModifierToggle,
    handleExtraKeyPaste,
    handleComboPress,
    handleSnippetPress,
    // Toolbar state (refs)
    isConnected,
    isReady,
    started,
    canScrollUp,
    canScrollDown,
    paneAlternate,
    scrollingToEdge,
    hasSelection,
    touchMode,
    // Toolbar actions (functions)
    scrollToEdge,
    cancelScrollToEdge,
    copySelection,
    getSelectionText,
    disconnect,
    reconnect,
    focus,
}
registerTerminal?.(props.terminalIndex, terminalApi)
onUnmounted(() => {
    unregisterTerminal?.(props.terminalIndex)
})

// Notify parent when the terminal's PTY exits (Ctrl+D, `exit`, shell crash, etc.)
// Only emits when the backend explicitly signals pty_exited — NOT on network disconnects
// (where the user should see a reconnect overlay instead).
// Vue stops watchers during unmount, so this does NOT fire when the component is
// destroyed by removeTerminalTab — only when the WS closes while the component is alive.
watch(isConnected, (connected, wasConnected) => {
    if (wasConnected && !connected && ptyExited.value) {
        emit('disconnected')
    }
})

// Lazy init: start the terminal only when the tab becomes active for the first time
watch(
    () => props.active,
    (active) => {
        if (active && !started.value) {
            start()
        }
    },
    { immediate: true },
)
</script>

<template>
    <div class="terminal-area">
        <div ref="containerRef" class="terminal-container"></div>

        <!-- Disconnect overlay (only covers terminal area, not ExtraKeysBar) -->
        <div v-if="started && !isConnected" class="disconnect-overlay">
            <wa-callout variant="warning" appearance="outlined">
                <wa-icon slot="icon" name="plug-circle-xmark"></wa-icon>
                <div class="disconnect-content">
                    <div>Terminal disconnected</div>
                    <wa-button
                        variant="warning"
                        appearance="outlined"
                        size="small"
                        @click="reconnect"
                    >
                        <wa-icon slot="start" name="arrow-rotate-right"></wa-icon>
                        Reconnect
                    </wa-button>
                </div>
            </wa-callout>
        </div>
    </div>
</template>

<style scoped>
.terminal-area {
    flex: 1;
    min-height: 0;
    position: relative;
}

.terminal-container {
    height: 100%;
    width: 100%;
    padding: var(--wa-space-2xs);
}

/* Ensure xterm fills its container */
.terminal-container :deep(.xterm) {
    height: 100%;
}

.terminal-container :deep(.xterm-viewport) {
    overflow-y: auto !important;
}

.disconnect-overlay {
    position: absolute;
    inset: 0;
    display: flex;
    align-items: center;
    justify-content: center;
    background: rgba(0, 0, 0, 0.4);
    z-index: 10;
}

.disconnect-content {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: var(--wa-space-m);
    text-align: center;
}
</style>
