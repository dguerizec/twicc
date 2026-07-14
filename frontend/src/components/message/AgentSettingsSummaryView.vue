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
// effortLabel }``) after the label; the permission text part carries the
// coloured mode glyph (``{ permissionIcon, permissionColor }``) before it.
//
// The summary is a flex row that stays on one line and shrinks to fit its
// container: the parts carry priority classes (``agent-summary-model``,
// ``agent-summary-dot``, ``agent-summary-permission``) so the CSS drops the
// least useful bits first (permission label, then separators, then the model
// clips toward its first letter) via graduated ``flex-shrink`` — no JS.
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
import PermissionModeIcon from '../ui/PermissionModeIcon.vue'

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
// flags read as one grouped cluster; text boundaries keep their dot. A part may
// also opt out explicitly via ``groupWithPrevious`` (thinking, glued to the
// model + effort cluster).
function showSeparator(i) {
    if (!i) return false
    if (props.parts[i].groupWithPrevious) return false
    return !(props.parts[i].field && props.parts[i - 1]?.field)
}
</script>

<template>
    <span class="agent-settings-summary">
        <ProviderIcon v-if="providerIcon" :provider="provider" class="provider-icon" />
        <template v-for="(part, i) in parts" :key="i">
            <span v-if="showSeparator(i)" class="agent-summary-dot"> · </span>

            <!-- Boolean flag rendered as a coloured icon (thinking / fast /
                 chrome), dimmed + struck when off. -->
            <template v-if="part.field">
                <span
                    :id="iconId(i)"
                    class="part-icon-wrap"
                    :class="{ 'setting-forced': markForced && part.forced }"
                >
                    <SettingFlagIcon :field="part.field" :on="part.on" :label="part.label" />
                </span>
                <AppTooltip :for="iconId(i)">{{ part.label }}</AppTooltip>
            </template>

            <!-- Text part (model, permission, …). The model part also carries
                 the 5-bar effort-level icon after its label; the permission
                 part carries the coloured mode glyph before it. -->
            <template v-else>
                <PermissionModeIcon
                    v-if="part.permissionIcon"
                    :icon="part.permissionIcon"
                    :color="part.permissionColor"
                    :label="part.text"
                    class="summary-permission-icon"
                    :class="{ 'setting-forced': markForced && part.forced }"
                />
                <span
                    :class="{
                        'setting-forced': markForced && part.forced,
                        'agent-summary-model': i === 0,
                        'agent-summary-permission': part.permissionIcon,
                    }"
                >{{ part.text }}</span>
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
.agent-settings-summary {
    display: flex;
    align-items: center;
    column-gap: var(--wa-space-2xs);
    overflow: hidden;
    line-height: 1.1;

    & > * {
        flex-shrink: 0;
        overflow: hidden;
        white-space: normal;
    }

    & > .agent-summary-model {
        flex-shrink: 1;
        min-width: .75rem;
    }

    & > .agent-summary-dot {
        flex-shrink: 100;
        min-width: 0;
    }

    & > .agent-summary-permission {
        flex-shrink: 1000;
        min-width: 0;
    }
}

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

/* Coloured mode glyph before the permission label; the flex column-gap now
   provides the spacing to its neighbours. */
.summary-permission-icon {
    vertical-align: -0.15em;
}

/* Effort-level bars after the model label (replaces "× effort"). */
.effort-icon-wrap {
    position: relative;
}

.effort-icon {
    font-size: 1.1em;
    vertical-align: -0.15em;
}

.part-icon-wrap {
    position: relative;
}
</style>
