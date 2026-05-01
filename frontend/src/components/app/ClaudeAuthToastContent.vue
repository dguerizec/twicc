<script setup>
/**
 * ClaudeAuthToastContent — rich content for the persistent "Claude CLI not
 * authenticated" toast.
 *
 * Includes:
 * - "Launch in terminal" — queues the login command for the global ("all
 *   projects") terminal view and navigates there.
 * - "Check again" — asks the backend to re-check the auth state right now
 *   (instead of waiting for the next periodic tick).
 */
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import { sendCheckAuth as sendCheckClaudeCodeAuth } from '../../providers/claude_code/ws'
import { useSettingsStore } from '../../stores/settings'
import { useTerminalCommandStore } from '../../stores/terminalCommand'

defineProps({
    /** Notivue item reference — passed by CustomNotification (unused here, but standard signature) */
    item: {
        type: Object,
        default: null,
    },
})

const settings = useSettingsStore()
const terminalCommandStore = useTerminalCommandStore()
const router = useRouter()

const loginCommand = computed(() => `${settings.twiccLaunchPrefix} claude auth login`)

// Disable the button briefly after a click to avoid spam-clicking while the
// backend round-trip happens.
const checking = ref(false)

function checkAgain() {
    if (checking.value) return
    checking.value = true
    sendCheckClaudeCodeAuth()
    setTimeout(() => {
        checking.value = false
    }, 1500)
}

function launchInTerminal() {
    terminalCommandStore.request('global', loginCommand.value)
    router.push({ name: 'projects-terminal' })
}
</script>

<template>
    <div class="claude-auth-toast-content">
        <p class="claude-auth-toast-message">
            Run <code>{{ loginCommand }}</code> to enable sending messages.
        </p>
        <div class="claude-auth-toast-actions wa-light">
            <wa-button size="small" variant="brand" appearance="outlined" @click="launchInTerminal">
                <wa-icon slot="start" name="terminal"></wa-icon>
                Launch in terminal
            </wa-button>
            <wa-button size="small" variant="brand" appearance="outlined" :disabled="checking" @click="checkAgain">
                Check again
            </wa-button>
        </div>
    </div>
</template>

<style scoped>
.claude-auth-toast-content {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-s);
    margin-top: var(--wa-space-xs);
}

.claude-auth-toast-message {
    margin: 0;
}

.claude-auth-toast-message code {
    font-family: var(--wa-font-family-code);
    font-size: 0.95em;
    padding: 0 var(--wa-space-3xs);
    background: var(--nv-accent, var(--nv-global-accent));
    border-radius: var(--wa-border-radius-s);
}

.claude-auth-toast-actions {
    display: flex;
    justify-content: flex-end;
    gap: var(--wa-space-xs);
    flex-wrap: wrap;
}
</style>
