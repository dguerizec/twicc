<script setup>
// PendingRequestBody.vue (Codex) — minimal stub.
//
// Renders the wire payload from the backend Codex approval bridge plus
// 3 action buttons (Approve / Deny / Cancel turn). PR2b is intentionally
// rough on the rendering side — PR3 will specialise the layout per
// ``tool_name`` (commandExecution / fileChange / permissions) and add the
// split-button Approve menu (Once / For session / + add allow rule).
//
// This component does NOT own:
// - The card outer wrapper (<wa-divider>, container div)
// - The shared header (icon + title + count badge + expand toggle)
// - The dispatch via respondToPendingRequest
//
// Instead, each button handler emits ('submit', payload) and the parent shell
// is responsible for dispatching the response.

import { computed } from 'vue'
import JsonHumanView from '../../../../json/JsonHumanView.vue'

const props = defineProps({
    sessionId: { type: String, required: true },
    pendingRequest: { type: Object, required: true },
    isResponding: { type: Boolean, default: false },
})

const emit = defineEmits(['submit'])

// Codex tool_name: 'commandExecution' | 'fileChange' | 'permissions'.
// Unknown tool_names fall through with a generic JSON view.
const toolName = computed(() => props.pendingRequest.tool_name || 'unknown')

// The wire params (as injected by the backend's make_pending_request).
const toolInput = computed(() => props.pendingRequest.tool_input || {})

// Whether the request type supports a "Cancel turn" decision. Permissions
// have no ``cancel`` wire variant per spec §1.1.c, so we hide the third
// button for them.
const supportsCancelTurn = computed(
    () => toolName.value === 'commandExecution' || toolName.value === 'fileChange',
)

/**
 * Build and emit the response payload. For commandExecution / fileChange
 * the payload is ``{tool_name, decision: <string>}``. For permissions the
 * Approve payload is ``{tool_name, permissions: <granted>, scope: 'turn'}``,
 * Deny is ``{tool_name, permissions: {}, scope: 'turn'}``.
 *
 * @param {'accept' | 'decline' | 'cancel'} action - The user's choice.
 */
function send(action) {
    if (props.isResponding) return

    const payload = { tool_name: toolName.value }
    if (toolName.value === 'permissions') {
        // Approve = grant exactly what was requested. Deny / cancel = empty.
        const granted = action === 'accept' ? (toolInput.value.permissions || {}) : {}
        payload.permissions = granted
        payload.scope = 'turn'
    } else {
        // commandExecution / fileChange / unknown — use the wire string.
        payload.decision = action
    }

    emit('submit', payload)
}

function handleApprove() { send('accept') }
function handleDeny() { send('decline') }
function handleCancelTurn() { send('cancel') }
</script>

<template>
    <div class="codex-pending-body">
        <div class="codex-pending-header">
            <span class="codex-pending-tool-badge">{{ toolName }}</span>
        </div>

        <div class="codex-pending-payload">
            <JsonHumanView :value="toolInput" />
        </div>

        <div class="codex-pending-actions">
            <wa-button variant="danger" :disabled="isResponding" @click="handleDeny">
                <wa-icon slot="start" name="xmark"></wa-icon>
                Deny
            </wa-button>
            <wa-button
                v-if="supportsCancelTurn"
                variant="neutral"
                :disabled="isResponding"
                @click="handleCancelTurn"
            >
                <wa-icon slot="start" name="stop"></wa-icon>
                Cancel turn
            </wa-button>
            <wa-button variant="success" :disabled="isResponding" @click="handleApprove">
                <wa-icon slot="start" name="check"></wa-icon>
                Approve
            </wa-button>
        </div>
    </div>
</template>

<style scoped>
.codex-pending-body {
    display: flex;
    flex-direction: column;
    gap: 0.75rem;
}

.codex-pending-header {
    display: flex;
    align-items: center;
}

.codex-pending-tool-badge {
    font-size: 0.75rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    padding: 0.25rem 0.5rem;
    background: var(--wa-color-neutral-90);
    border-radius: 4px;
    color: var(--wa-color-neutral-30);
}

.codex-pending-payload {
    max-height: 400px;
    overflow: auto;
    border: 1px solid var(--wa-color-neutral-90);
    border-radius: 6px;
    padding: 0.5rem;
    background: var(--wa-color-neutral-95);
}

.codex-pending-actions {
    display: flex;
    gap: 0.5rem;
    justify-content: flex-end;
    flex-wrap: wrap;
}
</style>
