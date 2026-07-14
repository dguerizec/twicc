// Path to the 5-bar effort-level icon (public/icons/effort/<level>.svg), shared
// by the agent-settings summary strip and the matrix column headers. Always
// five ascending bars; the first N (matching the level's rank low→max) are
// coloured in the level's hue, the rest in a lighter shade of it. Returns null
// for an unknown effort value.
import { resolvePublicAssetUrl } from './publicAsset'

const EFFORT_ICON_LEVELS = ['low', 'medium', 'high', 'xhigh', 'max']

export function effortIconSrc(effort) {
    return EFFORT_ICON_LEVELS.includes(effort) ? resolvePublicAssetUrl(`icons/effort/${effort}.svg`) : null
}
