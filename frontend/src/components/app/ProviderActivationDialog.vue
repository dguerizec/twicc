<script setup>
import { ref, computed, watch } from 'vue'
import { useSettingsStore } from '../../stores/settings'
import { getRegisteredProviders, getProviderLabel, getProviderIcon } from '../../providers'

const settings = useSettingsStore()

const choices = ref({})

// Initialise choices from the current store state: a provider is checked
// if it's NOT in disabledProviders.
function syncChoicesFromStore() {
    const disabled = new Set(settings.disabledProviders || [])
    const next = {}
    for (const p of getRegisteredProviders()) {
        next[p] = !disabled.has(p)
    }
    choices.value = next
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

function providerLabel(p) {
    return getProviderLabel(p) ?? p
}

function save() {
    if (!atLeastOneChecked.value) return
    const disabled = getRegisteredProviders().filter(p => !choices.value[p])
    // Mutating disabledProviders on the store triggers the synced-settings
    // watcher which sends update_synced_settings to the back. The back will
    // broadcast it back via synced_settings_updated; the watcher in the
    // store then flips disabledProvidersPresent to true, closing the dialog.
    settings.disabledProviders = disabled
}

// Conditional close: prevent user-initiated close (Esc) when `open` is still
// true; allow programmatic close (`:open` flipping to false after Save) so
// the dialog actually disappears. preventDefault on `wa-hide` blocks ALL
// close paths, including the one triggered by `open` becoming false — hence
// the `open.value` check.
function handleHide(event) {
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
        <wa-button
            slot="footer"
            variant="brand"
            :disabled="!atLeastOneChecked"
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
</style>
