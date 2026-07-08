<script setup>
import { computed } from 'vue'
import ShareItemsList from './ShareItemsList.vue'

const props = defineProps({ stack: { type: Array, required: true } })
const emit = defineEmits(['close', 'clear'])
const current = computed(() => props.stack[props.stack.length - 1])
</script>

<template>
    <div class="subagent-drawer">
        <div class="subagent-backdrop" @click="emit('clear')"></div>
        <div class="subagent-panel">
            <header class="subagent-head">
                <nav class="crumbs">
                    <span v-for="(id, i) in stack" :key="id" class="crumb">
                        Agent {{ i + 1 }}<span v-if="i < stack.length - 1"> ›</span>
                    </span>
                </nav>
                <wa-button size="small" appearance="plain" @click="emit('close')">
                    <wa-icon name="xmark"></wa-icon>
                </wa-button>
            </header>
            <ShareItemsList :key="current" :session-id="current" :parent-session-id="current" :last-line="100000" />
        </div>
    </div>
</template>

<style scoped>
.subagent-drawer { position: fixed; inset: 0; z-index: 20; }
.subagent-backdrop { position: absolute; inset: 0; background: rgba(0,0,0,.4); }
.subagent-panel { position: absolute; top: 0; right: 0; bottom: 0; width: min(52rem, 100%);
    background: var(--wa-color-surface-default); box-shadow: -4px 0 24px rgba(0,0,0,.3);
    display: flex; flex-direction: column; overflow: auto; }
.subagent-head { position: sticky; top: 0; display: flex; justify-content: space-between;
    align-items: center; padding: .5rem 1rem; background: var(--wa-color-surface-default); }
.crumbs { display: flex; gap: .35rem; font-size: var(--wa-font-size-s); color: var(--wa-color-text-quiet); }
</style>
