<script setup>
// Presentational renderer for a compact one-line agent-settings summary:
// the provider icon followed by the precomputed summary parts. Kept free of
// any session/composable knowledge so it can be reused both by the
// message-input summary (live ``useSessionAgentSettings`` state) and by the
// orchestration tree (static per-node bundles). Callers compute the ``parts``
// via ``providerHelpers.getSummaryParts(state)`` and pass them in.
//
// A part is either a text part (``{ text, forced }``) or an icon part
// (``{ icon, iconFamily?, on, color?, forced, label }``) — boolean flags like
// thinking / fast / Chrome MCP render as a coloured icon, dimmed + struck when
// off, with the label as a tooltip. The model text part additionally carries
// the effort as a 5-bar level icon (``{ effortSrc, effortLabel }``) glued
// right after the label.
//
// ``markForced`` controls the dashed underline on parts that differ from the
// provider defaults: useful when a user is composing a session (see at a
// glance what they changed), pointless for sessions that spawned each other
// in an orchestration tree — those pass ``markForced=false`` to render every
// effective value plainly.
import { computed, useId } from 'vue'
import { getProviderIcon, getProviderIconColor } from '../../providers'
import AppTooltip from '../ui/AppTooltip.vue'

const props = defineProps({
    provider: { type: String, default: null },
    // [{ text, forced } | { icon, iconFamily?, on, forced, label }] — already
    // resolved by the caller.
    parts: { type: Array, default: () => [] },
    markForced: { type: Boolean, default: true },
})

const providerIcon = computed(() => getProviderIcon(props.provider))
const providerColor = computed(() => getProviderIconColor(props.provider))

const uid = useId()
const iconId = (i) => `${uid}-icon-${i}`

// Semantic colour name (set by getSummaryParts) → WA token, applied only when
// the flag is on. Off icons stay dimmed/struck in the neutral text colour.
const ICON_COLORS = {
    pink: 'var(--wa-color-pink-70)',
    yellow: 'var(--wa-color-yellow-60)',
    chrome: 'var(--wa-color-blue-60)',
}
function iconColor(part) {
    return part.on && part.color ? ICON_COLORS[part.color] : null
}

// Suppress the "·" separator between two adjacent icon parts so the boolean
// flags read as one grouped cluster; text boundaries keep their dot.
function showSeparator(i) {
    if (!i) return false
    return !(props.parts[i].icon && props.parts[i - 1]?.icon)
}
</script>

<template>
    <span class="agent-settings-summary">
        <wa-icon
            v-if="providerIcon"
            auto-width
            family="brands"
            :name="providerIcon"
            class="provider-icon"
            :style="providerColor ? { color: providerColor } : null"
        ></wa-icon>
        <template v-for="(part, i) in parts" :key="i">
            <span v-if="showSeparator(i)"> · </span>

            <!-- Boolean flag rendered as a coloured icon (thinking / fast /
                 chrome), dimmed + struck when off. -->
            <template v-if="part.icon">
                <span
                    :id="iconId(i)"
                    class="part-icon-wrap"
                    :class="{
                        'setting-forced': markForced && part.forced,
                        struck: !part.on,
                        'icon-grouped': i > 0 && parts[i - 1]?.icon,
                    }"
                >
                    <wa-icon
                        auto-width
                        :name="part.icon"
                        :family="part.iconFamily || undefined"
                        :label="part.label"
                        class="part-icon"
                        :class="{ dimmed: !part.on }"
                        :style="iconColor(part) ? { color: iconColor(part) } : null"
                    ></wa-icon>
                </span>
                <AppTooltip :for="iconId(i)">{{ part.label }}</AppTooltip>
            </template>

            <!-- Text part (model, permission, …). The model part also carries
                 the 5-bar effort-level icon glued right after its label. -->
            <template v-else>
                <span :class="{ 'setting-forced': markForced && part.forced }">{{ part.text }}</span>
                <template v-if="part.effortSrc">
                    <wa-icon
                        :id="iconId(i)"
                        auto-width
                        :src="part.effortSrc"
                        :label="part.effortLabel"
                        class="effort-icon"
                    ></wa-icon>
                    <AppTooltip :for="iconId(i)">{{ part.effortLabel }}</AppTooltip>
                </template>
            </template>
        </template>
    </span>
</template>

<style scoped>
.setting-forced {
    text-decoration: underline dashed;
    text-underline-offset: 3px;
}

.provider-icon {
    margin-right: 0.25rem;
}

/* Effort-level bars glued right after the model label (replaces "× effort"). */
.effort-icon {
    margin-left: 0.3em;
    font-size: 1.1em;
    vertical-align: -0.15em;
}

.part-icon-wrap {
    position: relative;
    text-underline-offset: 3px;
}

/* Adjacent icons have no "·" between them — give them a small gap instead. */
.part-icon-wrap.icon-grouped {
    margin-left: 0.3em;
}

/* Off flags stay visible but dimmed so both states read at a glance. */
.part-icon.dimmed {
    opacity: 0.4;
}

/* An off flag also gets a "/" slash across it — the icon stays dimmed while
   the slash keeps the normal text colour so the disabled state pops. */
.part-icon-wrap.struck::after {
    content: '';
    position: absolute;
    left: 50%;
    top: 50%;
    width: 1.45em;
    height: 1.5px;
    background: currentColor;
    border-radius: 1px;
    transform: translate(-50%, -50%) rotate(-45deg);
    pointer-events: none;
}
</style>
