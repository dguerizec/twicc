<script setup>
// CostDisplay.vue - Consistent cost display with optional dollar icon + formatted amount.
// Renders: <span>[<wa-icon dollar-sign />] X.XX</span>
// If cost is null/undefined, displays a dash instead.
// The `icon` prop (default: true) controls whether the dollar icon is shown.

import { computed } from 'vue'

const props = defineProps({
    /** Cost value in USD (number or null) */
    cost: {
        type: Number,
        default: null,
    },
    /** Whether to show the dollar icon */
    icon: {
        type: Boolean,
        default: true,
    },
})

/**
 * Format cost as a plain number string without currency symbol (e.g., "0.42").
 * The dollar icon serves as the currency indicator.
 */
const formattedCost = computed(() => {
    if (props.cost == null) return null
    if (props.cost < 0.01) return '0.00'
    return props.cost.toFixed(2)
})
</script>

<template>
    <span class="cost-display">
        <wa-icon v-if="icon" auto-width name="dollar-sign" class="cost-icon"></wa-icon>
        <span v-if="formattedCost != null">{{ formattedCost }}</span>
        <span v-else>-</span>
    </span>
</template>

<style scoped>
/* Inline flow (not inline-flex): lets the dollar wa-icon use its natural
   baseline alignment with the digits — the previous ``inline-flex`` +
   ``align-items: center`` centred the icon on the line-box, which sits above
   the digits' baseline and made the ``$`` look too high (session header,
   session list, orchestration tree, …). */
.cost-display {
    white-space: nowrap;
}

.cost-icon {
    margin-inline-end: var(--wa-space-3xs);
}
</style>
