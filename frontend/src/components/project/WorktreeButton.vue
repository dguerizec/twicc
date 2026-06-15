<script setup>
// WorktreeButton.vue - Compact "session in a worktree" control for the project
// rows of the "New session" dropdowns. Two variants, picked from the project's
// cached git_root (read reactively from the store — never re-checked per
// render):
//
//   • git_root present  → the worktree button (branch + plus). Opens the
//     WorktreeDialog via the `create` emit, exactly as before.
//   • git_root absent    → a "crossed git + refresh" control. Clicking it
//     re-resolves git_root live on the backend (POST .../resolve-git/). If the
//     directory has since become a git repo (e.g. `git init` after the project
//     was created), the project_updated broadcast — mirrored here optimistically
//     for snappiness — flips this control to the worktree button (the user then
//     clicks it to create). If it is still not a git repo, a toast says so.
//
// This is why the affordance survives a directory that becomes a git repo after
// the project was registered, without a backend restart and without checking on
// every render.
import { computed, ref } from 'vue'
import { useDataStore } from '../../stores/data'
import { apiFetch } from '../../utils/api'
import { toast } from '../../composables/useToast'

const props = defineProps({
    projectId: { type: String, required: true },
})
const emit = defineEmits(['create'])

const data = useDataStore()
const hasGitRoot = computed(() => !!data.getProject(props.projectId)?.git_root)
const refreshing = ref(false)

function onCreate(e) {
    e.stopPropagation()
    emit('create')
}

async function onRefresh(e) {
    e.stopPropagation()
    if (refreshing.value) return
    refreshing.value = true
    try {
        const res = await apiFetch(`/api/projects/${props.projectId}/resolve-git/`, { method: 'POST' })
        if (!res.ok) {
            toast.error('Could not check this folder for a git repository.')
            return
        }
        const { git_root } = await res.json()
        if (git_root) {
            // Mirror the broadcast locally so the control flips to the worktree
            // button immediately (reveal-just: the user clicks it to create).
            data.updateProject({ id: props.projectId, git_root })
        } else {
            toast.info('No git repository found in this folder.')
        }
    } catch {
        toast.error('Could not check this folder for a git repository.')
    } finally {
        refreshing.value = false
    }
}
</script>

<template>
    <span class="worktree-button-overlay" @click.stop>
        <wa-button
            v-if="hasGitRoot"
            appearance="plain"
            size="small"
            class="worktree-button-trigger"
            title="New session in a worktree"
            @click="onCreate"
        >
            <wa-icon name="code-branch" label="Worktree"></wa-icon>
            <wa-icon name="plus"></wa-icon>
        </wa-button>
        <wa-button
            v-else
            appearance="plain"
            size="small"
            class="worktree-button-trigger worktree-button-trigger--nogit"
            title="Not a git repository — re-check (e.g. after git init)"
            :loading="refreshing"
            @click="onRefresh"
        >
            <wa-icon name="code-branch" label="Not a git repository" class="nogit-icon"></wa-icon>
            <wa-icon name="arrows-rotate"></wa-icon>
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

/* The "not a git repo" variant is dimmer still — it is an informational /
   re-check affordance, not a primary action. */
.worktree-button-trigger--nogit {
    opacity: 0.4;
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

/* Diagonal slash over the branch icon for the "not a git repo" variant — Font
   Awesome Free has no crossed-branch glyph, so draw it. The bar uses
   currentColor so it tracks the icon's color/opacity. */
.nogit-icon {
    position: relative;
}
.nogit-icon::after {
    content: '';
    position: absolute;
    left: 50%;
    top: -10%;
    height: 120%;
    width: 1.5px;
    background: currentColor;
    transform: translateX(-50%) rotate(45deg);
    transform-origin: center;
    pointer-events: none;
}

wa-dropdown-item:hover .worktree-button-trigger,
.worktree-button-overlay:focus-within .worktree-button-trigger {
    opacity: 1;
}
</style>
