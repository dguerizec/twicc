<script setup>
// A boolean agent-setting flag (thinking / fast mode / Chrome MCP) rendered as
// an icon: tinted in its brand colour when on, dimmed with a diagonal "/"
// strike when off. Shared by the settings summary strip and the defaults
// selects so the on/off styling stays identical. Glyph + tint come from
// AGENT_SETTING_ICONS; renders nothing for a field without an entry.
import { computed } from 'vue'
import { AGENT_SETTING_ICONS } from '../../utils/agentSettingIcons'

const props = defineProps({
    field: { type: String, required: true },
    on: { type: Boolean, default: false },
    // Accessible label for the underlying wa-icon (optional).
    label: { type: String, default: null },
})

const info = computed(() => AGENT_SETTING_ICONS[props.field] ?? null)
</script>

<template>
    <span v-if="info" class="flag-icon-wrap" :class="{ struck: !on }">
        <wa-icon
            auto-width
            :name="info.icon || undefined"
            :src="info.src || undefined"
            :family="info.family || undefined"
            :label="label || undefined"
            class="flag-icon"
            :class="{ dimmed: !on }"
            :style="on ? { color: info.color } : null"
        ></wa-icon>
    </span>
</template>

<style scoped>
.flag-icon-wrap {
    position: relative;
}

/* Off flags stay visible but dimmed. */
.flag-icon.dimmed {
    opacity: 0.4;
}

/* Off flag also gets a "/" slash across it — the icon stays dimmed while the
   slash keeps the normal text colour so the disabled state pops. */
.flag-icon-wrap.struck::after {
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
