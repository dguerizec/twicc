<script setup>
// Root of the dedicated artifact page's trusted shell (design §5/§9). It iframes
// the artifact's inner document and mounts the *same* broker host + consent
// prompt the in-SPA preview uses, through the shared `useArtifactBroker`
// composable — so an artifact behaves identically in both contexts.
import { ref } from 'vue'
import { useArtifactBroker } from '../composables/useArtifactBroker'
import ArtifactBrokerPrompt from '../components/artifacts/ArtifactBrokerPrompt.vue'

const props = defineProps({
    // Backend-served inner-doc URL (/artifacts/<id>/__twicc_doc__).
    innerDocUrl: { type: String, required: true },
    bookmarkId: { type: Number, default: null },
    allowedHosts: { type: Object, default: () => ({}) },
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
        persistAllow,
    }),
    [iframeRef],
)
</script>

<template>
    <!-- Same sandbox as the in-SPA preview: scripts run, but top-level
         navigation, popups and modals do not. Same-origin kept (localStorage
         works; design §13). -->
    <iframe
        ref="iframeRef"
        :src="innerDocUrl"
        class="artifact-frame"
        sandbox="allow-scripts allow-same-origin allow-forms"
        title="Artifact"
    ></iframe>
    <ArtifactBrokerPrompt :prompt="brokerPrompt" @decision="onBrokerDecision" />
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
</style>
