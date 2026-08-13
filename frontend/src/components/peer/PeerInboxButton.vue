<script setup>
/**
 * PeerInboxButton — badge entry point to the peer inbox.
 *
 * Sits next to the Settings button (ProjectView sidebar footer, HomeView).
 * Rendered ONLY while something actionable is pending (incoming requests or
 * inbound messages awaiting review) — so the badge is never zero and idle
 * instances don't grow chrome. History stays reachable through the peers
 * manager (Settings › Peers → Inbox).
 */
import { useId, computed } from 'vue'
import { usePeersStore } from '../../stores/peers'
import AppTooltip from '../ui/AppTooltip.vue'

const peersStore = usePeersStore()
const buttonId = useId()

const count = computed(() => peersStore.inboxCount)
const visible = computed(() => count.value > 0)

function openInbox() {
    window.dispatchEvent(new CustomEvent('twicc:open-peer-inbox'))
}
</script>

<template>
    <template v-if="visible">
        <wa-button
            :id="buttonId"
            class="peer-inbox-button"
            variant="neutral"
            appearance="filled-outlined"
            size="small"
            @click="openInbox"
        >
            <wa-icon name="envelope"></wa-icon>
            <wa-badge v-if="count > 0" variant="brand" class="peer-inbox-badge">{{ count }}</wa-badge>
        </wa-button>
        <AppTooltip :for="buttonId">Peer inbox</AppTooltip>
    </template>
</template>

<style scoped>
.peer-inbox-button {
    position: relative;
}
.peer-inbox-button::part(base) {
    inline-size: var(--wa-form-control-height);
    padding-inline: 0;
}
.peer-inbox-badge {
    position: absolute;
    inset-block-start: 0;
    inset-inline-end: 0;
    translate: 30% -30%;
    box-sizing: border-box;
    inline-size: 1.4rem;
    block-size: 1.4rem;
    padding: 0;
    border-radius: 50%;
    font-variant-numeric: tabular-nums;
    pointer-events: none;
}

/* Same footer degradation as CommandPaletteButton: drop out when the sidebar
   gets too narrow, below SettingsPopover's compact threshold. */
@container sidebar (width <= 9rem) {
    .peer-inbox-button { display: none; }
}
</style>
