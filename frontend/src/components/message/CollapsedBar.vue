<script setup>
// Shared single-line "collapsed" bar used by both the message input and the
// pending request form when they are reduced to a header. Clicking anywhere on
// the bar expands it (the chevron button is the explicit visual cue); the icon
// and label describe what is waiting inside. Keeping this in one component
// guarantees the two reduced panels render and hover identically.
import { useId } from 'vue'
import AppTooltip from '../ui/AppTooltip.vue'

defineProps({
    // Leading icon name (Font Awesome, resolved by wa-icon).
    icon: { type: String, required: true },
    // Single-line label; truncated with an ellipsis when too long.
    label: { type: String, default: '' },
    // Tooltip on the expand (chevron) button.
    expandTooltip: { type: String, default: 'Expand' },
    // Add left padding so the content clears the floating sidebar-toggle button
    // that overlaps the bottom-left of the chat surface (mobile always; desktop
    // when the sidebar is collapsed). Only the bottom-most bar needs this.
    sidebarToggleClearance: { type: Boolean, default: false },
})
const emit = defineEmits(['expand'])
const restoreButtonId = useId()
</script>

<template>
    <div
        class="collapsed-bar"
        :class="{ 'collapsed-bar--sidebar-clearance': sidebarToggleClearance }"
        @click="emit('expand')"
    >
        <wa-icon :name="icon" class="collapsed-bar-icon"></wa-icon>
        <span class="collapsed-bar-label">{{ label }}</span>
        <!-- Optional trailing content (e.g. a count badge) before the chevron. -->
        <slot name="trailing" />
        <wa-button
            variant="neutral"
            appearance="outlined"
            size="small"
            class="collapsed-bar-restore-btn"
            :id="restoreButtonId"
            @click.stop="emit('expand')"
        >
            <wa-icon name="chevron-up"></wa-icon>
        </wa-button>
        <AppTooltip :for="restoreButtonId">{{ expandTooltip }}</AppTooltip>
    </div>
</template>

<style scoped>
.collapsed-bar {
    display: flex;
    align-items: center;
    gap: var(--wa-space-xs);
    padding: var(--wa-space-xs);
    border-radius: var(--wa-border-radius-m);
    color: var(--wa-color-text-quiet);
    cursor: pointer;
}
.collapsed-bar:hover {
    background: var(--wa-color-neutral-fill-quiet);
}
/* On mobile the sidebar toggle button always overlaps the bottom-left of the
   chat surface; mirror the toolbar's offset so the label clears it. */
.collapsed-bar--sidebar-clearance {
    @media (width < 640px) {
        padding-block: var(--wa-space-s);
        padding-left: 4rem;
    }
}
/* When the sidebar is collapsed, the toggle overlaps the bottom-left on desktop too — same as
   .message-input-toolbar, reading the centralized --sidebar-toggle-clearance-x but sitting 0.5rem
   further in (so full=4rem, partial=2rem). */
body.sidebar-closed .collapsed-bar--sidebar-clearance {
    @media (width >= 640px) {
        padding-block: var(--wa-space-s);
        padding-left: calc(var(--sidebar-toggle-clearance-x) + 0.5rem);
    }
}
/* When a bottom dock/gutter lifts the bar above the toggle, or a left column pushes it clear, the
   bar needs no clearance and reverts to its compact base padding. */
body.sidebar-closed :is(.session-layout.has-bottom-region, .session-layout.has-bottom-gutter, .session-layout.has-left-col) .collapsed-bar--sidebar-clearance {
    @media (width >= 640px) {
        padding-block: var(--wa-space-xs);
        padding-left: var(--wa-space-xs);
    }
}
.collapsed-bar-icon {
    flex-shrink: 0;
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-warning-60);
}
.collapsed-bar-label {
    flex: 1;
    min-width: 0;
    font-size: var(--wa-font-size-s);
    font-weight: var(--wa-font-weight-bold);
    color: var(--wa-color-warning-60);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
}
.collapsed-bar-restore-btn {
    flex-shrink: 0;
}
</style>
