<script setup>
import { showHelp } from '../help/showHelp'

function openHelp() {
    showHelp('peers', { showDontShowAgain: false })
}
</script>

<template>
    <a class="peer-help-link" href="#" @click.prevent="openHelp">
        <wa-icon name="circle-question"></wa-icon>
        <span>What is Peer messaging?</span>
    </a>
</template>

<style scoped>
.peer-help-link {
    display: inline-flex;
    align-items: center;
    gap: var(--wa-space-2xs);
    color: var(--wa-color-brand-60);
    font-size: var(--wa-font-size-xs);
    font-weight: var(--wa-font-weight-normal);
    line-height: var(--wa-line-height-normal);
    text-decoration: none;
}

.peer-help-link:hover,
.peer-help-link:focus-visible {
    text-decoration: underline;
    outline: none;
}
</style>
