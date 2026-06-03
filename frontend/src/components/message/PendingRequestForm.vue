<script setup>
// PendingRequestForm.vue - Thin shell for pending request forms.
//
// Owns only:
// - The card wrapper + wa-divider
// - The shared header (icon + title + count badge + expand toggle)
// - Per-provider body routing via <component :is="bodyComponent" />
// - The isResponding guard + the provider-agnostic respondToPendingRequest dispatch
//   (triggered by the body's @submit event)

import { ref, computed, watch, useId } from 'vue'
import { getProviderLabel, respondToPendingRequest } from '../../providers'
import { useDataStore } from '../../stores/data'
import { PROVIDER } from '../../constants'
import AppTooltip from '../ui/AppTooltip.vue'
import ClaudePendingRequestBody from '../session/detail/items/claude_code/PendingRequestBody.vue'
import CodexPendingRequestBody from '../session/detail/items/codex/PendingRequestBody.vue'

const props = defineProps({
    sessionId: {
        type: String,
        required: true
    },
    pendingRequest: {
        type: Object,
        required: true
    },
    /**
     * Total number of pending requests for this session, including this one.
     * When > 1, a counter badge is shown to indicate that more requests are queued
     * behind the current one (parallel concurrency-safe tools like Read + Glob can
     * each have their own permission ask within a single assistant turn).
     */
    pendingCount: {
        type: Number,
        default: 1
    }
})

// Number of additional pending requests waiting behind this one (>= 0).
const extraPendingCount = computed(() => Math.max(0, props.pendingCount - 1))

// Human-readable provider label for the current session, used in the form header.
const dataStore = useDataStore()
const providerLabel = computed(() => getProviderLabel(dataStore.getSession(props.sessionId)?.provider))

// Wire-key provider for the current session, used to route responses
// through the provider-agnostic dispatcher.
const provider = computed(() => dataStore.getSession(props.sessionId)?.provider)

// Whether a response has been sent and we're waiting for the store to clear the pending request
const isResponding = ref(false)

// Display size of the form, as a single mutually-exclusive state:
//   'normal'    — default, capped at 50dvh
//   'minimized' — header only (body hidden), to free room to read the conversation above
//   'maximized' — fills the whole session area
// The two toggle buttons below each flip between their own extreme and 'normal'
// (window-controls style); the single enum guarantees we can never be both
// minimized and maximized at once.
const viewState = ref('normal')
const isMinimized = computed(() => viewState.value === 'minimized')
const isMaximized = computed(() => viewState.value === 'maximized')

// Unique IDs for tooltip anchoring on the size toggle buttons
const minimizeToggleId = useId()
const maximizeToggleId = useId()

/**
 * Toggle between the minimized state and the normal size.
 */
function toggleMinimized() {
    viewState.value = isMinimized.value ? 'normal' : 'minimized'
}

/**
 * Toggle between the maximized state and the normal size.
 */
function toggleMaximized() {
    viewState.value = isMaximized.value ? 'normal' : 'maximized'
}

// Request type for conditional rendering of the header icon/title
const requestType = computed(() => props.pendingRequest.request_type)

// Route to the appropriate body component based on provider
const bodyComponent = computed(() => {
    if (provider.value === PROVIDER.CODEX) return CodexPendingRequestBody
    if (provider.value === PROVIDER.CLAUDE_CODE) return ClaudePendingRequestBody
    return null
})

/**
 * Dispatch the response when the body emits 'submit'.
 * Sets isResponding to guard against double-submission.
 * @param {Object} payload - The response payload from the body component
 */
function onBodySubmit(payload) {
    if (isResponding.value) return
    isResponding.value = true
    respondToPendingRequest(
        provider.value,
        props.sessionId,
        props.pendingRequest.request_id,
        payload,
    )
}

// Reset isResponding when the pending request changes (e.g., a new one arrives
// after the previous was resolved). The body owns its own internal state reset.
watch(() => props.pendingRequest?.request_id, () => {
    isResponding.value = false
    // A new request taking over the slot should never inherit the previous
    // request's minimized/maximized size — reset to the default so the new
    // request is always shown at normal size (and never hidden by a leftover
    // minimized state).
    viewState.value = 'normal'
})
</script>

<template>
    <!--
        Shell-only component. Per-provider rendering lives in the
        ``bodyComponent`` resolved by ``session.provider`` (Claude vs
        Codex). The dynamic ``:is="bodyComponent"`` avoids the SFC
        compiler limitation that bit PR2b when we tried to nest
        ``<template v-else-if>`` branches.
    -->
    <wa-divider></wa-divider>
    <div class="pending-request-form" :class="{ maximized: isMaximized, minimized: isMinimized }">
        <!-- Shared header. Title + icon vary on requestType. -->
        <div class="pending-request-header">
            <wa-icon
                :name="requestType === 'ask_user_question' ? 'circle-question' : 'shield-halved'"
                class="pending-request-icon"
                :class="{ 'question-icon': requestType === 'ask_user_question' }"
            ></wa-icon>
            <span class="pending-request-title">
                {{ requestType === 'ask_user_question'
                    ? `${providerLabel} needs your input`
                    : 'Tool approval requested' }}
            </span>
            <span
                v-if="extraPendingCount > 0"
                class="pending-count-badge"
                :id="`pending-count-${sessionId}`"
                role="status"
            >+{{ extraPendingCount }} pending</span>
            <AppTooltip
                v-if="extraPendingCount > 0"
                :for="`pending-count-${sessionId}`"
            >{{ extraPendingCount }} more request{{ extraPendingCount > 1 ? 's' : '' }} waiting after this one</AppTooltip>
            <wa-button
                variant="neutral"
                appearance="plain"
                size="small"
                class="size-toggle-btn"
                :id="minimizeToggleId"
                @click="toggleMinimized"
            >
                <wa-icon :name="isMinimized ? 'window-restore' : 'window-minimize'" variant="classic"></wa-icon>
            </wa-button>
            <AppTooltip :for="minimizeToggleId">{{ isMinimized ? 'Restore' : 'Minimize' }}</AppTooltip>
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

        <!-- Provider-routed body -->
        <component
            :is="bodyComponent"
            v-if="bodyComponent"
            :session-id="sessionId"
            :pending-request="pendingRequest"
            :is-responding="isResponding"
            @submit="onBodySubmit"
        />
    </div>
</template>

<style scoped>

wa-divider {
    --width: var(--divider-size);
    --spacing: 0;
}

.pending-request-form {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-s);
    padding: var(--wa-space-s);
    background: var(--wa-color-surface-default);
    max-height: 50dvh;
    &.maximized {
        max-height: unset;
        position: absolute;
        inset: 0;
    }
}

/* Minimized: collapse to the header only. The body is hidden (not unmounted), so
   the per-provider body component keeps its in-progress state (deny reason draft,
   question selections, edit mode) across a minimize/restore round-trip.
   :deep() is required because the body root belongs to a child component: Vue's
   scoped CSS only forwards this component's scope id to a *single*-root child
   (Codex body), not to the Claude body's fragment root — so without :deep the
   rule would silently miss the Claude body. We drop the scope requirement on the
   target and hide every direct child of the form that isn't the header (i.e. the
   body root, whatever provider owns it). The `>` keeps it to direct children. */
.pending-request-form.minimized > :deep(:not(.pending-request-header)) {
    display: none;
}

.pending-request-header {
    display: flex;
    align-items: center;
    gap: var(--wa-space-xs);
    color: var(--wa-color-warning-60);
    font-weight: 600;
}

.pending-request-title {
    flex: 1;
}

.pending-count-badge {
    display: inline-flex;
    align-items: center;
    background: var(--wa-color-warning-fill-loud);
    color: var(--wa-color-warning-on-loud);
    font-size: var(--wa-font-size-xs);
    font-weight: 600;
    padding: 2px var(--wa-space-xs);
    border-radius: var(--wa-border-radius-pill);
    line-height: 1;
    white-space: nowrap;
}

.size-toggle-btn {
    color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-s);
}

.question-icon {
    color: var(--wa-color-primary-60);
}

</style>
