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
 * @param {() => ({ documentUrl: string, getBookmarkId: () => (number|null), allowedHosts: object, persistAllow?: Function }) | null} getConfig
 *        Evaluated at each (re)mount; return `null` to mount nothing (inactive).
 *        `getBookmarkId` is kept as a getter (not a snapshot) so the host reflects
 *        the live bookmark — created/removed without a re-mount — for "Forever".
 * @param {Array} [watchSources]  Reactive sources whose change re-binds the host
 *        (defaults to `[iframeRef]`). Pass the caller's own activation deps so the
 *        (re)bind triggers match its context exactly. The host ALSO re-binds on the
 *        iframe's `load` event, which covers reloads that change no reactive dep
 *        (e.g. KeepAlive reparenting the iframe into a fresh browsing context).
 * @returns {{ brokerPrompt: import('vue').Ref, onBrokerDecision: (d: string) => void }}
 *        Bind these to `<ArtifactBrokerPrompt :prompt="brokerPrompt" @decision="onBrokerDecision" />`.
 */
export function useArtifactBroker(iframeRef, getConfig, watchSources) {
    const brokerPrompt = ref(null) // { host, ip, kind, canRemember, settle } | null
    let brokerConnection = null
    // The iframe contentWindow the live connection is bound to, plus the iframe
    // element we hold a `load` listener on. Tracked so we rebind on a *new* browsing
    // context (KeepAlive reparenting reloads the iframe → new contentWindow) but
    // leave a live connection untouched on a same-window reload (penpal re-handshakes).
    let boundWindow = null
    let listeningIframe = null

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
        boundWindow = null
        onBrokerDecision('deny') // resolve any prompt left hanging
    }

    function setupBroker() {
        const iframe = toValue(iframeRef)
        const config = getConfig()
        const win = iframe?.contentWindow ?? null
        // Already bound to this exact window with an active config → leave it alone:
        // a same-element src reload keeps the same contentWindow, and penpal
        // re-handshakes with the freshly-loaded shim on its own. Tearing the live
        // connection down here would needlessly interrupt an in-flight handshake.
        if (config && win && brokerConnection && win === boundWindow) return
        teardownBroker()
        if (!iframe || !config || !win) return
        brokerConnection = mountBrokerHost(iframe, {
            documentUrl: config.documentUrl,
            getBookmarkId: config.getBookmarkId ?? (() => null),
            allowedHosts: config.allowedHosts ?? {},
            showPrompt: showBrokerPrompt,
            persistAllow: config.persistAllow,
            mode: config.mode ?? 'owner',
            proxyUrl: config.proxyUrl,
        })
        boundWindow = win
    }

    // Re-bind the host whenever the iframe's document (re)loads. This is the only
    // reliable trigger when the iframe reloads WITHOUT a reactive dep changing:
    // KeepAlive reparents the panel back into the DOM on reactivation, which gives
    // the iframe a brand-new browsing context (new contentWindow + a fresh shim)
    // while documentUrl/activation stay identical — so the watch below never fires,
    // and a host wired only to it would stay bound to the old, now-dead window
    // (the shim then handshakes with nobody and times out). `load` fires after the
    // new document — and its shim — are ready, so the rebind binds to the right one.
    function onIframeLoad() {
        setupBroker()
    }

    // Keep the `load` listener on the current iframe element, re-attaching if the
    // element instance itself changes (e.g. a `:key` swap when the file changes).
    function trackIframe() {
        const iframe = toValue(iframeRef)
        if (iframe === listeningIframe) return
        listeningIframe?.removeEventListener('load', onIframeLoad)
        listeningIframe = iframe ?? null
        listeningIframe?.addEventListener('load', onIframeLoad)
    }

    watch(
        watchSources ?? [iframeRef],
        () => {
            trackIframe()
            setupBroker()
        },
        { flush: 'post' },
    )
    onBeforeUnmount(() => {
        listeningIframe?.removeEventListener('load', onIframeLoad)
        listeningIframe = null
        teardownBroker()
    })

    return { brokerPrompt, onBrokerDecision }
}
