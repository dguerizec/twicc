<script setup>
import { ref, onMounted } from 'vue'
import MarkdownContent from '../components/ui/MarkdownContent.vue'

const props = defineProps({ tokenPath: String, meta: Object })
const source = ref('')
const error = ref(false)
const snapshotAt = ref(props.meta.snapshot_at)
const updated = ref(false)

async function load() {
    try {
        const res = await fetch(props.meta.docUrl, { credentials: 'same-origin' })
        if (!res.ok) throw new Error(String(res.status))
        source.value = await res.text()
    } catch { error.value = true }
}
onMounted(load)

// Poll snapshot freshness (D7) while visible.
onMounted(() => {
    setInterval(async () => {
        if (document.hidden) return
        try {
            const m = await (await fetch(`${props.tokenPath}/api/artifact-meta/`, { credentials: 'same-origin' })).json()
            if (m.snapshot_at && m.snapshot_at !== snapshotAt.value) updated.value = true
        } catch { /* ignore */ }
    }, 30000)
})
</script>

<template>
    <div class="share-doc">
        <wa-callout v-if="updated" variant="brand" class="share-banner">
            This artifact was updated — <a href="#" @click.prevent="location.reload()">Reload</a>
        </wa-callout>
        <wa-callout v-if="error" variant="danger">This document is not available.</wa-callout>
        <MarkdownContent v-else :source="source" :show-toolbar="false" />
        <footer class="share-footer">Shared with TwiCC</footer>
    </div>
</template>

<style>
.share-doc { max-width: 55rem; margin: 0 auto; padding: 1.5rem; }
</style>
