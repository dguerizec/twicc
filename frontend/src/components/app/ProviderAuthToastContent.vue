<script setup>
/**
 * ProviderAuthToastContent — rich content for the persistent
 * "<provider> CLI not authenticated" toast.
 *
 * Includes:
 * - "Launch in terminal" — queues the login command for the global ("all
 *   projects") terminal view and navigates there.
 * - "Check again" — asks the backend to re-check the auth state right now
 *   (instead of waiting for the next periodic tick).
 */
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { useTerminalCommandStore } from '../../stores/terminalCommand'
import { getProviderHelpers } from '../../providers'

const props = defineProps({
    /** Notivue item reference — passed by CustomNotification (unused here, but standard signature) */
    item: {
        type: Object,
        default: null,
    },
    /** Wire key of the provider this toast belongs to. */
    provider: {
        type: String,
        required: true,
    },
    /** Pre-resolved login command string. */
    loginCommand: {
        type: String,
        required: true,
    },
})

const terminalCommandStore = useTerminalCommandStore()
const router = useRouter()

// Disable the button briefly after a click to avoid spam-clicking while the
// backend round-trip happens.
const checking = ref(false)

function checkAgain() {
    if (checking.value) return
    checking.value = true
    getProviderHelpers(props.provider)?.requestAuthRecheck()
    setTimeout(() => {
        checking.value = false
    }, 1500)
}

function launchInTerminal() {
    terminalCommandStore.request('global', props.loginCommand)
    router.push({ name: 'projects-terminal' })
}
</script>

<template>
    <div class="provider-auth-toast-content">
        <p class="provider-auth-toast-message">
            Run <code>{{ loginCommand }}</code> to enable sending messages.
        </p>
        <div class="provider-auth-toast-actions wa-light">
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
.provider-auth-toast-content {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-s);
    margin-top: var(--wa-space-xs);
}

.provider-auth-toast-message {
    margin: 0;
}

.provider-auth-toast-message code {
    font-family: var(--wa-font-family-code);
    font-size: 0.95em;
    padding: 0 var(--wa-space-3xs);
    background: var(--nv-accent, var(--nv-global-accent));
    border-radius: var(--wa-border-radius-s);
}

.provider-auth-toast-actions {
    display: flex;
    justify-content: flex-end;
    gap: var(--wa-space-xs);
    flex-wrap: wrap;
}
</style>
