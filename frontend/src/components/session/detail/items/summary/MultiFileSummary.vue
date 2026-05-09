<script setup>
/**
 * Summary description for tools that touch several files in a single
 * call (Codex's ``apply_patch`` is the only one today). Renders each
 * file as ``<icon> <path>``, separated by commas, in document flow so
 * lines wrap naturally without truncation.
 *
 * Props:
 *   - ``files``: Array<{ path, fileIconSrc?: string|null }> — each
 *     ``path`` is already relative to the session base dir when
 *     possible (the helper does the conversion).
 */

defineProps({
    files: { type: Array, required: true },
})
</script>

<template>
    <span class="multi-file-summary">
        <template v-for="(file, idx) in files" :key="file.path">
            <span class="multi-file-summary-entry">
                <img
                    v-if="file.fileIconSrc"
                    :src="file.fileIconSrc"
                    class="items-details-summary-file-icon"
                    alt=""
                />
                <span class="items-details-summary-description">{{ file.path }}</span>
            </span>
            <span v-if="idx < files.length - 1" class="multi-file-summary-separator">,&nbsp;</span>
        </template>
    </span>
</template>

<style scoped>
.multi-file-summary {
    display: inline;
}

.multi-file-summary-entry {
    display: inline-flex;
    align-items: center;
    gap: var(--wa-space-2xs);
}

.multi-file-summary-separator {
    color: var(--wa-color-text-quiet);
}
</style>
