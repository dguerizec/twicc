<script setup>
// RequestUserInputBody.vue (codex) — body sub-component for a
// ``toolRequestUserInput`` pending request (request_type ``ask_user_question``).
//
// Wire params (tool_input): { threadId, turnId, itemId, questions: [
//   { id, header, question, isOther, isSecret, options: [{label, description}] | null }
// ] }
//
// This is the same shape the MCP-approval fallback uses (options labelled
// "Allow" / "Allow for this session" / "Allow and don't ask me again" /
// "Cancel") — we render whatever labels/descriptions we're given, no
// special-casing.
//
// Self-contained: unlike the sibling approval bodies, this component owns
// its entire body including the action row (Dismiss / Submit).

import { computed, nextTick, onMounted, ref, useId, watch } from 'vue'
import AppTooltip from '../../../../ui/AppTooltip.vue'
import { canStealFocus } from '../../../../../utils/focusGuard'

const props = defineProps({
    pendingRequest: { type: Object, required: true },
    isResponding: { type: Boolean, default: false },
    sessionId: { type: String, required: true },
})
const emit = defineEmits(['submit'])

const dismissButtonId = useId()
const submitButtonId = useId()

// Wire params.
const questions = computed(() => {
    const qs = props.pendingRequest.tool_input?.questions
    return Array.isArray(qs) ? qs : []
})

// Per-question-index state, keyed by array index (questions have no stable
// position guarantee across requests, but request_id changes reset all of this).
const selections = ref({}) // idx -> selected option label
const otherTexts = ref({}) // idx -> free text
const otherActive = ref({}) // idx -> bool ("Other"/bare-input is the active choice)

function resetState() {
    selections.value = {}
    otherTexts.value = {}
    otherActive.value = {}
}

// Template ref for the primary control (first option card of the first
// question, or its text input when that question has no options) —
// auto-focused on mount and whenever a new request takes over the slot.
const primaryRef = ref(null)
function setPrimaryRef(el, isPrimary) {
    if (isPrimary) primaryRef.value = el
}

// Template refs for "Other" / bare text inputs, keyed by question index —
// used to move focus into the field right after it's activated.
const textInputRefs = ref({})
function setTextInputRef(idx, el) {
    if (el) textInputRefs.value[idx] = el
}

function focusPrimary() {
    nextTick(() => {
        if (!canStealFocus()) return
        primaryRef.value?.focus()
    })
}

onMounted(focusPrimary)

// Reset per-question state and re-focus when a new request takes over the slot.
watch(() => props.pendingRequest?.request_id, () => {
    resetState()
    focusPrimary()
})

function hasOptions(question) {
    return Array.isArray(question.options) && question.options.length > 0
}

function selectOption(idx, label) {
    if (props.isResponding) return
    selections.value[idx] = label
    otherActive.value[idx] = false
}

function activateOther(idx) {
    if (props.isResponding) return
    otherActive.value[idx] = true
    selections.value[idx] = null
    nextTick(() => textInputRefs.value[idx]?.focus())
}

function onTextInput(idx, event) {
    otherTexts.value[idx] = event.target.value
    otherActive.value[idx] = true
}

// Resolved answer for a question: the free text when "Other"/bare-input is
// active (or the question has no options at all), otherwise the selected
// option label. null means unanswered.
function answerFor(idx) {
    const q = questions.value[idx]
    if (!q) return null
    if (otherActive.value[idx] || !hasOptions(q)) {
        const text = (otherTexts.value[idx] || '').trim()
        return text ? text : null
    }
    return selections.value[idx] ?? null
}

function isOptionSelected(idx, label) {
    return !otherActive.value[idx] && selections.value[idx] === label
}

const allAnswered = computed(() =>
    questions.value.length > 0 && questions.value.every((q, idx) => answerFor(idx) !== null))

function submit() {
    if (props.isResponding || !allAnswered.value) return
    const answers = {}
    for (const [idx, q] of questions.value.entries()) {
        answers[q.id] = { answers: [answerFor(idx)] }
    }
    emit('submit', { tool_name: 'toolRequestUserInput', answers })
}

function dismiss() {
    if (props.isResponding) return
    // Empty answers map: Codex treats a missing answer as a cancel.
    emit('submit', { tool_name: 'toolRequestUserInput', answers: {} })
}
</script>

<template>
    <div class="codex-pending-body">
        <div class="questions-container">
            <div
                v-for="(question, qIndex) in questions"
                :key="question.id ?? qIndex"
                class="codex-pending-section"
            >
                <span v-if="question.header" class="codex-summary-label">{{ question.header }}</span>
                <div class="question-text">{{ question.question }}</div>

                <!-- Option cards, keyboard-accessible via native <button> semantics. -->
                <div v-if="hasOptions(question)" class="question-options">
                    <button
                        v-for="(option, optionIndex) in question.options"
                        :key="option.label"
                        type="button"
                        class="option-card"
                        :class="{
                            selected: isOptionSelected(qIndex, option.label),
                            'auto-focused': qIndex === 0 && optionIndex === 0,
                        }"
                        :disabled="isResponding"
                        :ref="el => setPrimaryRef(el, qIndex === 0 && optionIndex === 0)"
                        @click="selectOption(qIndex, option.label)"
                    >
                        <span class="option-label">{{ option.label }}</span>
                        <span v-if="option.description" class="option-description">{{ option.description }}</span>
                    </button>

                    <button
                        v-if="question.isOther"
                        type="button"
                        class="option-card other-card"
                        :class="{ selected: otherActive[qIndex] }"
                        :disabled="isResponding"
                        @click="activateOther(qIndex)"
                    >
                        <span class="option-label">Other…</span>
                    </button>
                </div>

                <!-- Free-text alternative alongside options ("Other"). -->
                <div v-if="question.isOther && hasOptions(question)" class="text-input-row">
                    <wa-input
                        :ref="el => setTextInputRef(qIndex, el)"
                        :type="question.isSecret ? 'password' : 'text'"
                        placeholder="Type your answer..."
                        size="small"
                        :value.prop="otherTexts[qIndex] || ''"
                        :disabled="isResponding"
                        @input="onTextInput(qIndex, $event)"
                        @focus="activateOther(qIndex)"
                    ></wa-input>
                </div>

                <!-- Pure free-text question (no options at all). -->
                <div v-else-if="!hasOptions(question)" class="text-input-row">
                    <wa-input
                        :ref="el => setPrimaryRef(el, qIndex === 0)"
                        :class="{ 'auto-focused': qIndex === 0 }"
                        :type="question.isSecret ? 'password' : 'text'"
                        placeholder="Type your answer..."
                        size="small"
                        :value.prop="otherTexts[qIndex] || ''"
                        :disabled="isResponding"
                        @input="onTextInput(qIndex, $event)"
                    ></wa-input>
                </div>
            </div>
        </div>

        <div class="codex-pending-actions">
            <wa-button
                :id="dismissButtonId"
                variant="neutral"
                appearance="outlined"
                size="small"
                :disabled="isResponding"
                @click="dismiss"
            >
                <wa-icon slot="start" name="ban" variant="classic"></wa-icon>
                Dismiss
            </wa-button>
            <AppTooltip :for="dismissButtonId">Dismiss without answering — Codex treats it as cancelled.</AppTooltip>

            <wa-button
                :id="submitButtonId"
                variant="brand"
                size="small"
                :disabled="isResponding || !allAnswered"
                @click="submit"
            >
                <wa-icon slot="start" name="check" variant="classic"></wa-icon>
                Submit
            </wa-button>
            <AppTooltip :for="submitButtonId">Send your answers.</AppTooltip>
        </div>
    </div>
</template>

<style scoped>
.codex-pending-body {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-s);
    flex: 1;
    min-height: 0;
    overflow-y: auto;
}

.codex-pending-section {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-xs);
    background: var(--wa-color-neutral-5);
    border-radius: var(--wa-border-radius-m);
    padding: var(--wa-space-s);
}

.codex-summary-label {
    color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-xs);
    text-transform: uppercase;
    letter-spacing: 0.05em;
    font-weight: 600;
}

.codex-pending-actions {
    display: flex;
    flex-wrap: wrap;
    justify-content: flex-end;
    gap: var(--wa-space-s);
}

.questions-container {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-m);
    overflow-y: auto;
    flex: 1;
    min-height: 0;
}

.question-text {
    line-height: 1.4;
}

.question-options {
    display: flex;
    flex-wrap: wrap;
    gap: var(--wa-space-s);
    margin-top: var(--wa-space-2xs);
}

/* Plain <button> styled as a card — native focus/keyboard semantics (Tab,
   Enter, Space) come for free, no custom keydown handling needed. */
.option-card {
    flex: 1 1 0;
    min-width: min-content;
    max-width: 20rem;
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-s);
    text-align: left;
    cursor: pointer;
    font: inherit;
    color: inherit;
    transition: border-color 0.15s, background-color 0.15s;
    padding: var(--wa-space-s);
    border-radius: var(--wa-border-radius-m);
    border: 1px solid var(--border-color-base, var(--wa-color-surface-border));
    background: var(--background-color-base, var(--wa-color-surface-raised));

    --border-color-base: var(--wa-color-surface-border);
    --background-color-base: var(--wa-color-surface-raised);

    box-shadow: var(--wa-shadow-offset-x-s) var(--wa-shadow-offset-y-s) 0 0 var(--border-color-base);
}

.option-card:hover:not(:disabled) {
    --border-color-base: oklch(from var(--wa-color-surface-border) calc(l + 0.025) c h);
    --background-color-base: oklch(from var(--wa-color-surface-raised) calc(l + 0.025) c h);
}

.option-card.selected {
    --border-color-base: var(--wa-color-border-normal);
    --background-color-base: var(--wa-color-fill-normal);
}

.option-card:disabled {
    opacity: 0.6;
    cursor: not-allowed;
}

.option-card:focus-visible {
    outline: 2px solid var(--wa-color-brand-fill-loud);
    outline-offset: 2px;
}

/* Always show the focus outline on the primary target of the form (first
   option card, or the lone text input for an options-less question),
   whether focus lands there via mouse click, Tab, or the programmatic
   auto-focus on mount / new request. Default :focus-visible would skip
   mouse and programmatic focus, which hides the indicator here. */
.option-card.auto-focused:focus-within {
    outline: 2px solid var(--wa-color-brand-fill-loud);
    outline-offset: 2px;
}

wa-input.auto-focused:focus-within::part(base) {
    outline: var(--wa-focus-ring);
    outline-offset: var(--wa-focus-ring-offset);
}

.option-label {
    font-weight: 600;
    color: var(--wa-color-text);
    line-height: 1.4;
}

.option-description {
    display: block;
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-quiet);
    line-height: 1.3;
}

.text-input-row {
    margin-top: var(--wa-space-2xs);
}

.text-input-row wa-input {
    width: 100%;
}
</style>
