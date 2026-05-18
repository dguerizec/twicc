<script setup>
import { ref, computed, watch, onBeforeUnmount } from 'vue'
import { useTipsStore } from '../../stores/tips'
import { useSettingsStore } from '../../stores/settings'
import { renderMarkdown } from '../../utils/markdown'
import { stripFrontMatter } from '../../utils/frontMatter'
import { resolvePublicAssetUrl } from '../../utils/publicAsset'
import { TIP_COOLDOWN_MS } from '../../composables/useTipScheduler'

// CustomNotification automatically injects the Notivue item as the `item` prop.
// (See CustomNotification.vue line 103 : <component … :item="item" />)
const props = defineProps({
    item: { type: Object, required: true },
})

const tipsStore = useTipsStore()
const settings = useSettingsStore()

const tip = computed(() => {
    const k = tipsStore.currentToastTipKey
    if (!k) return null
    return { key: k, ...tipsStore.manifest[k] }
})

const bodyHtml = ref('')
const loading = ref(false)
const errored = ref(false)
const showAgainLater = ref(false)
const bodyCache = new Map()

const env = computed(() => ({
    platform: settings._isTouchDevice ? 'mobile' : 'desktop',
    os: settings.os,
    enabledProviders: settings.enabledProviders,
}))

const hasMoreCandidates = computed(() => {
    if (!tip.value) return false
    const candidates = tipsStore.getCandidates(env.value)
    return candidates.filter((c) => c.key !== tip.value.key).length > 0
})

async function loadBody(key) {
    if (bodyCache.has(key)) {
        bodyHtml.value = bodyCache.get(key)
        loading.value = false
        errored.value = false
        return
    }
    loading.value = true
    errored.value = false
    try {
        const r = await fetch(resolvePublicAssetUrl(`tips/${key}.md`))
        if (!r.ok) throw new Error(`HTTP ${r.status}`)
        const raw = await r.text()
        const body = stripFrontMatter(raw)
        const html = await renderMarkdown(body)
        const finalHtml = rewriteTipAssetUrls(html)
        bodyCache.set(key, finalHtml)
        bodyHtml.value = finalHtml
    } catch (e) {
        console.error('Failed to load tip', key, e)
        errored.value = true
    } finally {
        loading.value = false
    }
}

// Rewrite asset URLs inside a rendered tip body so authors can use any of
// the natural shapes — `/tips/foo.png`, `./foo.png`, or bare `foo.png` —
// and have them resolved to the runtime BASE_URL (`/` in dev, `/static/`
// in prod). Operates on parsed HTML rather than the markdown source so
// occurrences inside code blocks are left alone. External URLs (with a
// scheme, protocol-relative, anchors, or any other absolute path) are
// untouched. Covers both `<img src>` and `<a href>`.
function rewriteTipAssetUrls(html) {
    const tipsBase = resolvePublicAssetUrl('tips/')
    const tmp = document.createElement('div')
    tmp.innerHTML = html
    const rewriteAttr = (el, attr) => {
        const v = el.getAttribute(attr)
        if (!v) return
        if (/^[a-z][a-z0-9+.-]*:/i.test(v)) return   // scheme: http:, data:, mailto:, ...
        if (v.startsWith('//')) return                // protocol-relative
        if (v.startsWith('#')) return                 // in-page anchor
        if (v.startsWith('/tips/')) {
            el.setAttribute(attr, tipsBase + v.slice('/tips/'.length))
            return
        }
        if (v.startsWith('/')) return                 // other absolute path — leave alone
        if (v.startsWith('./')) {
            el.setAttribute(attr, tipsBase + v.slice(2))
            return
        }
        // Bare relative path: treat as relative to the tips folder.
        el.setAttribute(attr, tipsBase + v)
    }
    tmp.querySelectorAll('img').forEach((el) => rewriteAttr(el, 'src'))
    tmp.querySelectorAll('a').forEach((el) => rewriteAttr(el, 'href'))
    return tmp.innerHTML
}

// Commit the current tip's seen-state based on the checkbox.
// Called only on voluntary close / Next tip, never on display.
function commitState(key) {
    if (!key) return
    if (showAgainLater.value) {
        tipsStore.unmarkSeen(key)
    } else {
        tipsStore.markSeen(key)
    }
}

// React to currentToastTipKey changes : load new body, reset checkbox.
// No mark/unmark here — commit happens only on voluntary close / Next.
watch(() => tipsStore.currentToastTipKey, async (newKey) => {
    if (!newKey) return
    showAgainLater.value = false
    await loadBody(newKey)
}, { immediate: true })

// Tear down without re-committing (used after commitState has already run).
function teardown() {
    tipsStore.nextEligibleTime = Date.now() + TIP_COOLDOWN_MS
    tipsStore.currentToastTipKey = null
    props.item.clear()
}

function onClose() {
    commitState(tipsStore.currentToastTipKey)
    teardown()
}

// Catch-all for external dismissals — e.g. the user clicks Notivue's own
// "×" button (rendered by CustomNotification.vue), or the toast is cleared
// from elsewhere. teardown() has already run for our explicit paths
// (sets currentToastTipKey = null), so this only fires when the toast was
// dismissed externally without going through onClose / onNextTip's "no
// more candidates" branch.
onBeforeUnmount(() => {
    const key = tipsStore.currentToastTipKey
    if (key === null) return   // dismissed via our own teardown() — nothing to do
    commitState(key)
    tipsStore.nextEligibleTime = Date.now() + TIP_COOLDOWN_MS
    tipsStore.currentToastTipKey = null
    // Do NOT call props.item.clear() here — the toast is already being
    // unmounted ; calling clear() again would be redundant (and Notivue may
    // not be happy about it).
})

function onNextTip() {
    const key = tipsStore.currentToastTipKey
    commitState(key)
    showAgainLater.value = false
    const candidates = tipsStore.getCandidates(env.value)
    const next = tipsStore.pickRandom(candidates, [key])
    if (!next) {
        teardown()
        return
    }
    tipsStore.currentToastTipKey = next.key
}
</script>

<template>
    <div class="tip-toast wa-invert" tabindex="-1" @keydown.esc="onClose">
        <header class="tip-header">
            <wa-tag size="medium" class="tip-badge" variant="brand">
                <wa-icon name="lightbulb" />
                Tip
            </wa-tag>
            <span class="tip-title">{{ tip?.title }}</span>
        </header>

        <wa-divider></wa-divider>

        <div v-if="loading" class="tip-loading">Loading…</div>
        <div v-else-if="errored" class="tip-error">Failed to load tip content.</div>
        <div v-else class="tip-body" v-html="bodyHtml" />

        <wa-divider></wa-divider>

        <footer class="tip-footer">
            <wa-switch
                :checked="showAgainLater"
                @change="showAgainLater = $event.target.checked"
                size="small"
            >
                Show again later
            </wa-switch>
            <wa-button v-if="hasMoreCandidates" size="small" @click="onNextTip">
                Next tip
                <wa-icon slot="end" name="chevron-right" />
            </wa-button>
        </footer>
    </div>
</template>

<style>
/* Notivue's .Notivue__content-message has a hardcoded max-height of 250px
   (notivue/dist/Notifications/notifications.css) which clips our toast's
   footer (checkbox + Next tip) on any tip whose body exceeds ~200px.
   Override for tip toasts only. The :has() selector targets the wrapper
   that contains our component root. The body keeps its own max-height so
   very long tips still scroll internally below the footer threshold. */
.Notivue__content-message:has(> .tip-toast) {
    max-height: none;
}

/* Hide Notivue's type-driven left icon (info/warning/error/...) on tip
   toasts only. We render our own lightbulb in the tip header and the
   framework icon is just visual noise here. `push[type]` requires a type,
   so we can't suppress the icon by passing none — CSS is the cleanest
   knob. `display: none` removes it from layout so the content flushes
   naturally against the left edge of the notification. */
.Notivue__notification:has(.tip-toast) .Notivue__icon {
    visibility: hidden;
}
</style>

<style scoped>
.tip-toast {
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
    min-width: 0;
}

.tip-header {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    font-weight: 600;
}

.tip-badge {
    gap: 0.25rem;
}

.tip-title {
    flex: 1;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}

.tip-body {
    max-height: 60vh;
    overflow-y: auto;
}

.tip-body :deep(img) {
    max-width: 100%;
    height: auto;
}

.tip-footer {
    display: flex;
    align-items: center;
    justify-content: space-between;
}

wa-divider {
    --spacing: 0.375rem;
}

.tip-loading,
.tip-error {
    padding: 0.5rem 0;
    font-style: italic;
    color: var(--wa-color-neutral-on-quiet, #888);
}
</style>
