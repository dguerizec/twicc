import { useToast } from '../../composables/useToast'
import { useTipsStore } from '../../stores/tips'
import TipToast from './TipToast.vue'

/**
 * Show (or swap to) a tip toast.
 * If a toast is already displayed, this just swaps the current key — the
 * TipToast component watches `currentToastTipKey` to re-render in place.
 */
export function showTipToast(key) {
    const tipsStore = useTipsStore()
    if (!tipsStore.manifest[key]) return

    if (tipsStore.currentToastTipKey !== null) {
        tipsStore.currentToastTipKey = key
        return
    }

    tipsStore.currentToastTipKey = key
    useToast().custom(TipToast, {
        type: 'info',
        duration: Number.POSITIVE_INFINITY,
        style: { '--nv-width': 'min(480px, calc(100vw - 2rem))' },
        // Sentinel used by useTipScheduler to tell our tip toast apart
        // from "app" toasts (errors, session events, ...). App toasts
        // have priority — the scheduler won't fire while one is up, and
        // an app toast appearing during a tip auto-dismisses the tip.
        props: { isTipToast: true },
    })
}

/**
 * True iff a Notivue notification item is our tip toast (vs an app toast).
 * Identified by the `isTipToast` marker we pass in `contentProps` above.
 */
export function isTipToastItem(item) {
    return item?.props?.contentProps?.isTipToast === true
}
