<script setup>
// App-level host for all TerminalPanel-managed terminal instances. Each live
// terminal is rendered once here and <Teleport>ed into the slot of whichever
// panel currently displays it (by element ref, so a panel docked anywhere — or
// itself teleported into a dock — still works). Unclaimed instances stay alive
// in the off-screen holder. See stores/terminalPool.js. Mounted once in App.vue.
//
// Hybrid CLI terminals ('h:' context) are NOT pooled — they live inline in
// HybridTerminalBlock and never participate in attach/move.
import { computed } from 'vue'
import { useTerminalPoolStore } from '../../stores/terminalPool'
import TerminalInstance from './TerminalInstance.vue'

const pool = useTerminalPoolStore()
const keys = computed(() => Object.keys(pool.descriptors))
</script>

<template>
    <div class="terminal-pool-holder" aria-hidden="true">
        <template v-for="key in keys" :key="key">
            <Teleport :to="pool.targets[key]" :disabled="!pool.targets[key]">
                <TerminalInstance
                    :pool-key="key"
                    :context-key="pool.descriptors[key].contextKey"
                    :session-id="pool.descriptors[key].sessionId"
                    :project-id="pool.descriptors[key].projectId"
                    :cwd="pool.descriptors[key].cwd"
                    :terminal-index="pool.descriptors[key].index"
                    :active="!!pool.targets[key] && !!pool.activeFlags[key]"
                    :start-mode="pool.descriptors[key].startMode || 'auto'"
                    @disconnected="pool.onExit(key)"
                />
            </Teleport>
        </template>
    </div>
</template>

<style scoped>
/* Off-screen parking area for terminals that aren't currently displayed in any
   panel (kept alive). Given a stable size so a parked xterm has sane dimensions
   instead of collapsing to 0. Teleported-out instances live in their panel slot
   and are unaffected by these rules. */
.terminal-pool-holder {
    position: fixed;
    left: -100000px;
    top: 0;
    width: 1000px;
    height: 700px;
    overflow: hidden;
    pointer-events: none;
}
.terminal-pool-holder :deep(.terminal-area) {
    position: absolute;
    inset: 0;
}
</style>
