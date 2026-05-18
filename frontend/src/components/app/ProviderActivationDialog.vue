<script setup>
import { ref, computed, watch } from 'vue'
import { useSettingsStore } from '../../stores/settings'
import { getRegisteredProviders, getProviderLabel, getProviderIcon, getProviderOptions } from '../../providers'

const settings = useSettingsStore()

const choices = ref({})
const defaultChoice = ref('')

// Initialise choices from the current store state: a provider is checked
// if it's NOT in disabledProviders. The default-provider field always starts
// empty — the user must pick one explicitly. The watcher on
// enabledProviderOptions will auto-fill it if only one provider remains.
function syncChoicesFromStore() {
    const disabled = new Set(settings.disabledProviders || [])
    const next = {}
    for (const p of getRegisteredProviders()) {
        next[p] = !disabled.has(p)
    }
    choices.value = next
    defaultChoice.value = ''
}

// Open when the key is absent, OR when every registered provider is disabled
// (corrupted state — recovery via the dialog).
const open = computed(() =>
    !settings.disabledProvidersPresent ||
    (settings.disabledProviders || []).length >= getRegisteredProviders().length
)
watch(open, (now) => { if (now) syncChoicesFromStore() }, { immediate: true })

const atLeastOneChecked = computed(() =>
    Object.values(choices.value).some(v => v === true)
)

// Options shown in the default-provider select: filtered to currently
// checked providers, in registered order.
const enabledProviderOptions = computed(() =>
    getProviderOptions().filter(opt => choices.value[opt.value] === true)
)

// Reactive policy for defaultChoice as switches toggle:
//   - Exactly one provider enabled → auto-select it (no real choice to make).
//   - Otherwise → keep the manual choice if it's still enabled, else clear
//     it so the user must pick again (and Save stays disabled).
watch(enabledProviderOptions, (next) => {
    if (next.length === 1) {
        defaultChoice.value = next[0].value
        return
    }
    if (defaultChoice.value && !next.some(opt => opt.value === defaultChoice.value)) {
        defaultChoice.value = ''
    }
}, { immediate: true })

const canSave = computed(() => {
    if (!atLeastOneChecked.value) return false
    if (!defaultChoice.value) return false
    return enabledProviderOptions.value.some(opt => opt.value === defaultChoice.value)
})

function providerLabel(p) {
    return getProviderLabel(p) ?? p
}

function save() {
    if (!canSave.value) return
    const disabled = getRegisteredProviders().filter(p => !choices.value[p])
    // Mutating both synced settings in the same tick: the synced-settings
    // watcher batches them into a single update_synced_settings WS message.
    // The back broadcasts synced_settings_updated, the store flips
    // disabledProvidersPresent to true, and the dialog closes.
    settings.disabledProviders = disabled
    settings.defaultProvider = defaultChoice.value
}

// Conditional close: prevent user-initiated close (Esc) when `open` is still
// true; allow programmatic close (`:open` flipping to false after Save) so
// the dialog actually disappears. preventDefault on `wa-hide` blocks ALL
// close paths, including the one triggered by `open` becoming false — hence
// the `open.value` check.
//
// wa-hide also bubbles from child overlays (e.g. wa-select listbox closing
// after picking an option) — without the target check below, the listbox
// would refuse to close, stuck open under the dialog.
function handleHide(event) {
    if (event.target !== event.currentTarget) return
    if (open.value) event.preventDefault()
}
</script>

<template>
    <wa-dialog
        :open="open"
        without-header
        @wa-hide="handleHide"
    >
        <h2 class="dialog-title">Choose your providers</h2>
        <p>
            TwiCC supports multiple AI coding providers. Pick the ones you want
            to enable. You can change this anytime from
            <strong>Settings &rarr; Providers</strong>.
        </p>
        <div class="provider-choices">
            <wa-switch
                v-for="p in getRegisteredProviders()"
                :key="p"
                class="provider-row"
                :checked="choices[p] === true"
                @change="(e) => choices[p] = e.target.checked"
            >
                <wa-icon
                    v-if="getProviderIcon(p)"
                    auto-width
                    family="brands"
                    :name="getProviderIcon(p)"
                    class="provider-icon"
                ></wa-icon>
                {{ providerLabel(p) }}
            </wa-switch>
        </div>
        <div class="default-provider-group">
            <label class="default-provider-label">Default provider for new sessions</label>
            <wa-select
                :value.prop="defaultChoice"
                placeholder="Select a default provider…"
                :disabled="enabledProviderOptions.length === 0"
                @change="(e) => defaultChoice = e.target.value"
            >
                <wa-icon
                    v-if="defaultChoice && getProviderIcon(defaultChoice)"
                    slot="start"
                    auto-width
                    family="brands"
                    :name="getProviderIcon(defaultChoice)"
                ></wa-icon>
                <wa-option
                    v-for="option in enabledProviderOptions"
                    :key="option.value"
                    :value="option.value"
                    :label="option.label"
                >
                    <wa-icon
                        v-if="getProviderIcon(option.value)"
                        auto-width
                        family="brands"
                        :name="getProviderIcon(option.value)"
                        class="provider-option-icon"
                    ></wa-icon>
                    {{ option.label }}
                </wa-option>
            </wa-select>
        </div>
        <wa-button
            slot="footer"
            variant="brand"
            :disabled="!canSave"
            @click="save"
        >
            Save
        </wa-button>
    </wa-dialog>
</template>

<style scoped>
.dialog-title {
    margin: 0 0 var(--wa-space-s);
    font-size: var(--wa-font-size-l);
}
.provider-choices {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-m);
    margin: var(--wa-space-m) 0;
}
.provider-row::part(label) {
    display: inline-flex;
    align-items: center;
    gap: var(--wa-space-xs);
}
.provider-icon {
    font-size: 1.2em;
}
.default-provider-group {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-xs);
    margin-top: var(--wa-space-l);
}
.default-provider-label {
    font-weight: var(--wa-font-weight-semibold);
    font-size: var(--wa-font-size-s);
}
.provider-option-icon {
    margin-right: var(--wa-space-2xs);
}
</style>
