<script setup>
// Shared hybrid-mode explainer, rendered as a wa-details. Single source of truth
// so the toggle dialog (MessageInput) and the Claude settings section stay in
// sync. Pass ``open`` to expand it by default.
//
// Each sentence leads with a bold inline mini-title so the reader can scan the
// bold lead-ins to find the line they need; UI references (the Claude CLI
// terminal, the Settings path) stay plain to keep one bold anchor per line.
import { computed } from 'vue'
import { useSettingsStore } from '../../stores/settings'

defineProps({
    open: {
        type: Boolean,
        default: false,
    },
})

// First-time readers get the persuasive "why you need it" summary; once they've
// seen it (the synced flag flips after the dialog is acknowledged), it reads as
// plain reference — "what is hybrid mode".
const settingsStore = useSettingsStore()
const explainerSeen = computed(() => settingsStore.isClaudeHybridExplainerSeen)
</script>

<template>
    <wa-details :open="open" class="hybrid-mode-explainer">
        <span v-if="!explainerSeen" slot="summary">Why you may <strong><em>need</em></strong> this in the future</span>
        <span v-else slot="summary">What is hybrid mode</span>

        <wa-callout variant="neutral" size="small" class="explainer-maintenance-note">
            <wa-icon slot="icon" name="screwdriver-wrench" variant="classic"></wa-icon>
            <strong>Internal reminder</strong> — the copy below was written for Anthropic's
            programmatic-billing change announced for 15 June 2026, which has since been
            postponed. Hybrid mode is kept dormant behind the
            <code>TWICC_CLAUDE_HYBRID_ENABLED</code> flag. Revisit this wording and its
            source link before the feature is un-gated.
        </wa-callout>

        <wa-callout variant="warning" size="small">
            <wa-icon slot="icon" name="triangle-exclamation" variant="classic"></wa-icon>
            <strong>Worth a minute</strong> — hybrid mode changes what your Claude usage costs.
        </wa-callout>

        <p class="explainer-section-title">The change</p>

        <p>
            <strong>What changed —</strong> from
            <a href="https://x.com/ClaudeDevs/status/2054610152817619388" target="_blank" rel="noopener">15 June 2026</a>,
            Anthropic bills programmatic usage — the SDK TwiCC normally runs — at
            full API rates, from a small monthly pool.
        </p>

        <p>
            <strong>Stay on your plan —</strong> hybrid runs the real CLI instead,
            which still uses your Pro/Max quota: several times more usage for the
            same money.
        </p>

        <p class="explainer-section-title">In TwiCC</p>

        <p>
            <strong>Real CLI inside —</strong> the CLI runs in a terminal above
            the message input, opened on demand.
        </p>

        <p>
            <strong>Same input —</strong> you still type as usual: slash commands,
            attachments and snippets all work like normal mode.
        </p>

        <p>
            <strong>Nothing lost —</strong> the rich session view, approvals, the
            question widget, notifications — everything.
        </p>

        <p>
            <strong>No live streaming —</strong> the message list shows
            <em>Claude is thinking…</em>, then the finished reply and tools — not
            live; open the Claude CLI terminal to watch it stream.
        </p>

        <p>
            <strong>Mostly hands-off —</strong> TwiCC drives the CLI for you; the
            rare time it needs you on a prompt only it can show, open the Claude
            CLI terminal to answer.
        </p>

        <p>
            <strong>No orchestration —</strong> that's also why hybrid sessions
            can't be orchestrated: no human to step in. To opt Claude out
            entirely, turn off Enabled in orchestration under Settings → Claude.
        </p>

        <p class="explainer-section-title">Turning it on</p>

        <p>
            <strong>Turn it on —</strong> click the
            <wa-icon class="inline-terminal-icon" name="terminal" variant="classic"></wa-icon>
            icon at the bottom-right of the message input, or default new sessions
            under Settings → Claude → Hybrid Mode.
        </p>

        <p>
            <strong>One-way —</strong> a hybrid session can never go back to
            normal mode.
        </p>
    </wa-details>
</template>

<style scoped>
.hybrid-mode-explainer p {
    margin: 0 0 var(--wa-space-s) 0;
    line-height: 1.5;
}

/* Section labels between the groups of sentences. Scoped with two classes so
   they beat ``.hybrid-mode-explainer p``. */
.hybrid-mode-explainer .explainer-section-title {
    margin: var(--wa-space-l) 0 var(--wa-space-2xs);
    font-size: var(--wa-font-size-l);
    font-weight: var(--wa-font-weight-bold);
    color: var(--wa-color-text-quiet);
}

.inline-terminal-icon {
    vertical-align: -0.1em;
    color: var(--wa-color-brand-fill-loud);
}
</style>
