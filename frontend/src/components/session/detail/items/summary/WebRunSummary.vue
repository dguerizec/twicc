<script setup>
defineProps({
    items: { type: Array, required: true },
})

function linkTarget(item) {
    if (typeof item !== 'string') return null
    return /^(https?:\/\/[^\s·]+)(?:\s*·|$)/i.exec(item)?.[1] ?? null
}
</script>

<template>
    <span class="items-details-summary-description web-run-summary">
        <template v-for="(item, index) in items" :key="`${index}:${item}`">
            <a
                v-if="linkTarget(item)"
                :href="linkTarget(item)"
                target="_blank"
                rel="noopener noreferrer nofollow"
                @click.stop
            >{{ item }}</a>
            <span v-else>{{ item }}</span>
        </template>
    </span>
</template>

<style scoped>
.web-run-summary {
    display: inline-flex;
    flex-direction: column;
    min-width: 0;
}

.web-run-summary a {
    color: inherit;
    text-decoration: none;
}

.web-run-summary a:hover {
    text-decoration: underline;
}
</style>
