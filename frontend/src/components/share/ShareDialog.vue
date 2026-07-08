<script setup>
import { ref, reactive, computed, watch, nextTick } from 'vue'
import { useSharesStore } from '../../stores/shares'
import { useSettingsStore } from '../../stores/settings'
import { shareAbsoluteUrl } from '../../utils/shareUrl'
import { toast } from '../../composables/useToast'

const props = defineProps({
    open: Boolean,
    kind: { type: String, required: true },         // 'session' | 'artifact'
    sessionId: { type: String, default: null },
    bookmarkId: { type: Number, default: null },
    allowedHosts: { type: Object, default: () => ({}) },  // artifact: hosts viewers reach
    defaultTitle: { type: String, default: '' },     // real session title / bookmark name (placeholder)
    edit: { type: Object, default: null },          // existing serialized share when editing
})
const emit = defineEmits(['close'])
const shares = useSharesStore()
const settings = useSettingsStore()

const sharingEnabled = computed(() => !!settings.getShareBaseUrl)

const form = reactive({
    label: '', display_title: '', password: '', expires_at: '', notify_on_view: false,
    // session options
    mode: 'live', max_display_mode: 'normal', include_subagents: true,
    show_timestamps: true, show_title: true,
})
const error = ref('')
const createdUrl = ref('')
const dialogRef = ref(null)
const formId = 'share-dialog-form'

// `immediate` matters for the on-demand mounts (ProjectView's artifact/session
// dialog, ShareManagerDialog's edit dialog): they are v-if'd in at the same tick
// `open` becomes true, so the component mounts with `open` already true and a
// plain watcher never sees the false→true edge — leaving the form (title…)
// unseeded on the first open in a tab. The `if (o)` guard keeps a closed mount a
// no-op.
watch(() => props.open, (o) => { if (o) reset() }, { immediate: true })
function reset() {
    error.value = ''; createdUrl.value = ''
    const e = props.edit
    Object.assign(form, {
        // Pre-fill the title field with the real session title / bookmark name so it's
        // visible and editable; clearing it (session, title shown) falls back to the
        // live real title server-side.
        label: e?.label || '', display_title: e?.options?.display_title || props.defaultTitle || '',
        password: '', expires_at: e?.expires_at || '',
        notify_on_view: e?.notify_on_view || false,
        mode: e?.options?.mode || 'live',
        max_display_mode: e?.options?.max_display_mode || 'normal',
        include_subagents: e?.options?.include_subagents ?? true,
        show_timestamps: e?.options?.show_timestamps ?? true,
        show_title: e?.options?.show_title ?? true,
    })
}

const isSession = computed(() => props.kind === 'session')
const allowedHostList = computed(() => Object.keys(props.allowedHosts || {}))

function sessionOptions() {
    return {
        mode: form.mode, max_display_mode: form.max_display_mode,
        include_subagents: form.include_subagents,
        show_timestamps: form.show_timestamps, show_title: form.show_title,
    }
}

// Options sent for both kinds: session config (session only) + show_title (the
// master switch, both kinds) + the optional public title override. The title is
// only sent when "Show title" is on (off ⇒ the viewer sees the generic label);
// empty ⇒ omitted ⇒ viewers see the real session title / bookmark name.
function buildOptions() {
    const opts = isSession.value ? sessionOptions() : { show_title: form.show_title }
    const t = form.display_title.trim()
    if (t && form.show_title) opts.display_title = t
    return opts
}

async function handleSave() {
    error.value = ''
    if (!sharingEnabled.value) {
        error.value = 'Configure a share host in Settings → Sharing to create links.'
        return
    }
    try {
        if (props.edit) {
            const fields = { label: form.label.trim(), notify_on_view: form.notify_on_view, options: buildOptions() }
            if (form.password) fields.password = form.password
            fields.expires_at = form.expires_at || null
            const share = await shares.patchShare(props.edit.id, fields)
            createdUrl.value = shareAbsoluteUrl(share)
        } else {
            const body = {
                kind: props.kind, label: form.label.trim(),
                password: form.password || null, expires_at: form.expires_at || null,
                notify_on_view: form.notify_on_view, options: buildOptions(),
            }
            if (isSession.value) body.session_id = props.sessionId
            else body.bookmark_id = props.bookmarkId
            const share = await shares.createShare(body)
            createdUrl.value = shareAbsoluteUrl(share)
        }
    } catch (e) {
        error.value = (e?.errors?.[0]?.message) || e?.reason || e?.error || 'Failed to save share'
    }
}

function copyUrl() { navigator.clipboard.writeText(createdUrl.value); toast.success('Share URL copied') }

function onAfterShow(e) {
    // Guard bubbling wa-after-show from a nested wa-select/wa-switch panel: re-running
    // the focus logic on the dialog would steal focus from an opening dropdown and
    // snap it shut (the classic nested-WA-event trap).
    if (e.target !== dialogRef.value) return
    nextTick(() => {
        const submit = dialogRef.value?.querySelector(`button[type="submit"]`)
        submit?.setAttribute('form', formId)
        dialogRef.value?.querySelector('#share-label-input')?.focus()
    })
}
// Guard bubbling wa-hide from nested wa-select/wa-switch (only the dialog's own closes).
function onHide(e) { if (e.target === dialogRef.value) emit('close') }
</script>

<template>
    <wa-dialog ref="dialogRef" :open="open" :label="edit ? 'Edit share' : 'Create share'"
               style="--width: min(560px, calc(100vw - 2rem))"
               @wa-after-show="onAfterShow" @wa-hide="onHide">
        <form :id="formId" @submit.prevent="handleSave">
            <wa-callout v-if="error" variant="danger">{{ error }}</wa-callout>

            <wa-callout v-if="!sharingEnabled" variant="warning">
                No share host configured. Set one in Settings → Sharing before creating links.
            </wa-callout>

            <wa-callout variant="warning">
                Anyone with the link can read this {{ isSession ? 'transcript' : 'artifact' }} as-is,
                including file paths, commands and output. There is no redaction.
            </wa-callout>

            <label>Label (private)
                <wa-input id="share-label-input" :value="form.label"
                          @input="form.label = $event.target.value" placeholder="e.g. for Alice"></wa-input>
            </label>

            <label>Title (shown to viewers)
                <wa-input :value="form.display_title" @input="form.display_title = $event.target.value"
                          :placeholder="defaultTitle || 'Default title'"
                          :disabled="!form.show_title"></wa-input>
            </label>
            <wa-switch :checked="form.show_title"
                       @change.stop="form.show_title = $event.target.checked">
                Show title <span class="switch-hint">— off shows viewers a generic label</span>
            </wa-switch>

            <template v-if="isSession">
                <label>Snapshot / live
                    <wa-select :value="form.mode" @change.stop="form.mode = $event.target.value">
                        <wa-option value="live">Live (follows the session)</wa-option>
                        <wa-option value="snapshot">Snapshot (frozen now)</wa-option>
                    </wa-select>
                </label>
                <label>Max detail
                    <wa-select :value="form.max_display_mode" @change.stop="form.max_display_mode = $event.target.value">
                        <wa-option value="conversation">Conversation</wa-option>
                        <wa-option value="simplified">Simplified</wa-option>
                        <wa-option value="normal">Normal</wa-option>
                        <wa-option value="debug">Debug (raw JSON)</wa-option>
                    </wa-select>
                </label>
                <wa-switch :checked="form.include_subagents" @change.stop="form.include_subagents = $event.target.checked">Include subagents</wa-switch>
                <wa-switch :checked="form.show_timestamps" @change.stop="form.show_timestamps = $event.target.checked">Show timestamps</wa-switch>
            </template>

            <template v-else>
                <wa-callout v-if="allowedHostList.length" variant="neutral">
                    Viewers will be able to reach these hosts (already allowed on this artifact):
                    <ul><li v-for="h in allowedHostList" :key="h"><code>{{ h }}</code></li></ul>
                </wa-callout>
            </template>

            <label>Password (optional)
                <wa-input type="password" :value="form.password"
                          @input="form.password = $event.target.value"
                          :placeholder="edit?.has_password ? 'unchanged — type to replace' : 'no password'"></wa-input>
            </label>
            <label>Expires (optional)
                <wa-input type="datetime-local" :value="form.expires_at"
                          @input="form.expires_at = $event.target.value"></wa-input>
            </label>
            <wa-switch :checked="form.notify_on_view" @change.stop="form.notify_on_view = $event.target.checked">Notify me when viewed</wa-switch>

            <div v-if="createdUrl" class="share-url">
                <wa-input readonly :value="createdUrl"></wa-input>
                <wa-button @click.stop="copyUrl"><wa-icon slot="start" name="copy"></wa-icon>Copy</wa-button>
            </div>
        </form>
        <div slot="footer" class="dialog-footer">
            <wa-button @click="emit('close')">Close</wa-button>
            <wa-button type="submit" variant="brand" :form="formId" :disabled="!sharingEnabled">{{ edit ? 'Save' : 'Create link' }}</wa-button>
        </div>
    </wa-dialog>
</template>

<style scoped>
form {
    display: flex;
    flex-direction: column;
    row-gap: 1em;
}
label { display: block; font-size: var(--wa-font-size-s); font-weight: 600; }
label wa-input, label wa-select { margin-top: 0.3rem; font-weight: 400; }
wa-switch { display: block; }
.switch-hint { font-weight: 400; color: var(--wa-color-text-quiet); }
.share-url { display: flex; gap: 0.5rem; align-items: center; margin-top: 0.8rem; }
.share-url wa-input { flex: 1; }
.dialog-footer {
    display: flex;
    gap: var(--wa-space-s);
    justify-content: flex-end;
    width: 100%;
    align-items: center;
    flex-wrap: wrap;
}
</style>
