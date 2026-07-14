// Path to a permission-mode glyph (public/icons/permission/<name>.svg). The
// (provider, mode) → { icon, color } mapping lives on each provider's
// permission_mode choice objects (AGENT_SETTINGS_CHOICES) and is resolved via
// ``getChoiceIcon``; this only turns the glyph's basename into its src. The
// SVGs are monochrome (currentColor), tinted by the caller via ``color``.
import { resolvePublicAssetUrl } from './publicAsset'

export function permissionIconSrc(name) {
    return name ? resolvePublicAssetUrl(`icons/permission/${name}.svg`) : null
}
