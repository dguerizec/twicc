<script setup>
// Provider brand icon (Claude, Codex, …) tinted with the provider's own brand
// colour. Both the icon name and the colour are declared on the provider's
// helpers class (static ``icon`` / ``iconColor``) and resolved here, so every
// place that shows a provider icon renders it identically. Renders nothing
// when the provider is unknown or declares no icon. Extra attributes (class,
// size, slot, …) fall through to the underlying wa-icon.
import { computed } from 'vue'
import { getProviderIcon, getProviderIconColor } from '../../providers'

const props = defineProps({
    provider: { type: String, default: null },
    // Set to false to render the icon in the inherited text colour instead of
    // the provider's brand tint (e.g. the session list, where too many tinted
    // icons would be noisy).
    colored: { type: Boolean, default: true },
})

const name = computed(() => getProviderIcon(props.provider))
const color = computed(() => (props.colored ? getProviderIconColor(props.provider) : null))
</script>

<template>
    <wa-icon
        v-if="name"
        auto-width
        family="brands"
        :name="name"
        :style="color ? { color } : null"
    ></wa-icon>
</template>
