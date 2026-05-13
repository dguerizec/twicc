<script setup>
defineProps({
    url: { type: String, required: true },
})
</script>

<template>
    <a :href="url" target="_blank" rel="noopener noreferrer nofollow" class="items-details-summary-description webfetch-link" @click.stop>{{ url }}<wa-icon name="arrow-up-right-from-square" class="webfetch-link-icon"></wa-icon></a>
</template>

<style scoped>
.webfetch-link {
    color: inherit;
    text-decoration: none;
    &:hover {
        text-decoration: underline;
    }
}
.webfetch-link-icon {
    font-size: var(--wa-font-size-2xs);
    margin-left: var(--wa-space-3xs);
    opacity: 0.6;
}
</style>
