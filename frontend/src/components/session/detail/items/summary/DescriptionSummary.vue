<script setup>
defineProps({
    description: { type: String, required: true },
    fileIconSrc: { type: String, default: null },
    // Force the description into a single ellipsis-truncated line.
    // Used by Codex's ``Exec`` summary where the input is a raw shell
    // script: we want to show only the first line. A single line that
    // overflows the row width collapses to ``…`` via CSS; a multi-line
    // script gets an explicit ``…`` appended to its first line by the
    // caller before being passed in.
    truncate: { type: Boolean, default: false },
})
</script>

<template>
    <span v-if="fileIconSrc" class="items-details-summary-file" :class="{ 'truncate-summary-wrapper': truncate }">
        <img :src="fileIconSrc" class="items-details-summary-file-icon" loading="lazy" width="16" height="16" />
        <span class="items-details-summary-description" :class="{ 'truncate-summary': truncate }">{{ description }}</span>
    </span>
    <span v-else class="items-details-summary-description" :class="{ 'truncate-summary': truncate }">{{ description }}</span>
</template>
