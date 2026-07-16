<script setup>
// PlanImplementationBody.vue (codex) — body sub-component for a
// ``planImplementation`` pending request (request_type ``ask_user_question``).
//
// TwiCC-owned post-plan prompt: raised by the agent when a Plan
// collaboration-mode turn delivers its final plan (the ``<proposed_plan>``
// message right above this form). Mirrors the official Codex TUI's
// "Implement this plan?" menu (labels and descriptions included), minus the
// "clear context and implement" fresh-session option — deliberately deferred.
//
// Self-contained: owns its entire body including the action row. The wire
// decision is interpreted by the backend agent
// (``CodexAgent._prompt_plan_implementation``): ``implement`` switches the
// thread back to Default collaboration mode and runs the fixed
// "Implement the plan." turn; ``stay`` keeps Plan mode and returns control.

import { nextTick, onMounted, ref, useId, watch } from 'vue'
import AppTooltip from '../../../../ui/AppTooltip.vue'
import { usePendingRequestSubmitShortcut } from '../../../../../composables/usePendingRequestSubmitShortcut'
import { canStealFocus } from '../../../../../utils/focusGuard'

const props = defineProps({
    pendingRequest: { type: Object, required: true },
    isResponding: { type: Boolean, default: false },
})
const emit = defineEmits(['submit'])

const stayButtonId = useId()
const implementButtonId = useId()
const implementButtonRef = ref(null)

function implement() {
    emit('submit', { tool_name: 'planImplementation', decision: 'implement' })
}

function stay() {
    emit('submit', { tool_name: 'planImplementation', decision: 'stay' })
}

function focusImplement() {
    nextTick(() => {
        if (!canStealFocus()) return
        implementButtonRef.value?.focus()
    })
}
onMounted(focusImplement)
watch(() => props.pendingRequest?.request_id, focusImplement)

usePendingRequestSubmitShortcut((event) => {
    event.preventDefault()
    event.stopPropagation()
    if (document.activeElement?.id === stayButtonId) stay()
    else implement()
}, () => props.isResponding)
</script>

<template>
    <div class="plan-implementation-body">
        <div class="plan-section">
            <span class="summary-label">Implement this plan?</span>
            <span class="plan-hint">
                Codex proposed the plan above. Implement it now, or keep refining it in Plan mode.
            </span>
        </div>

        <div class="plan-actions">
            <wa-button
                :id="stayButtonId"
                variant="neutral"
                appearance="outlined"
                size="small"
                :disabled="isResponding"
                @click="stay"
            >
                <wa-icon slot="start" name="comments" variant="classic"></wa-icon>
                No, stay in Plan mode
            </wa-button>
            <AppTooltip :for="stayButtonId">Continue planning with the model.</AppTooltip>

            <wa-button
                :id="implementButtonId"
                ref="implementButtonRef"
                class="auto-focused"
                variant="brand"
                size="small"
                :disabled="isResponding"
                @click="implement"
            >
                <wa-icon slot="start" name="check" variant="classic"></wa-icon>
                Yes, implement this plan
            </wa-button>
            <AppTooltip :for="implementButtonId">Switch to Default and start coding.</AppTooltip>
        </div>
    </div>
</template>

<style scoped>
.plan-implementation-body {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-s);
    flex: 1;
    min-height: 0;
    overflow-y: auto;
}

.plan-section {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-xs);
    padding: var(--wa-space-s);
    background: var(--wa-color-neutral-5);
    border-radius: var(--wa-border-radius-m);
}

.summary-label {
    color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-xs);
    font-weight: 600;
    letter-spacing: 0.05em;
    text-transform: uppercase;
}

.plan-hint {
    font-size: var(--wa-font-size-m);
}

.plan-actions {
    display: flex;
    flex-wrap: wrap;
    justify-content: flex-end;
    gap: var(--wa-space-s);
}

/* Keep the focus outline visible on the primary button even for mouse /
   programmatic focus (same rationale as PendingRequestBody). */
wa-button.auto-focused:focus-within::part(base) {
    outline: var(--wa-focus-ring);
    outline-offset: var(--wa-focus-ring-offset);
}
</style>
