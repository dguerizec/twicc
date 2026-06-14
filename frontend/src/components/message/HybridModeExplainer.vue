<script setup>
// Shared "What does hybrid mode do?" explanation, rendered as a wa-details.
// Single source of truth so the toggle dialog (MessageInput) and the Claude
// settings section stay in sync. Pass ``open`` to expand it by default.
defineProps({
    open: {
        type: Boolean,
        default: false,
    },
})
</script>

<template>
    <wa-details :open="open" class="hybrid-mode-explainer">
        <span slot="summary">What does hybrid mode do?</span>
        <p>
            Hybrid mode runs the real interactive Claude Code CLI in a terminal
            embedded in the composer — slash commands, dialogs and all — while
            TwiCC keeps the rich session view, costs, history and notifications.
        </p>
        <p>
            It is a one-way switch: a session resumed by the CLI can never go back
            to the regular SDK mode.
        </p>
    </wa-details>
</template>
