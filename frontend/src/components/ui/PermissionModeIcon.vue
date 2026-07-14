<script setup>
// A permission-mode glyph rendered as a colour-tinted icon, shared by every
// permission-mode select (option rows + closed combobox) so the glyph/hue stays
// identical everywhere. Dumb + presentational: the (provider, mode) →
// { icon, color } mapping lives on the permission_mode choice objects and is
// resolved by the caller (``getChoiceIcon`` / a raw choice); this only renders.
// The SVGs are monochrome (currentColor), tinted via the ``color`` prop.
import { permissionIconSrc } from '../../utils/permissionModeIcon'

defineProps({
    // SVG basename under /icons/permission/ (e.g. 'shield-halved').
    icon: { type: String, required: true },
    // CSS colour applied to the glyph — a --wa-color-* token expression.
    color: { type: String, default: null },
    // Accessible label for the underlying wa-icon (optional).
    label: { type: String, default: null },
})
</script>

<template>
    <wa-icon
        auto-width
        :src="permissionIconSrc(icon)"
        :label="label || undefined"
        :style="color ? { color } : null"
    ></wa-icon>
</template>
