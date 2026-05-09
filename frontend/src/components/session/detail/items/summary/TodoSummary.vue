<script setup>
defineProps({
    parts: { type: Array, required: true },
})
</script>

<template>
    <template v-for="(part, i) in parts" :key="i">
        <span v-if="i > 0" class="items-details-summary-separator"> — </span>
        <span class="items-details-summary-description" :class="{ 'no-wrap': !part.status }">{{ part.text }}<wa-icon v-if="part.status === 'completed'" name="check" class="todo-icon todo-icon-completed"></wa-icon></span>
    </template>
</template>

<style scoped>
.todo-icon {
    margin-left: var(--wa-space-xs);
    font-size: 0.85em;
    vertical-align: baseline;
}
.todo-icon-completed {
    color: var(--wa-color-success-60);
}
</style>
