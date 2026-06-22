<script setup>
// Settings → Help panel. Sibling of TipsSettings.vue. Lists every help page
// available for the current environment and lets the user reopen any of them
// (always without the "Don't show this again" switch — manual opens never
// gate). There is intentionally no enable/disable toggle and no reset button:
// help is a light, automatic nudge, not something to configure.
import { computed } from 'vue'
import { useHelpStore } from '../../stores/help'
import { useSettingsStore } from '../../stores/settings'
import { formatRelative } from '../../utils/date'
import { showHelp } from '../help/showHelp'

const helpStore = useHelpStore()
const settings = useSettingsStore()

const env = computed(() => ({
    platform: settings._isTouchDevice ? 'mobile' : 'desktop',
    os: settings.os,
    enabledProviders: settings.enabledProviders,
}))

const availableHelp = computed(() => {
    return helpStore.getAvailableHelp(env.value).sort((a, b) => a.title.localeCompare(b.title))
})

function isSeen(key) {
    return key in helpStore.seenHelp
}

function statusIcon(item) {
    return isSeen(item.key) ? 'check' : 'circle'
}

function statusLabel(item) {
    const iso = helpStore.seenHelp[item.key]
    if (!iso) return 'Not shown yet'
    const ms = Date.parse(iso)
    if (Number.isNaN(ms)) return 'Seen'
    return `Seen ${formatRelative(ms)}`
}

function onClickHelp(key) {
    // Close the Settings popover before opening the dialog, like the tips
    // list does — SettingsPopover.vue listens for this event and hides.
    window.dispatchEvent(new CustomEvent('twicc:close-settings-popover'))
    // Opened from the list for re-reading — never with the dismiss switch.
    // Defer one tick so the popover starts closing before the dialog opens.
    setTimeout(() => showHelp(key, { showDontShowAgain: false }), 0)
}
</script>

<template>
    <div class="help-settings">
        <p class="help-hint">
            Help pages open by themselves the first time you reach a feature,
            and you can reopen any of them from the list below. There are only
            a few for now — the list will grow as the app evolves.
        </p>

        <h4 class="help-list-title">All help</h4>

        <p v-if="availableHelp.length === 0" class="help-empty">
            No help available yet.
        </p>

        <ul v-else class="help-list">
            <li
                v-for="item in availableHelp"
                :key="item.key"
                class="help-row"
                :class="{ seen: isSeen(item.key) }"
                tabindex="0"
                @click="onClickHelp(item.key)"
                @keydown.enter="onClickHelp(item.key)"
            >
                <wa-icon :name="statusIcon(item)" class="help-status" />
                <div class="help-content">
                    <div class="help-row-title">{{ item.title }}</div>
                    <div class="help-row-sub">{{ statusLabel(item) }}</div>
                </div>
            </li>
        </ul>
    </div>
</template>

<style scoped>
.help-settings {
    display: flex;
    flex-direction: column;
    gap: 0.75rem;
}

.help-hint {
    margin: 0;
    font-size: 0.85em;
    color: var(--wa-color-neutral-on-quiet, #888);
}

.help-list-title {
    margin: 0.5rem 0 0;
    font-size: 0.95em;
    font-weight: 600;
}

.help-list {
    display: flex;
    flex-direction: column;
    gap: 0.25rem;
    list-style: none;
    padding: 0;
    margin: 0;
}

.help-row {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    padding: 0.5rem;
    border-radius: 0.25rem;
    cursor: pointer;
    background-color: var(--wa-color-surface-lowered, transparent);
}

.help-row:hover,
.help-row:focus-visible {
    background-color: var(--wa-color-surface-default, #eee);
    outline: none;
}

.help-row.seen .help-row-title {
    color: var(--wa-color-neutral-on-quiet, #888);
}

.help-content {
    flex: 1;
    min-width: 0;
}

.help-row-title {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}

.help-row-sub {
    font-size: 0.8em;
    color: var(--wa-color-neutral-on-quiet, #888);
}

.help-empty {
    margin: 0.5rem 0;
    font-style: italic;
    color: var(--wa-color-neutral-on-quiet, #888);
}
</style>
