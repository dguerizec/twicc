<script setup>
import { ref, reactive, provide, onMounted, onUnmounted, computed } from 'vue'
import ShareItemsList from './ShareItemsList.vue'
import SharedSubagentView from './SharedSubagentView.vue'
import GlobalMediaPreview from '../components/media/GlobalMediaPreview.vue'
import { useDataStore } from '../stores/data'
import { useSettingsStore } from '../stores/settings'
import { getProviderIcon } from '../providers'
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
    title: meta.title || 'Shared session',
    last_line: meta.last_line, git_directory: null, cwd: null, artifacts_dir: null,
    created_at: meta.created_at, last_updated_at: meta.last_updated_at,
    compacted: meta.compacted === true,
})
settings.areMessageTimestampsShown = meta.show_timestamps !== false
settings.setDisplayMode(clampMode(meta.max_display_mode || 'normal'))

const displayModes = computed(() => boundedModes(meta.max_display_mode || 'normal'))
function boundedModes(max) {
    const order = ['conversation', 'simplified', 'normal', 'debug']
    return order.slice(0, order.indexOf(max) + 1)
}
function clampMode(m) { return boundedModes(meta.max_display_mode || 'normal').includes(m) ? m : 'normal' }
const capitalize = (s) => s.charAt(0).toUpperCase() + s.slice(1)

const providerIcon = computed(() => getProviderIcon(meta.provider))

// Subagent overlay stack (design §8.6). Opening one is reflected in the URL hash
// (#agent=<id>[,<nested>…]) through the History API — the share bundle has no
// router — so browser Back closes the drawer instead of leaving the share page.
const subagentStack = ref([])

function seedAgentSession(id, slug = null) {
    // Seed the subagent as a store session so the reused SessionItem dispatch can
    // resolve its provider (subagents inherit the root's) AND its display label.
    // Without it getSession returns null → provider undefined → every item falls to
    // UnknownEntry ("Unhandled event").
    store.setSession({
        id, provider: meta.provider, slug: slug || store.getSession(id)?.slug || null,
        project_id: 'share', title: null, last_line: 0,
        git_directory: null, cwd: null, artifacts_dir: null,
    })
}

function agentUrl(stack) {
    const base = location.pathname + location.search
    return stack.length ? `${base}#agent=${stack.join(',')}` : base
}
function openSubagent(agentId) {
    if (!store.getSession(agentId)) seedAgentSession(agentId)
    subagentStack.value.push(agentId)
    history.pushState({ shareAgentStack: [...subagentStack.value] }, '', agentUrl(subagentStack.value))
}
function closeSubagent() { history.back() }                                  // pop one → popstate syncs
function clearSubagents() { if (subagentStack.value.length) history.go(-subagentStack.value.length) }
function onPopState(e) {
    subagentStack.value = Array.isArray(e.state?.shareAgentStack) ? e.state.shareAgentStack : []
}

if (meta.include_subagents) {
    provide('openSubagent', openSubagent)
    api.fetchSubagents().then((links) => {
        store.setAgentLinks(meta.session_id, links)
        // Seed each subagent's slug so the drawer labels them like the owner UI
        // (Agent <slug>, else Agent <shortId>) via getAgentDisplayLabel.
        for (const l of links) if (l.agent_id) seedAgentSession(l.agent_id, l.agent_slug)
    }).catch(() => {})
}
provide('sessionActive', ref(true))
// A snapshot (or a share closed under the viewer) is a frozen transcript: no tool
// can be running, so the reused tree drops its running spinners / result polling.
// Live shares keep the real state (tool-states fetch + WS share_tool_state).
provide('transcriptFrozen', computed(() => meta.mode !== 'live' || revoked.value))

onMounted(() => {
    window.addEventListener('popstate', onPopState)
    // Anchor a base history entry (stack empty), then re-open any agents encoded in
    // the URL on load/reload — so Back from a deep-linked agent returns to the session.
    const hash = /#agent=([^&]*)/.exec(location.hash)
    history.replaceState({ shareAgentStack: [] }, '', location.pathname + location.search)
    if (meta.include_subagents && hash && hash[1]) {
        for (const id of hash[1].split(',').filter(Boolean)) openSubagent(id)
    }
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
            // Assistant-turn indicator: inject/drop the reused "<Provider> is
            // thinking" synthetic message (root session only).
            onProcessState: (m) => store.setLiveAssistantTurn(meta.session_id, m.state === 'assistant_turn'),
            // A subagent spawned live becomes openable (seed its link + session so
            // the tool card's "View Agent" resolves it).
            onAgentLink: (link) => {
                if (!link?.agent_id) return
                store.addAgentLink(meta.session_id, link)
                seedAgentSession(link.agent_id, link.agent_slug)
            },
            onClosed: () => { revoked.value = true; store.setLiveAssistantTurn(meta.session_id, false) },
        })
    }
})
onUnmounted(() => window.removeEventListener('popstate', onPopState))
</script>

<template>
    <div class="share-shell">
        <header class="share-header">
            <div class="share-title">
                <wa-icon v-if="providerIcon" auto-width family="brands" :name="providerIcon"></wa-icon>
                <strong>{{ meta.title || 'Shared session' }}</strong>
            </div>
            <wa-button id="share-menu-trigger" size="small" appearance="plain" class="share-menu-button">
                <wa-icon name="bars" label="View options"></wa-icon>
            </wa-button>
            <wa-popover for="share-menu-trigger" placement="bottom-end" class="share-menu">
                <div class="share-menu-content">
                    <label class="share-menu-field">Detail level
                        <wa-select size="small" :value="settings.displayMode"
                                   @change.stop="settings.setDisplayMode($event.target.value)">
                            <wa-option v-for="m in displayModes" :key="m" :value="m">{{ capitalize(m) }}</wa-option>
                        </wa-select>
                    </label>
                    <wa-switch size="small" :checked="settings.areMessageTimestampsShown"
                               @change.stop="settings.areMessageTimestampsShown = $event.target.checked">Show timestamps</wa-switch>
                    <label class="share-menu-field">Color scheme
                        <wa-select size="small" :value="settings._colorScheme"
                                   @change.stop="settings.setColorScheme($event.target.value)">
                            <wa-option value="system">System</wa-option>
                            <wa-option value="light">Light</wa-option>
                            <wa-option value="dark">Dark</wa-option>
                        </wa-select>
                    </label>
                </div>
            </wa-popover>
        </header>

        <wa-callout v-if="revoked" variant="warning" class="share-banner">
            This share is no longer available.
        </wa-callout>

        <ShareItemsList
            :session-id="meta.session_id"
            :last-line="meta.last_line"
        />

        <SharedSubagentView v-if="subagentStack.length"
            :stack="subagentStack" @close="closeSubagent" @clear="clearSubagents" />

        <GlobalMediaPreview />
        <footer class="share-footer">Shared with TwiCC</footer>
    </div>
</template>

<style>
/* App-shell layout: the viewer fills the viewport so the transcript list scrolls
   INTERNALLY. The VirtualScroller needs a bounded-height parent (it uses
   height:100% + overscroll-behavior:contain); without one it grows to full
   content height and swallows the wheel over the content, leaving only the page
   margins scrollable. This mirrors the SPA's SessionItemsList flex chain. */
html, body { height: 100%; margin: 0; }
#app { height: 100%; }
.share-shell { max-width: 60rem; margin: 0 auto; padding: 0 1rem; height: 100%;
    display: flex; flex-direction: column; }
.share-header { flex: 0 0 auto; display: flex; justify-content: space-between;
    align-items: center; gap: 1rem; padding: .5rem 0; background: var(--wa-color-surface-default); }
.share-title { display: flex; align-items: center; gap: .5rem; min-width: 0; }
.share-title strong { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.share-menu-button { flex: 0 0 auto; }
.share-menu-content { display: flex; flex-direction: column; gap: .75rem; min-width: 13rem; }
.share-menu-field { display: flex; flex-direction: column; gap: .3rem;
    font-size: var(--wa-font-size-s); font-weight: 600; }
.share-menu-field wa-select { font-weight: 400; }
.share-banner { flex: 0 0 auto; }
.share-items-list { flex: 1 1 auto; min-height: 0; display: flex; flex-direction: column;
    overflow: hidden; position: relative; }
.share-items-list .session-items { flex: 1; min-height: 0; }
.share-footer { flex: 0 0 auto; text-align: center; color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-s); padding: .75rem 0; }
/* Print: let everything flow (the scroller still only renders its virtualized
   window, but at least it isn't clipped to one viewport). */
@media print {
    html, body, #app, .share-shell { height: auto; }
    .share-items-list, .share-items-list .session-items { overflow: visible; min-height: 0; }
    .share-header, .share-footer { display: none; }
}
</style>
