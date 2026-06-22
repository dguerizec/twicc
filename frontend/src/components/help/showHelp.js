import { useHelpStore } from '../../stores/help'

/**
 * Open a help page in the global HelpDialog manually — from a button, the
 * Settings → Help list, or a `help/<key>` cross-link. The page always
 * opens; `showDontShowAgain` (default true) controls whether the
 * "Don't show this again" switch appears. Pass false for reference pages
 * that should never be dismissible (e.g. the artifacts explainer), and
 * from the Settings list where dismissing makes no sense.
 *
 * @param {string} key
 * @param {{ showDontShowAgain?: boolean }} [options]
 */
export function showHelp(key, { showDontShowAgain = true } = {}) {
    useHelpStore().open(key, { showSwitch: showDontShowAgain })
}
