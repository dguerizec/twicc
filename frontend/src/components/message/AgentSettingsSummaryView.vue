<script setup>
// Presentational renderer for a compact one-line agent-settings summary:
// the provider icon followed by the precomputed summary parts. Kept free of
// any session/composable knowledge so it can be reused both by the
// message-input summary (live ``useSessionAgentSettings`` state) and by the
// orchestration tree (static per-node bundles). Callers compute the ``parts``
// via ``providerHelpers.getSummaryParts(state)`` and pass them in.
//
// A part is either a text part (``{ text, forced }``) or an icon part
// (``{ field, on, forced, label }``) — boolean flags like thinking / fast /
// Chrome MCP render as a coloured icon (glyph + tint from AGENT_SETTING_ICONS),
// dimmed + struck when off, with the label as a tooltip. The model text part
// additionally carries the effort as a 5-bar level icon (``{ effortSrc,
// effortLabel }``) glued right after the label.
//
// ``markForced`` controls the dashed underline on parts that differ from the
// provider defaults: useful when a user is composing a session (see at a
// glance what they changed), pointless for sessions that spawned each other
// in an orchestration tree — those pass ``markForced=false`` to render every
// effective value plainly.
import { computed, useId } from 'vue'
import { getProviderIcon } from '../../providers'
import AppTooltip from '../ui/AppTooltip.vue'
import ProviderIcon from '../ui/ProviderIcon.vue'
import SettingFlagIcon from '../ui/SettingFlagIcon.vue'

const props = defineProps({
    provider: { type: String, default: null },
    // [{ text, forced } | { field, on, forced, label }] — already resolved by
    // the caller.
    parts: { type: Array, default: () => [] },
    markForced: { type: Boolean, default: true },
})

const providerIcon = computed(() => getProviderIcon(props.provider))

const uid = useId()
const iconId = (i) => `${uid}-icon-${i}`

// Suppress the "·" separator between two adjacent icon parts so the boolean
// flags read as one grouped cluster; text boundaries keep their dot.
function showSeparator(i) {
    if (!i) return false
    return !(props.parts[i].field && props.parts[i - 1]?.field)
}
</script>

<template>
    <span class="agent-settings-summary">
        <ProviderIcon v-if="providerIcon" :provider="provider" class="provider-icon" />
        <template v-for="(part, i) in parts" :key="i">
            <span v-if="showSeparator(i)"> · </span>

            <!-- Boolean flag rendered as a coloured icon (thinking / fast /
                 chrome), dimmed + struck when off. -->
            <template v-if="part.field">
                <span
                    :id="iconId(i)"
                    class="part-icon-wrap"
                    :class="{
                        'setting-forced': markForced && part.forced,
                        'icon-grouped': i > 0 && parts[i - 1]?.field,
                    }"
                >
                    <SettingFlagIcon :field="part.field" :on="part.on" :label="part.label" />
                </span>
                <AppTooltip :for="iconId(i)">{{ part.label }}</AppTooltip>
            </template>

            <!-- Text part (model, permission, …). The model part also carries
                 the 5-bar effort-level icon glued right after its label. -->
            <template v-else>
                <span :class="{ 'setting-forced': markForced && part.forced }">{{ part.text }}</span>
                <template v-if="part.effortSrc">
                    <span
                        :id="iconId(i)"
                        class="effort-icon-wrap"
                        :class="{ 'setting-forced': markForced && part.forced }"
                    >
                        <wa-icon
                            auto-width
                            :src="part.effortSrc"
                            :label="part.effortLabel"
                            class="effort-icon"
                        ></wa-icon>
                    </span>
                    <AppTooltip :for="iconId(i)">{{ part.effortLabel }}</AppTooltip>
                </template>
            </template>
        </template>
    </span>
</template>

<style scoped>
/* A part that differs from the provider default gets a dashed underline —
   drawn as a border-bottom pseudo (not text-decoration) so it renders
   identically for text labels AND for (atomic inline) wa-icons, which
   text-decoration doesn't paint under. Uniform dash/gap across the whole
   summary. */
.setting-forced {
    position: relative;
}

.setting-forced::before {
    content: '';
    position: absolute;
    left: 0;
    right: 0;
    bottom: -2px;
    border-bottom: 1px dashed currentColor;
    pointer-events: none;
}

.provider-icon {
    margin-right: 0.25rem;
}

/* Effort-level bars glued right after the model label (replaces "× effort"). */
.effort-icon-wrap {
    position: relative;
    margin-left: 0.3em;
}

.effort-icon {
    font-size: 1.1em;
    vertical-align: -0.15em;
}

.part-icon-wrap {
    position: relative;
}

/* Adjacent icons have no "·" between them — give them a small gap instead. */
.part-icon-wrap.icon-grouped {
    margin-left: 0.3em;
}
</style>
