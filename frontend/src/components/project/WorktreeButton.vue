<script setup>
// WorktreeButton.vue - Compact "session in a worktree" button for the project
// rows of the "New session" dropdowns. Opens the WorktreeDialog, where the
// user either creates a new worktree or picks an existing one. Same structure
// and styling as the "…" actions button of the project selector rows
// (ProjectSelectorRow.vue): a plain wa-button inside a near-full-height overlay
// pinned to the right of the row. The click is stopped so the row's own
// selection (create a session in that project) does not fire.
const emit = defineEmits(['create'])

function onClick(e) {
    e.stopPropagation()
    emit('create')
}
</script>

<template>
    <span class="worktree-button-overlay" @click.stop>
        <wa-button
            appearance="plain"
            size="small"
            class="worktree-button-trigger"
            title="New session in a worktree"
            @click="onClick"
        >
            <wa-icon name="code-branch" label="Worktree"></wa-icon>
            <wa-icon name="plus"></wa-icon>
        </wa-button>
    </span>
</template>

<style scoped>
/* Near-full-height overlay pinned to the right of the row — same treatment as
   the "…" actions button of the project selector rows. The wa-dropdown-item
   host is position:relative and adds vertical padding; without the overlay
   that padding band would be dead space selecting the row instead of the
   button. inset-block leaves a 1px gap top/bottom; inset-inline-end keeps the
   button clear of the dropdown edge. The hosting rows reserve matching right
   padding (see .project-picker-row in ProjectView.vue). */
.worktree-button-overlay {
    position: absolute;
    inset-block: 1px;
    inset-inline-end: 0.5em;
    display: flex;
    align-items: stretch;
    justify-content: center;
    width: 3rem;
}

.worktree-button-trigger {
    opacity: 0.55;
    font-size: var(--wa-font-size-s);
    transition: opacity 0.12s ease;
    width: 100%;
}

.worktree-button-trigger::part(base) {
    padding: 0;
    min-height: 0;
    width: 100%;
    /* Fill the full-height overlay so the entire band opens the dialog. */
    height: 100%;
}

.worktree-button-trigger::part(label) {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 3px;
}

/* Neutralize the icon spacing wa-button/Font Awesome forces on slotted icons
   (margin-inline-end on the first, margin-inline-start on the last); the gap
   above is the only spacing between the two icons. */
.worktree-button-trigger wa-icon {
    margin-inline: 0;
}

wa-dropdown-item:hover .worktree-button-trigger,
.worktree-button-overlay:focus-within .worktree-button-trigger {
    opacity: 1;
}
</style>
