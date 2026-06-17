// Shared, Vue-free metadata for the dockable layout UI (labels, placement options, edges).
// The pure geometry/rules live in utils/layoutResolver.js; this is presentation-only.

import { DOCKS } from '../../../utils/layoutResolver'

export const CENTER = 'center'

export const DOCK_LABELS = {
    center: 'Center',
    'left-top': 'Left top',
    'left-bottom': 'Left bottom',
    'right-top': 'Right top',
    'right-bottom': 'Right bottom',
    'bottom-left': 'Bottom left',
    'bottom-right': 'Bottom right',
}

// Icons (Font Awesome names, as used elsewhere via wa-icon) hinting the dock position.
export const DOCK_ICONS = {
    center: 'table-columns',
    'left-top': 'table-cells-large',
    'left-bottom': 'table-cells-large',
    'right-top': 'table-cells-large',
    'right-bottom': 'table-cells-large',
    'bottom-left': 'table-cells-large',
    'bottom-right': 'table-cells-large',
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
