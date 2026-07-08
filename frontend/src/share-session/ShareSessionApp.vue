<script setup>
import { ref, reactive, provide, onMounted, computed } from 'vue'
import ShareItemsList from './ShareItemsList.vue'
import SharedSubagentView from './SharedSubagentView.vue'
import GlobalMediaPreview from '../components/media/GlobalMediaPreview.vue'
import { useDataStore } from '../stores/data'
import { useSettingsStore } from '../stores/settings'
import { makeShareApi, setShareApi } from './shims/shareApi'
import { connectShareLive } from './shims/shareLive'

const props = defineProps({ tokenPath: String, meta: Object })

const api = makeShareApi(props.tokenPath); setShareApi(api)
provide('shareApi', api)

const store = useDataStore()
const settings = useSettingsStore()
const meta = reactive({ ...props.meta })
const revoked = ref(false)

// Seed a session-ish object the reused components read via getSession.
store.setSession({
    id: meta.session_id, provider: meta.provider, project_id: 'share',
    title: meta.title || 'Shared session', total_cost: meta.total_cost ?? null,
    last_line: meta.last_line, git_directory: null, cwd: null, artifacts_dir: null,
    created_at: meta.created_at, last_updated_at: meta.last_updated_at,
})
settings.areMessageTimestampsShown = meta.show_timestamps !== false
settings.areCostsShown = !!meta.total_cost
settings.setDisplayMode(clampMode(meta.max_display_mode || 'normal'))

const displayModes = computed(() => boundedModes(meta.max_display_mode || 'normal'))
function boundedModes(max) {
    const order = ['conversation', 'simplified', 'normal', 'debug']
    return order.slice(0, order.indexOf(max) + 1)
}
function clampMode(m) { return boundedModes(meta.max_display_mode || 'normal').includes(m) ? m : 'normal' }

// Subagent overlay stack (design §8.6).
const subagentStack = ref([])
if (meta.include_subagents) {
    provide('openSubagent', (agentId) => subagentStack.value.push(agentId))
    api.fetchSubagents().then((links) => store.setAgentLinks(meta.session_id, links)).catch(() => {})
}
provide('sessionActive', ref(true))
// A snapshot (or a share closed under the viewer) is a frozen transcript: no tool
// can be running, so the reused tree drops its running spinners / result polling.
// Live shares keep the real state (tool-states fetch + WS share_tool_state).
provide('transcriptFrozen', computed(() => meta.mode !== 'live' || revoked.value))

onMounted(() => {
    if (meta.mode === 'live') {
        connectShareLive({
            tokenPath: props.tokenPath, sessionId: meta.session_id,
            // The consumer forwards subagent traffic too — route by the message's
            // own session_id, never assume the root.
            onItems: (items, sid) => store.addSessionItems(sid || meta.session_id, items),
            // Fresh meta can carry a TIGHTENED max_display_mode: re-clamp the
            // viewer's current mode so the select never sits on a now-invalid value.
            onMeta: (m) => { Object.assign(meta, m); settings.setDisplayMode(clampMode(settings.displayMode)) },
            onToolState: (m) => store.setToolState(
                m.session_id, m.tool_use_id, m.result_count, m.completed_at,
                m.error ?? null, m.extra ?? null, m.tool_result_line_nums || [],
            ),
            onClosed: () => { revoked.value = true },
        })
    }
})
</script>

<template>
    <div class="share-shell">
        <header class="share-header">
            <div class="share-title">
                <wa-icon :name="meta.provider === 'codex' ? 'circle' : 'robot'"></wa-icon>
                <strong>{{ meta.title || 'Shared session' }}</strong>
                <wa-tag size="small" variant="neutral">Read-only</wa-tag>
                <wa-tag v-if="meta.mode === 'live'" size="small" variant="success">Live</wa-tag>
            </div>
            <div class="share-controls">
                <wa-select size="small" :value="settings.displayMode"
                           @change="settings.setDisplayMode($event.target.value)">
                    <wa-option v-for="m in displayModes" :key="m" :value="m">{{ m }}</wa-option>
                </wa-select>
                <wa-switch size="small" :checked="settings.areMessageTimestampsShown"
                           @change="settings.areMessageTimestampsShown = $event.target.checked">Times</wa-switch>
                <wa-button size="small" appearance="plain"
                           @click="settings.setColorScheme(settings._effectiveColorScheme === 'dark' ? 'light' : 'dark')">
                    <wa-icon :name="settings._effectiveColorScheme === 'dark' ? 'sun' : 'moon'"></wa-icon>
                </wa-button>
            </div>
        </header>

        <wa-callout v-if="revoked" variant="warning" class="share-banner">
            This share is no longer available.
        </wa-callout>

        <ShareItemsList
            :session-id="meta.session_id"
            :last-line="meta.last_line"
        />

        <SharedSubagentView v-if="subagentStack.length"
            :stack="subagentStack" @close="subagentStack.pop()" @clear="subagentStack = []" />

        <GlobalMediaPreview />
        <footer class="share-footer">Shared with TwiCC</footer>
    </div>
</template>

<style>
.share-shell { max-width: 60rem; margin: 0 auto; padding: 1rem; }
.share-header { position: sticky; top: 0; z-index: 3; display: flex; justify-content: space-between;
    align-items: center; gap: 1rem; padding: .5rem 0; background: var(--wa-color-surface-default); }
.share-title { display: flex; align-items: center; gap: .5rem; }
.share-controls { display: flex; align-items: center; gap: .5rem; }
.share-footer { text-align: center; color: var(--wa-color-text-quiet); font-size: var(--wa-font-size-s);
    padding: 2rem 0 1rem; }
@media print { .share-header, .share-controls, .share-footer { display: none; } }
</style>
