<script setup>
defineProps({
    query: { type: String, required: true },
})
</script>

<template>
    <span class="items-details-summary-description">{{ query }}</span>
</template>
