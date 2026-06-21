<script setup>
/**
 * SessionDateSeparator — horizontal rule with a centered "older than" label,
 * inserted into the sidebar session list to mark the bulk-archive thresholds
 * (24 hours, 7 days, …). Same visual treatment as the message list's
 * DaySeparator (line · label · line), but with two sidebar-specific behaviors:
 *
 *  - Responsive label, CSS-only via a container query on `.session-list-container`
 *    (named `session-list`): on a sidebar ≥ 12rem it reads "Older than <duration>";
 *    on a narrower one it collapses to "<duration> +".
 *  - A native tooltip showing the exact cutoff datetime the separator marks.
 */
defineProps({
    // Variable part of the label, e.g. "24 hours", "7 days", "2 months".
    duration: {
        type: String,
        required: true,
    },
    // Tooltip: the exact cutoff datetime this separator marks (YYYY-MM-DD HH:MM:SS).
    title: {
        type: String,
        default: null,
    },
})
</script>

<template>
    <div
        class="session-date-separator"
        role="separator"
        :aria-label="`Older than ${duration}`"
        :title="title"
    >
        <span class="sds-line"></span>
        <span class="sds-label"><span class="sds-prefix">Older than </span>{{ duration }}<span class="sds-suffix"> +</span></span>
        <span class="sds-line"></span>
    </div>
</template>

<style scoped>
.session-date-separator {
    display: flex;
    align-items: center;
    gap: var(--wa-space-m);
    margin: var(--wa-space-xs) var(--wa-space-m);
    user-select: none;
}

.sds-line {
    flex: 1;
    height: 0;
    border-top: var(--wa-border-width-s) solid var(--wa-color-surface-border);
}

.sds-label {
    flex: 0 0 auto;
    font-size: var(--wa-font-size-xs);
    font-weight: var(--wa-font-weight-semibold);
    color: var(--wa-color-text-quiet);
    white-space: nowrap;
}

/* Default (narrow sidebar, < 12rem): compact "<duration> +". */
.sds-prefix { display: none; }
.sds-suffix { display: inline; }

/* Wide enough sidebar: spell out "Older than <duration>". */
@container session-list (min-width: 12rem) {
    .sds-prefix { display: inline; }
    .sds-suffix { display: none; }
}
</style>
