import { onMounted, onBeforeUnmount, watch } from 'vue'
import { useNotifications } from 'notivue'
import { useTipsStore } from '../stores/tips'
import { useSettingsStore } from '../stores/settings'
import { hasBlockingOverlay } from '../utils/focusGuard'
import { isTipToastItem } from '../components/tips/showTipToast'
// showTipToast (the function) is imported lazily inside tryShowTip() to
// break the cycle useTipScheduler → showTipToast → TipToast →
// useTipScheduler (CLAUDE.md "Avoiding Circular Imports"). The
// `isTipToastItem` named import above is safe because it's a plain
// helper that doesn't touch TipToast.vue.

// --- Tunable constants ---------------------------------------------------
// Cooldown measured from the moment the user voluntarily dismisses the
// previous tip (Close, Escape, or "Next tip" returning no further
// candidate). Bump as needed — this is the variable to tune first.
export const TIP_COOLDOWN_MS = 2 * 60 * 60_000   // 2 hours

// Delay between app mount and the first tip attempt.
export const FIRST_TIP_DELAY_MS = 60_000          // 60 seconds

// How often the scheduler wakes up to check whether it can show a tip.
// Short enough to feel responsive once the cooldown is over, long enough
// to be invisible. NOT the inter-tip delay.
export const SCHEDULER_POLL_MS = 60_000           // 1 minute
// -------------------------------------------------------------------------

export function useTipScheduler() {
    const tipsStore = useTipsStore()
    const settings = useSettingsStore()
    const { entries } = useNotifications()

    let pollHandle = null

    /** Any non-tip notification currently up? Tip toasts must yield to those. */
    function hasAppToast() {
        return entries.value.some((item) => !isTipToastItem(item))
    }

    /**
     * Auto-dismiss our tip without committing seen-state. Used when an app
     * toast appears while a tip is on-screen — app toasts (errors, session
     * events, confirmations) take precedence and the user never had a real
     * opportunity to read the tip, so it shouldn't count as seen.
     *
     * The order matters: setting `currentToastTipKey = null` BEFORE clearing
     * the Notivue entry makes TipToast.onBeforeUnmount short-circuit (it
     * checks for null and skips its commit-seen branch). nextEligibleTime
     * is deliberately NOT bumped: the next scheduler tick is free to
     * re-show the same tip once the app toast clears.
     */
    function dismissTipForAppToast() {
        tipsStore.currentToastTipKey = null
        const tipEntry = entries.value.find(isTipToastItem)
        if (tipEntry?.clear) tipEntry.clear()
    }

    async function tryShowTip() {
        if (!tipsStore.enabled) return
        if (tipsStore.currentToastTipKey !== null) return    // already showing
        if (Date.now() < tipsStore.nextEligibleTime) return  // cooldown
        if (hasBlockingOverlay()) return
        if (document.visibilityState !== 'visible') return
        if (hasAppToast()) return                            // yield to app toasts

        const env = {
            platform: settings._isTouchDevice ? 'mobile' : 'desktop',
            os: settings.os,
            enabledProviders: settings.enabledProviders,
        }
        const candidates = tipsStore.getCandidates(env)
        if (candidates.length === 0) return

        const tip = tipsStore.pickRandom(candidates)
        if (!tip) return

        // Lazy import to break the circular dependency with TipToast.vue
        // (which imports TIP_COOLDOWN_MS from this file).
        const { showTipToast } = await import('../components/tips/showTipToast')
        showTipToast(tip.key)
        // nextEligibleTime is NOT updated here; it gets bumped on dismiss
        // by TipToast.vue.
    }

    // Auto-discard our tip toast as soon as a non-tip toast appears. Tip
    // toasts are low-priority; app toasts (errors, session events, ...)
    // must take precedence. We do NOT mark the tip as seen, since the user
    // never got a chance to interact with it.
    watch(entries, (current) => {
        if (tipsStore.currentToastTipKey === null) return
        if (current.some((item) => !isTipToastItem(item))) {
            dismissTipForAppToast()
        }
    })

    onMounted(() => {
        tipsStore.nextEligibleTime = Date.now() + FIRST_TIP_DELAY_MS
        pollHandle = setInterval(tryShowTip, SCHEDULER_POLL_MS)
    })

    onBeforeUnmount(() => {
        if (pollHandle) {
            clearInterval(pollHandle)
            pollHandle = null
        }
    })
}
