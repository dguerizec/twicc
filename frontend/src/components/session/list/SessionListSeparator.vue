<script setup>
/**
 * SessionListSeparator — horizontal rule with a centered label, inserted before
 * a group of sessions in the sidebar list (Selected, Pinned, Active, "Last 24
 * hours", "Older than <X>", …). Same visual treatment as the message list's
 * DaySeparator (line · label · line).
 *
 * Two label modes:
 *  - Plain: just `label` (e.g. "Pinned", "Active", "Last 24 hours").
 *  - Threshold (when `prefix` is set, e.g. "Older than "): responsive, CSS-only
 *    via a container query on `.session-list-container` (named `session-list`).
 *    On a sidebar ≥ 12rem it reads "<prefix><label>" (e.g. "Older than 7 days");
 *    on a narrower one it collapses to "<label> +" (e.g. "7 days +").
 *
 * `title` is shown as a native tooltip (used for the exact cutoff datetime of
 * the "older than" thresholds).
 */
defineProps({
    // Main label text, e.g. "Pinned", "Last 24 hours", or the duration "7 days".
    label: {
        type: String,
        required: true,
    },
    // Optional leading text for threshold separators, e.g. "Older than ". When
    // set, the label becomes responsive (collapses to "<label> +" when narrow).
    prefix: {
        type: String,
        default: null,
    },
    // Optional native tooltip (e.g. the exact cutoff datetime).
    title: {
        type: String,
        default: null,
    },
})
</script>

<template>
    <div
        class="session-list-separator"
        role="separator"
        :aria-label="prefix ? `${prefix}${label}` : label"
        :title="title"
    >
        <span class="sls-line"></span>
        <span class="sls-label"><span v-if="prefix" class="sls-prefix">{{ prefix }}</span>{{ label }}<span v-if="prefix" class="sls-suffix"> +</span></span>
        <span class="sls-line"></span>
    </div>
</template>

<style scoped>
.session-list-separator {
    display: flex;
    align-items: center;
    gap: var(--wa-space-m);
    margin: var(--wa-space-xs) var(--wa-space-m);
    user-select: none;
}

.sls-line {
    flex: 1;
    height: 0;
    border-top: var(--wa-border-width-s) solid var(--wa-color-surface-border);
}

.sls-label {
    flex: 0 0 auto;
    font-size: var(--wa-font-size-xs);
    font-weight: var(--wa-font-weight-semibold);
    color: var(--wa-color-text-quiet);
    white-space: nowrap;
}

/* Threshold labels (with a prefix): default to the compact "<label> +" form,
   expand to "<prefix><label>" once the sidebar is wide enough. */
.sls-prefix { display: none; }
.sls-suffix { display: inline; }

@container session-list (min-width: 12rem) {
    .sls-prefix { display: inline; }
    .sls-suffix { display: none; }
}
</style>
