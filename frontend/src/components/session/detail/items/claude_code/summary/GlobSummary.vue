<script setup>
defineProps({
    pattern: { type: String, required: true },
})
</script>

<template>
    <span class="items-details-summary-description glob-summary"><code>{{ pattern }}</code></span>
</template>

<style scoped>
.glob-summary code {
    font-size: 1em;
    background: var(--wa-color-neutral-fill-quiet);
    border-radius: var(--wa-border-radius-s);
}
</style>
