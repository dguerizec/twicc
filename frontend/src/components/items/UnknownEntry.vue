<script setup>
import { ref } from 'vue'
import JsonHumanView from '../JsonHumanView.vue'

defineProps({
    type: {
        type: String,
        default: 'unknown'
    },
    data: {
        type: Object,
        default: null
    }
})

// Lazy rendering: content is only mounted when wa-details is open
const isOpen = ref(false)
</script>

<template>
    <wa-details class="item-details unknown-entry" icon-placement="start" @wa-show="isOpen = true" @wa-hide="isOpen = false">
        <span slot="summary" class="items-details-summary">
            <strong class="items-details-summary-name">Unhandled event</strong>
            <span class="items-details-summary-separator"> — </span>
            <span class="items-details-summary-description">{{ type }}</span>
        </span>
        <template v-if="isOpen">
            <div v-if="data" class="unknown-data">
                <JsonHumanView
                    :value="data"
                />
            </div>
            <div v-else class="unknown-no-data">
                No data available
            </div>
        </template>
    </wa-details>
</template>

<style scoped>
wa-details::part(content) {
    padding-top: 0;
}

.unknown-data {
    padding: var(--wa-space-xs) 0;
    overflow-x: auto;
}

.unknown-no-data {
    color: var(--wa-color-text-quiet);
    font-style: italic;
    padding: var(--wa-space-xs) 0;
}
</style>
