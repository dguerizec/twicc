// Shared, Vue-free metadata for the dockable layout UI (labels, placement options, edges).
// The pure geometry/rules live in utils/layoutResolver.js; this is presentation-only.

import { DOCKS } from '../../../utils/layoutResolver'

export const CENTER = 'center'

export const DOCK_LABELS = {
    center: 'Main area',
    'left-top': 'Left top',
    'left-bottom': 'Left bottom',
    'right-top': 'Right top',
    'right-bottom': 'Right bottom',
    'bottom-left': 'Bottom left',
    'bottom-right': 'Bottom right',
}

// Custom position-hinting icons (PyCharm-style: layout frame + a filled bar where the panel docks).
// Local SVGs in public/icons/docks/, consumed via `<wa-icon src>`. One per placement, unlike the old
// single shared FA glyph that couldn't distinguish the positions.
export const DOCK_ICONS = {
    center: '/icons/docks/center.svg',
    'left-top': '/icons/docks/left-top.svg',
    'left-bottom': '/icons/docks/left-bottom.svg',
    'right-top': '/icons/docks/right-top.svg',
    'right-bottom': '/icons/docks/right-bottom.svg',
    'bottom-left': '/icons/docks/bottom-left.svg',
    'bottom-right': '/icons/docks/bottom-right.svg',
}

// Order shown in the per-tab placement menu: Center first, then the six docks.
export const PLACEMENT_OPTIONS = [CENTER, ...DOCKS]

// Which edge gutter a dock collapses to.
export function edgeOfDock(dockId) {
    if (dockId.startsWith('left-')) return 'left'
    if (dockId.startsWith('right-')) return 'right'
    if (dockId.startsWith('bottom-')) return 'bottom'
    return null
}
