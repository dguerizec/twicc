<script setup>
// ElicitationFormBody.vue (codex) — body sub-component for an
// ``elicitationForm`` pending request (request_type ``ask_user_question``,
// mode ``form``, no approval ``_meta`` tag — untagged elicitations route
// here instead of McpToolCallApprovalBody).
//
// Wire params (tool_input): McpServerElicitationRequestParams —
// { threadId, turnId, serverName, mode: 'form', _meta, message,
//   requestedSchema: { type: 'object', properties: {...}, required: [...] } }
// (MCP 2025-11-25 ``ElicitRequestFormParams``).
//
// Self-contained: like RequestUserInputBody, this component owns its entire
// body including the action row (Cancel / Decline / Submit).

import { computed, nextTick, onMounted, ref, useId, watch } from 'vue'
import AppTooltip from '../../../../ui/AppTooltip.vue'
import { canStealFocus } from '../../../../../utils/focusGuard'

const props = defineProps({
    pendingRequest: { type: Object, required: true },
    isResponding: { type: Boolean, default: false },
    sessionId: { type: String, required: true },
})
const emit = defineEmits(['submit'])

const cancelButtonId = useId()
const declineButtonId = useId()
const submitButtonId = useId()

// Wire params.
const input = computed(() => props.pendingRequest.tool_input || {})
const serverName = computed(() => input.value.serverName || 'unknown server')
const message = computed(() => input.value.message || '')
const schema = computed(() => input.value.requestedSchema || {})
const requiredSet = computed(() => new Set(schema.value.required || []))

function classify(spec) {
    if (spec.type === 'boolean') return 'boolean'
    if (spec.type === 'number' || spec.type === 'integer') return 'number'
    if (spec.type === 'array') return 'multiselect' // items.enum / items.anyOf|oneOf
    if (spec.type === 'string' && (spec.enum || spec.oneOf)) return 'select'
    if (spec.type === 'string') return 'text'
    return 'unsupported' // rendered as a read-only notice, never blocks submit
}

// Normalised options for select/multiselect:
// enum:[v] (+ enumNames:[n]) → [{value: v, label: n||v}]
// oneOf/anyOf:[{const, title}] → [{value: const, label: title}]
function optionsFor(spec) {
    if (spec.items?.enum) return spec.items.enum.map((v) => ({ value: v, label: v }))
    if (spec.enum) return spec.enum.map((v, i) => ({ value: v, label: spec.enumNames?.[i] || v }))
    return (spec.oneOf || spec.items?.anyOf || spec.items?.oneOf || [])
        .map((o) => ({ value: o.const, label: o.title || o.const }))
}

// One entry per schema property. ``options`` is precomputed for select/
// multiselect kinds so the template and the submit mapping share the exact
// same (index-stable) list.
const fields = computed(() =>
    Object.entries(schema.value.properties || {}).map(([name, spec]) => {
        const kind = classify(spec)
        return {
            name,
            spec,
            kind,
            label: spec.title || name,
            required: requiredSet.value.has(name),
            options: kind === 'select' || kind === 'multiselect' ? optionsFor(spec) : null,
        }
    }))

// Maps a string format to an <input> type. Anything unrecognised falls back
// to plain text.
function inputTypeFor(spec) {
    switch (spec.format) {
        case 'email': return 'email'
        case 'uri': return 'url'
        case 'date': return 'date'
        case 'date-time': return 'datetime-local'
        default: return 'text'
    }
}

// Per-field reactive store, keyed by property name. Shapes by kind:
// boolean → Boolean; number/text → String (raw control value); select → the
// String INDEX into field.options (wa-option values can't safely hold
// arbitrary strings with spaces, so we index and map back on read);
// multiselect → Array of real option values.
const values = ref({})

function resetState() {
    const initial = {}
    for (const field of fields.value) {
        if (field.kind === 'boolean') {
            initial[field.name] = field.spec.default === true
        } else if (field.kind === 'number') {
            initial[field.name] = field.spec.default !== undefined ? String(field.spec.default) : ''
        } else if (field.kind === 'select') {
            const idx = field.options.findIndex((o) => o.value === field.spec.default)
            initial[field.name] = idx >= 0 ? String(idx) : ''
        } else if (field.kind === 'multiselect') {
            initial[field.name] = Array.isArray(field.spec.default) ? [...field.spec.default] : []
        } else if (field.kind === 'text') {
            initial[field.name] = field.spec.default !== undefined && field.spec.default !== null
                ? String(field.spec.default) : ''
        }
        // unsupported: no state — never read, never written.
    }
    values.value = initial
}
resetState()

function toggleMultiselect(name, optionValue, checked) {
    const arr = values.value[name] || []
    values.value[name] = checked
        ? (arr.includes(optionValue) ? arr : [...arr, optionValue])
        : arr.filter((v) => v !== optionValue)
}

// Template ref + focus for the primary action (Submit), same
// canStealFocus()-gated pattern as the sibling bodies. A Submit still gated
// by unmet required fields is a native disabled button and won't actually
// receive focus — that's fine, the outline class just marks intent.
const submitButtonRef = ref(null)
function focusSubmit() {
    nextTick(() => {
        if (!canStealFocus()) return
        submitButtonRef.value?.focus()
    })
}
onMounted(focusSubmit)

// Reset per-field state and re-focus when a new request takes over the slot.
watch(() => props.pendingRequest?.request_id, () => {
    resetState()
    focusSubmit()
})

// Per-field validity. Booleans and unsupported fields never block submit.
function fieldValid(field) {
    const raw = values.value[field.name]
    switch (field.kind) {
        case 'boolean':
            return true
        case 'number': {
            if (raw === '' || raw === undefined || raw === null) return !field.required
            const num = Number(raw)
            if (!Number.isFinite(num)) return false
            if (field.spec.minimum !== undefined && num < field.spec.minimum) return false
            if (field.spec.maximum !== undefined && num > field.spec.maximum) return false
            return true
        }
        case 'text': {
            const text = (raw || '').toString()
            if (!text) return !field.required
            if (field.spec.minLength !== undefined && text.length < field.spec.minLength) return false
            if (field.spec.maxLength !== undefined && text.length > field.spec.maxLength) return false
            return true
        }
        case 'select':
            return !field.required || (raw !== '' && raw !== undefined && raw !== null)
        case 'multiselect': {
            const arr = Array.isArray(raw) ? raw : []
            if (arr.length === 0) return !field.required
            const { minItems, maxItems } = field.spec
            if (minItems !== undefined && arr.length < minItems) return false
            if (maxItems !== undefined && arr.length > maxItems) return false
            return true
        }
        case 'unsupported':
        default:
            return true
    }
}

const canSubmit = computed(() => fields.value.every(fieldValid))

function collectContent() {
    const content = {}
    for (const field of fields.value) {
        const raw = values.value[field.name]
        if (field.kind === 'unsupported') continue
        if (field.kind === 'boolean') {
            // A checkbox always has a definite state — always include it.
            content[field.name] = Boolean(raw)
            continue
        }
        if (field.kind === 'select') {
            if (raw === '' || raw === undefined || raw === null) continue
            const opt = field.options[Number(raw)]
            if (opt) content[field.name] = opt.value
            continue
        }
        if (field.kind === 'multiselect') {
            const arr = Array.isArray(raw) ? raw : []
            if (arr.length) content[field.name] = arr
            continue
        }
        if (raw === undefined || raw === null || raw === '') continue
        if (field.kind === 'number') {
            const num = field.spec.type === 'integer' ? parseInt(raw, 10) : Number(raw)
            if (Number.isFinite(num)) content[field.name] = num
        } else {
            content[field.name] = raw
        }
    }
    return content
}

function submit() {
    if (props.isResponding || !canSubmit.value) return
    emit('submit', { tool_name: 'elicitationForm', action: 'accept', content: collectContent() })
}
function decline() {
    if (props.isResponding) return
    emit('submit', { tool_name: 'elicitationForm', action: 'decline' })
}
function cancel() {
    if (props.isResponding) return
    emit('submit', { tool_name: 'elicitationForm', action: 'cancel' })
}
</script>

<template>
    <div class="codex-pending-body">
        <div class="codex-pending-section">
            <div class="codex-pending-summary">
                <span class="codex-summary-label">MCP form</span>
                <wa-badge variant="neutral">{{ serverName }}</wa-badge>
            </div>
            <div v-if="message" class="codex-pending-reason">
                <span>{{ message }}</span>
            </div>
        </div>

        <div v-if="fields.length" class="codex-elicit-fields">
            <div v-for="field in fields" :key="field.name" class="codex-elicit-field">
                <template v-if="field.kind === 'boolean'">
                    <wa-checkbox
                        :checked="values[field.name]"
                        :disabled="isResponding"
                        @change="values[field.name] = $event.target.checked"
                    >
                        {{ field.label }}<span v-if="field.required" class="codex-elicit-required" aria-hidden="true"> *</span>
                    </wa-checkbox>
                </template>
                <template v-else>
                    <label class="codex-elicit-label">
                        {{ field.label }}<span v-if="field.required" class="codex-elicit-required" aria-hidden="true"> *</span>
                    </label>

                    <wa-input
                        v-if="field.kind === 'text'"
                        size="small"
                        :type="inputTypeFor(field.spec)"
                        :minlength="field.spec.minLength"
                        :maxlength="field.spec.maxLength"
                        :value="values[field.name]"
                        :disabled="isResponding"
                        @input="values[field.name] = $event.target.value"
                    ></wa-input>

                    <wa-input
                        v-else-if="field.kind === 'number'"
                        size="small"
                        type="number"
                        :min="field.spec.minimum"
                        :max="field.spec.maximum"
                        :value="values[field.name]"
                        :disabled="isResponding"
                        @input="values[field.name] = $event.target.value"
                    ></wa-input>

                    <wa-select
                        v-else-if="field.kind === 'select'"
                        size="small"
                        :value="values[field.name]"
                        :disabled="isResponding"
                        @change="values[field.name] = $event.target.value"
                    >
                        <wa-option v-for="(opt, idx) in field.options" :key="idx" :value="String(idx)">{{ opt.label }}</wa-option>
                    </wa-select>

                    <div v-else-if="field.kind === 'multiselect'" class="codex-elicit-multiselect">
                        <wa-checkbox
                            v-for="(opt, idx) in field.options"
                            :key="idx"
                            :checked="values[field.name]?.includes(opt.value)"
                            :disabled="isResponding"
                            @change="toggleMultiselect(field.name, opt.value, $event.target.checked)"
                        >{{ opt.label }}</wa-checkbox>
                    </div>

                    <p v-else-if="field.kind === 'unsupported'" class="codex-elicit-unsupported">
                        Unsupported field type for "{{ field.name }}".
                    </p>
                </template>

                <p v-if="field.spec.description" class="codex-elicit-help">{{ field.spec.description }}</p>
            </div>
        </div>

        <div class="codex-pending-actions">
            <wa-button
                :id="cancelButtonId"
                variant="neutral"
                appearance="outlined"
                size="small"
                :disabled="isResponding"
                @click="cancel"
            >
                <wa-icon slot="start" name="ban" variant="classic"></wa-icon>
                Cancel
            </wa-button>
            <AppTooltip :for="cancelButtonId">Dismiss the form without answering.</AppTooltip>

            <wa-button
                :id="declineButtonId"
                variant="danger"
                appearance="outlined"
                size="small"
                :disabled="isResponding"
                @click="decline"
            >
                <wa-icon slot="start" name="xmark" variant="classic"></wa-icon>
                Decline
            </wa-button>
            <AppTooltip :for="declineButtonId">Refuse to provide this information.</AppTooltip>

            <wa-button
                :id="submitButtonId"
                ref="submitButtonRef"
                class="auto-focused"
                variant="brand"
                size="small"
                :disabled="isResponding || !canSubmit"
                @click="submit"
            >
                <wa-icon slot="start" name="check" variant="classic"></wa-icon>
                Submit
            </wa-button>
            <AppTooltip :for="submitButtonId">Send the form to the MCP server.</AppTooltip>
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

.codex-pending-summary {
    display: flex;
    align-items: baseline;
    gap: var(--wa-space-s);
    flex-wrap: wrap;
}

.codex-summary-label {
    color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-xs);
    text-transform: uppercase;
    letter-spacing: 0.05em;
    font-weight: 600;
}

.codex-pending-reason {
    display: flex;
    align-items: center;
    gap: var(--wa-space-xs);
    color: var(--wa-color-text);
    font-size: var(--wa-font-size-m);
}

.codex-elicit-fields {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-m);
}

.codex-elicit-field {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-2xs);
}

.codex-elicit-label {
    font-size: var(--wa-font-size-s);
    font-weight: 600;
    color: var(--wa-color-text);
}

.codex-elicit-required {
    color: var(--wa-color-danger-fill-loud);
}

.codex-elicit-help {
    margin: 0;
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-text-quiet);
}

.codex-elicit-multiselect {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-2xs);
}

.codex-elicit-unsupported {
    margin: 0;
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-quiet);
    font-style: italic;
}

.codex-pending-actions {
    display: flex;
    flex-wrap: wrap;
    justify-content: flex-end;
    gap: var(--wa-space-s);
}

/* Always show the focus outline on the Submit button. Default
   :focus-visible would skip mouse and programmatic focus, which hides the
   indicator we want for this primary action.
   We use :focus-within (not :focus) because wa-button delegates focus to an
   inner element in its shadow DOM; the host doesn't carry :focus, but the
   browser keeps :focus-within accurate via activeElement. */
wa-button.auto-focused:focus-within::part(base) {
    outline: var(--wa-focus-ring);
    outline-offset: var(--wa-focus-ring-offset);
}
</style>
