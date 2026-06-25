<script setup>
// Read-only render of a session's plan markdown (the file the provider's plans
// watcher detects — Claude Code: ~/.claude/plans/<slug>.md). The Plan tab is
// only present when has_plan is true, so this pane assumes a plan exists; it
// fetches the content from the dedicated, path-safe endpoint
// /api/sessions/<id>/plan/ (the server resolves the file, the client only knows
// the session id). Provider-agnostic: nothing here is Claude-Code-specific.
import { ref, watch, onMounted, onBeforeUnmount } from 'vue'
import MarkdownContent from '../ui/MarkdownContent.vue'

const props = defineProps({
    sessionId: { type: String, required: true },
    // True while the Plan tab is the shown tab in its region. Drives a refetch
    // on (re)activation, mirroring the other tool panels.
    active: { type: Boolean, default: false },
})

const content = ref('')
const loading = ref(false)
const error = ref(null)
const hasLoaded = ref(false)

let controller = null

async function load() {
    if (!props.sessionId) return
    // Cancel any in-flight request so a fast tab/session switch can't let an
    // older response overwrite a newer one.
    if (controller) controller.abort()
    controller = new AbortController()
    loading.value = true
    error.value = null
    try {
        const url = `/api/sessions/${encodeURIComponent(props.sessionId)}/plan/`
        const response = await fetch(url, { signal: controller.signal })
        if (!response.ok) {
            // 404 = the plan vanished between the tab showing and this fetch;
            // the plan_gone WS message will drop the tab shortly after.
            throw new Error(`Failed to load plan (${response.status})`)
        }
        const data = await response.json()
        content.value = data.content ?? ''
        hasLoaded.value = true
    } catch (e) {
        if (e.name === 'AbortError') return
        error.value = e.message || 'Failed to load plan'
    } finally {
        loading.value = false
    }
}

onMounted(load)
onBeforeUnmount(() => { if (controller) controller.abort() })

// Reload when switched to (caught a change while hidden) or when the session changes.
watch(() => props.active, (active) => { if (active) load() })
watch(() => props.sessionId, () => { hasLoaded.value = false; load() })

// Live refresh is driven by SessionView (the owner of this session's WS events),
// mirroring how the Artifacts panel is refreshed: it calls reload() on a
// plan_changed for this session and on a WebSocket reconnection.
defineExpose({ reload: load })
</script>

<template>
    <div class="plan-pane">
        <div v-if="error" class="plan-state plan-error">
            <wa-icon name="triangle-exclamation"></wa-icon>
            <span>{{ error }}</span>
        </div>
        <div v-else-if="loading && !hasLoaded" class="plan-state">
            <wa-spinner></wa-spinner>
        </div>
        <div v-else class="plan-scroll">
            <MarkdownContent :source="content" class="plan-markdown" />
        </div>
    </div>
</template>

<style scoped>
.plan-pane {
    display: flex;
    flex-direction: column;
    height: 100%;
    min-height: 0;
    overflow: hidden;
}
.plan-scroll {
    flex: 1;
    min-height: 0;
    overflow: auto;
    padding: var(--wa-space-l);
}
.plan-markdown {
    max-width: 80ch;
    margin-inline: auto;
}
.plan-state {
    display: flex;
    flex: 1;
    align-items: center;
    justify-content: center;
    gap: var(--wa-space-s);
    color: var(--wa-color-text-quiet);
}
.plan-error {
    color: var(--wa-color-danger-on-quiet, var(--wa-color-text-quiet));
}
</style>
