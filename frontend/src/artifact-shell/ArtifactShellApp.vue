<script setup>
// Root of the dedicated artifact page's trusted shell (design §5/§9). It iframes
// the artifact's inner document and mounts the *same* broker host + consent
// prompt the in-SPA preview uses, through the shared `useArtifactBroker`
// composable — so an artifact behaves identically in both contexts.
import { ref, onMounted } from 'vue'
import { useArtifactBroker } from '../composables/useArtifactBroker'
import ArtifactBrokerPrompt from '../components/artifacts/ArtifactBrokerPrompt.vue'

const props = defineProps({
    // Backend-served inner-doc URL (/artifacts/<id>/__twicc_doc__).
    innerDocUrl: { type: String, required: true },
    bookmarkId: { type: Number, default: null },
    allowedHosts: { type: Object, default: () => ({}) },
    mode: { type: String, default: 'owner' },
    proxyUrl: { type: String, default: undefined },
    snapshotAt: { type: [String, null], default: null },
    tokenPath: { type: [String, null], default: null },
})

const iframeRef = ref(null)

// Persist "Forever" onto the bookmark's allowlist. The page holds the TwiCC
// session cookie, so a same-origin POST is authenticated — no auth-store/router
// dependency, which keeps this bundle free of the main SPA.
async function persistAllow(url, kind) {
    if (props.bookmarkId == null) return
    await fetch(`/api/artifact-bookmarks/${props.bookmarkId}/allowed-hosts/`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        credentials: 'same-origin',
        body: JSON.stringify({ url, kind }),
    })
}

// documentUrl is the inner doc; its own relative assets resolve to
// /artifacts/<id>/<asset>, so the artifact's directory is the inner doc's parent.
const { brokerPrompt, onBrokerDecision } = useArtifactBroker(
    iframeRef,
    () => ({
        documentUrl: new URL(props.innerDocUrl, location.href).href,
        getBookmarkId: () => props.bookmarkId,
        allowedHosts: props.allowedHosts,
        persistAllow: props.mode === 'share' ? undefined : persistAllow,
        mode: props.mode,
        proxyUrl: props.proxyUrl,
    }),
    [iframeRef],
)

// Share mode: poll the snapshot freshness (D7) for the "updated — reload" banner.
const updated = ref(false)
onMounted(() => {
    if (props.mode !== 'share' || !props.tokenPath) return
    setInterval(async () => {
        if (document.hidden) return
        try {
            const m = await (await fetch(`${props.tokenPath}/api/artifact-meta/`, { credentials: 'same-origin' })).json()
            if (m.snapshot_at && m.snapshot_at !== props.snapshotAt) updated.value = true
        } catch { /* ignore */ }
    }, 30000)
})
</script>

<template>
    <!-- Same sandbox as the in-SPA preview: scripts run, but top-level
         navigation, popups and modals do not. Same-origin kept (localStorage
         works; design §13). -->
    <div v-if="updated" class="share-update-banner">
        This artifact was updated — <a href="#" @click.prevent="location.reload()">Reload</a>
    </div>
    <iframe
        ref="iframeRef"
        :src="innerDocUrl"
        class="artifact-frame"
        sandbox="allow-scripts allow-same-origin allow-forms"
        title="Artifact"
    ></iframe>
    <!-- Share mode never prompts (server enforces the owner allowlist, D6). -->
    <ArtifactBrokerPrompt v-if="mode !== 'share'" :prompt="brokerPrompt" @decision="onBrokerDecision" />
</template>

<style>
/* The shell is the whole tab; the artifact iframe fills the viewport. */
html,
body {
    margin: 0;
    height: 100%;
}
#app {
    height: 100vh;
}
.artifact-frame {
    display: block;
    width: 100%;
    height: 100%;
    border: 0;
}
.share-update-banner {
    position: fixed;
    top: 0;
    left: 0;
    right: 0;
    z-index: 10;
    padding: 0.5rem 1rem;
    text-align: center;
    background: #0891b2;
    color: #fff;
    font: 500 0.9rem system-ui, -apple-system, sans-serif;
}
.share-update-banner a {
    color: #fff;
    text-decoration: underline;
}
</style>
