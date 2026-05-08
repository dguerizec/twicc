<script setup>
defineProps({
    name: { type: String, required: true },
    namespace: { type: String, default: null },
})
</script>

<template>
    <span class="items-details-summary-description">{{ name }}<span v-if="namespace" class="items-details-summary-quiet"> ({{ namespace }})</span></span>
</template>
