<script setup>
import { ref } from 'vue'
import { readRecentShares, removeRecentShare } from '../share-recent/recordView'

const entries = ref(readRecentShares())

function open(token) { window.location.assign(`/share/${token}/`) }
function remove(token) { removeRecentShare(token); entries.value = readRecentShares() }
function when(iso) { try { return new Date(iso).toLocaleString() } catch { return '' } }
</script>

<template>
    <main class="share-recent">
        <h1>Shared with you</h1>
        <p v-if="!entries.length" class="empty">No shared links opened on this browser yet.</p>
        <ul v-else>
            <li v-for="e in entries" :key="e.token">
                <button class="row" type="button" @click="open(e.token)">
                    <wa-tag size="small">{{ e.kind }}</wa-tag>
                    <span class="title">{{ e.title || e.token }}</span>
                    <span class="when">{{ when(e.lastAccess) }}</span>
                </button>
                <wa-button size="small" appearance="plain" title="Remove" @click="remove(e.token)">
                    <wa-icon name="xmark"></wa-icon>
                </wa-button>
            </li>
        </ul>
    </main>
</template>

<style scoped>
.share-recent { max-width: 640px; margin: 3rem auto; padding: 0 1rem; }
h1 { font-size: 1.4rem; margin-bottom: 1rem; }
.empty { color: var(--wa-color-text-quiet, #888); }
ul { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: .3rem; }
li { display: flex; align-items: center; gap: .5rem; }
.row { flex: 1; display: flex; align-items: center; gap: .6rem; background: none; border: 1px solid var(--wa-color-surface-border, #333); border-radius: 8px; padding: .55rem .7rem; color: inherit; cursor: pointer; text-align: left; }
.row:hover { background: var(--wa-color-surface-raised, rgba(255,255,255,.04)); }
.title { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.when { color: var(--wa-color-text-quiet, #888); font-size: .8rem; }
</style>
