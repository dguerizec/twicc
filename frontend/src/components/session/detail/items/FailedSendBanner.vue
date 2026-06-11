<script setup>
// Failure banner shown under a failed-send bubble (messaging pattern: an
// undeliverable message stays in the conversation flow, marked failed, with
// Retry / Edit / Delete actions). Provider-agnostic — the bubble itself is
// rendered by the provider's user-message renderer; this banner only reads
// the synthetic parsed content's ``failedSend`` field and the store entry.

import { computed, inject } from 'vue'
import { useDataStore } from '../../../../stores/data'
import { sendWsMessage } from '../../../../composables/useWebSocket'
import { generateUUID } from '../../../../utils/crypto'
import { mediasToSdkFormat } from '../../../../utils/fileUtils'

const props = defineProps({
    // Parsed content of the synthetic item (carries ``failedSend``)
    content: {
        type: Object,
        required: true
    },
    projectId: {
        type: String,
        required: true
    },
    sessionId: {
        type: String,
        required: true
    }
})

const store = useDataStore()
const insertTextAtCursor = inject('insertTextAtCursor', null)

const failedSend = computed(() => props.content?.failedSend || null)

function getEntry() {
    const requestId = failedSend.value?.requestId
    return requestId ? store.getFailedSend(props.sessionId, requestId) : null
}

/**
 * Re-send the message as-is. The payload re-sends the session's CURRENT
 * settings untouched: the backend overwrites the stored settings bundle with
 * whatever the payload carries, so omitting them would reset the session's
 * forced settings to "use global default".
 */
function retry() {
    const entry = getEntry()
    if (!entry) return
    const session = store.getSession(props.sessionId)
    const requestId = generateUUID()
    const { images, documents } = mediasToSdkFormat(entry.medias || [])
    const payload = {
        type: 'send_message',
        session_id: props.sessionId,
        project_id: props.projectId,
        provider: session?.provider,
        text: entry.text,
        permission_mode: session?.permission_mode ?? null,
        selected_model: session?.selected_model ?? null,
        effort: session?.effort ?? null,
        thinking_enabled: session?.thinking_enabled ?? null,
        claude_in_chrome: session?.claude_in_chrome ?? null,
        fast_mode: session?.fast_mode ?? null,
        context_max: session?.context_max ?? null,
        request_id: requestId,
    }
    if (images.length) payload.images = images
    if (documents.length) payload.documents = documents
    // WebSocket down: keep the failed bubble, the user can retry later
    if (!sendWsMessage(payload)) return
    store.registerOutgoingSend(props.sessionId, props.projectId, requestId, {
        text: entry.text,
        medias: entry.medias || [],
        images,
        documents,
    })
    store.removeFailedSend(props.sessionId, entry.requestId)
}

/** Put the message back into the composer for rework, then drop the bubble. */
async function edit() {
    const entry = getEntry()
    if (!entry || !insertTextAtCursor) return
    insertTextAtCursor(entry.text)
    if (entry.medias?.length) {
        await store.restoreDraftAttachments(props.sessionId, entry.medias)
    }
    store.removeFailedSend(props.sessionId, entry.requestId)
}

function discard() {
    const entry = getEntry()
    if (!entry) return
    store.removeFailedSend(props.sessionId, entry.requestId)
}
</script>

<template>
    <div v-if="failedSend" class="failed-send-banner">
        <div class="failed-send-reason">
            <wa-icon name="triangle-exclamation"></wa-icon>
            <span>{{ failedSend.message }}</span>
        </div>
        <span v-if="failedSend.mediasDropped" class="failed-send-note">
            Its attachments were too large to preserve — only the text can be retried or edited.
        </span>
        <div class="failed-send-actions">
            <wa-button size="small" variant="danger" appearance="outlined" @click="retry">
                <wa-icon slot="start" name="rotate-right"></wa-icon>
                Retry
            </wa-button>
            <wa-button v-if="insertTextAtCursor" size="small" variant="neutral" appearance="outlined" @click="edit">
                <wa-icon slot="start" name="pen"></wa-icon>
                Edit
            </wa-button>
            <wa-button size="small" variant="neutral" appearance="plain" @click="discard">
                Delete
            </wa-button>
        </div>
    </div>
</template>

<style scoped>
.failed-send-banner {
    display: flex;
    flex-direction: column;
    align-items: flex-end;
    gap: var(--wa-space-xs);
    margin-top: var(--wa-space-2xs);
}

.failed-send-reason {
    display: flex;
    align-items: baseline;
    gap: var(--wa-space-2xs);
    color: var(--wa-color-danger-60);
    font-size: var(--wa-font-size-s);
    text-align: right;
}

.failed-send-note {
    color: var(--wa-color-danger-60);
    font-size: var(--wa-font-size-s);
    font-weight: var(--wa-font-weight-semibold);
    text-align: right;
}

.failed-send-actions {
    display: flex;
    align-items: center;
    gap: var(--wa-space-xs);
}
</style>
