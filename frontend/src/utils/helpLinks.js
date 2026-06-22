/**
 * Delegated click handler for cross-links to help pages.
 *
 * `utils/contentAssets.js` rewrites markdown links of the form
 * `[label](help/<key>)` into inert `<a data-help-key="<key>" href="#">`.
 * Wire this on the `@click` of any container that renders such bodies
 * (the tip toast, the help dialog): a click on a tagged link opens the
 * corresponding help page instead of navigating.
 */
import { showHelp } from '../components/help/showHelp'

export function handleHelpLinkClick(event) {
    const target = event.target
    const anchor = target && target.closest ? target.closest('a[data-help-key]') : null
    if (!anchor) return
    event.preventDefault()
    const key = anchor.getAttribute('data-help-key')
    if (key) showHelp(key)
}
