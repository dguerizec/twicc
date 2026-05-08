<script setup>
defineProps({
    description: { type: String, required: true },
    fileIconSrc: { type: String, default: null },
})
</script>

<template>
    <span v-if="fileIconSrc" class="items-details-summary-file">
        <img :src="fileIconSrc" class="items-details-summary-file-icon" loading="lazy" width="16" height="16" />
        <span class="items-details-summary-description">{{ description }}</span>
    </span>
    <span v-else class="items-details-summary-description">{{ description }}</span>
</template>
