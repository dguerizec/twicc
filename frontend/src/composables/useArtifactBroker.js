// Shared wiring for the artifact network broker (design §9). Both run contexts —
// the in-SPA preview (FilePane) and the dedicated artifact page (the shell
// bundle) — mount the broker host the *same* way through this composable, so an
// artifact behaves identically in either context (the whole point of phase 5).
//
// The composable owns two things: the consent-prompt state machine (a promise
// the host awaits while the dialog is up) and the host mount/teardown lifecycle.
// The caller supplies the iframe ref, a per-mount config getter, and the reactive
// sources whose change should (re)mount. It stays free of stores/router so the
// shell bundle pulls in nothing of the main SPA.

import { ref, toValue, watch, onBeforeUnmount } from 'vue'
import { mountBrokerHost } from '../artifact-broker/host'

/**
 * @param {import('vue').Ref<HTMLIFrameElement|null>} iframeRef  The artifact iframe.
 * @param {() => ({ documentUrl: string, bookmarkId: number|null, allowedHosts: object, persistAllow?: Function }) | null} getConfig
 *        Evaluated at each (re)mount; return `null` to mount nothing (inactive).
 * @param {Array} [watchSources]  Reactive sources whose change re-runs the mount
 *        (defaults to `[iframeRef]`). Pass the caller's own activation deps so the
 *        (re)mount triggers match its context exactly.
 * @returns {{ brokerPrompt: import('vue').Ref, onBrokerDecision: (d: string) => void }}
 *        Bind these to `<ArtifactBrokerPrompt :prompt="brokerPrompt" @decision="onBrokerDecision" />`.
 */
export function useArtifactBroker(iframeRef, getConfig, watchSources) {
    const brokerPrompt = ref(null) // { host, ip, kind, canRemember, settle } | null
    let brokerConnection = null

    // The host calls this to ask the user; resolves once (button click or dismiss).
    function showBrokerPrompt(target) {
        return new Promise((resolve) => {
            let done = false
            const settle = (decision) => {
                if (done) return
                done = true
                brokerPrompt.value = null
                resolve(decision)
            }
            brokerPrompt.value = { ...target, settle }
        })
    }

    function onBrokerDecision(decision) {
        brokerPrompt.value?.settle(decision)
    }

    function teardownBroker() {
        if (brokerConnection) {
            brokerConnection.destroy()
            brokerConnection = null
        }
        onBrokerDecision('deny') // resolve any prompt left hanging
    }

    function setupBroker() {
        teardownBroker()
        const iframe = toValue(iframeRef)
        const config = getConfig()
        if (!iframe || !config) return
        brokerConnection = mountBrokerHost(iframe, {
            documentUrl: config.documentUrl,
            bookmarkId: config.bookmarkId ?? null,
            allowedHosts: config.allowedHosts ?? {},
            showPrompt: showBrokerPrompt,
            persistAllow: config.persistAllow,
        })
    }

    watch(watchSources ?? [iframeRef], setupBroker, { flush: 'post' })
    onBeforeUnmount(teardownBroker)

    return { brokerPrompt, onBrokerDecision }
}
