<script setup>
// NotificationSettings.vue - Notification settings section for the settings popover
// Two notification types: User Turn and Pending Request
// Each has a sound selector (with test button) and a browser notification toggle.
// Plus the external notification targets (Apprise URLs, pushed by the backend).

import { computed, ref, watch } from 'vue'
import { useSettingsStore } from '../../stores/settings'
import { playNotificationSound, getAvailableSoundOptions, NOTIFICATION_SOUNDS } from '../../utils/notificationSounds'

const store = useSettingsStore()

// Sound options for selects
const soundOptions = getAvailableSoundOptions()

// Computed from store
const userTurnSound = computed(() => store.getNotifUserTurnSound)
const userTurnBrowser = computed(() => store.isNotifUserTurnBrowser)
const pendingRequestSound = computed(() => store.getNotifPendingRequestSound)
const pendingRequestBrowser = computed(() => store.isNotifPendingRequestBrowser)

// External notification targets (synced settings, consumed by the backend).
// The UI edits a local working list (``externalRows``) so that rows with an
// empty URL (just added, or cleared) exist on screen without ever being
// persisted: ``persistExternalRows()`` writes only the non-empty ones to the
// store. Remote updates (another device) reset the working list.
const externalRows = ref((store.getExternalNotificationTargets || []).map(t => ({ ...t })))
const publicBaseUrl = computed(() => store.getPublicBaseUrl || '')

// Transient per-row state (keyed by row index; cleared on list mutations):
// in-flight tests, last test error, and the URL as currently typed.
// ``liveUrls`` exists because rows only update on the input's change event
// (blur/Enter) — the Test button must react to the value being typed or
// pasted before it is committed.
const testingTargets = ref({})
const targetTestErrors = ref({})
const liveUrls = ref({})

function persistExternalRows() {
    store.setExternalNotificationTargets(externalRows.value.filter(r => r.url).map(r => ({ ...r })))
}

watch(() => store.getExternalNotificationTargets, (remote) => {
    // Ignore the echo of our own persist; reset the working list (and the
    // index-keyed transient maps) on genuine remote changes.
    const persisted = externalRows.value.filter(r => r.url)
    if (JSON.stringify(remote || []) !== JSON.stringify(persisted)) {
        externalRows.value = (remote || []).map(t => ({ ...t }))
        testingTargets.value = {}
        targetTestErrors.value = {}
        liveUrls.value = {}
    }
})

function effectiveTargetUrl(index) {
    return liveUrls.value[index] ?? externalRows.value[index]?.url ?? ''
}

function addExternalTarget() {
    // Local draft row only — persisted once it gets a non-empty URL.
    externalRows.value.push({ url: '', enabled: true, tested: null, notifyUserTurn: true, notifyPendingRequest: true })
}

function removeExternalTarget(index) {
    externalRows.value.splice(index, 1)
    persistExternalRows()
    // The transient maps are keyed by row index, which just shifted: drop
    // them wholesale rather than showing state on the wrong rows. An
    // in-flight test still lands correctly (it re-checks the URL on return).
    targetTestErrors.value = {}
    testingTargets.value = {}
    liveUrls.value = {}
}

function onExternalTargetUrlInput(index, event) {
    liveUrls.value[index] = event.target.value.trim()
}

function onExternalTargetUrlChange(index, event) {
    // Editing the URL invalidates the last test result: a stale checkmark
    // must never vouch for a URL it didn't test.
    externalRows.value[index] = { ...externalRows.value[index], url: event.target.value.trim(), tested: null }
    persistExternalRows()
    delete liveUrls.value[index]
    delete targetTestErrors.value[index]
}

function onExternalTargetEnabledChange(index, event) {
    externalRows.value[index] = { ...externalRows.value[index], enabled: event.target.checked }
    persistExternalRows()
}

function onExternalTargetEventChange(index, key, event) {
    externalRows.value[index] = { ...externalRows.value[index], [key]: event.target.checked }
    persistExternalRows()
}

async function testExternalTarget(index) {
    const url = effectiveTargetUrl(index)
    if (!url || testingTargets.value[index]) return
    // Commit the typed-but-not-blurred URL first, so the tested outcome can
    // be persisted on the row it actually vouches for.
    if (url !== externalRows.value[index]?.url) {
        externalRows.value[index] = { ...externalRows.value[index], url, tested: null }
        persistExternalRows()
    }
    delete liveUrls.value[index]
    testingTargets.value[index] = true
    delete targetTestErrors.value[index]
    let ok = false
    let error = null
    try {
        const response = await fetch('/api/external-notifications/test/', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ urls: [url] }),
        })
        const data = await response.json()
        if (!response.ok) {
            error = data?.error || `Request failed (${response.status})`
        } else {
            const result = data.results?.[0]
            ok = !!result?.ok
            error = result?.error || null
        }
    } catch (e) {
        error = e?.message || 'Network error'
    } finally {
        delete testingTargets.value[index]
    }
    // Persist the outcome on the target (synced across devices) — unless the
    // URL changed while the test was in flight.
    if (externalRows.value[index]?.url === url) {
        externalRows.value[index] = { ...externalRows.value[index], tested: ok }
        persistExternalRows()
    }
    if (error) targetTestErrors.value[index] = error
}

function externalTestIcon(target) {
    if (target.tested === true) return 'check'
    if (target.tested === false) return 'xmark'
    return 'paper-plane'
}

function onPublicBaseUrlChange(event) {
    store.setPublicBaseUrl(event.target.value)
}

// Browser notification permission state
const browserNotifPermission = ref(getBrowserNotifPermission())

function getBrowserNotifPermission() {
    if (!('Notification' in window)) return 'unsupported'
    return Notification.permission
}

// Event handlers
function onUserTurnSoundChange(event) {
    store.setNotifUserTurnSound(event.target.value)
}

function onPendingRequestSoundChange(event) {
    store.setNotifPendingRequestSound(event.target.value)
}

function onUserTurnBrowserChange(event) {
    store.setNotifUserTurnBrowser(event.target.checked)
}

function onPendingRequestBrowserChange(event) {
    store.setNotifPendingRequestBrowser(event.target.checked)
}

function testUserTurnSound() {
    playNotificationSound(userTurnSound.value)
}

function testPendingRequestSound() {
    playNotificationSound(pendingRequestSound.value)
}

/**
 * Request browser notification permission.
 * Once granted, the switches become usable.
 */
async function requestPermission() {
    if (!('Notification' in window)) return
    try {
        const permission = await Notification.requestPermission()
        browserNotifPermission.value = permission
    } catch (e) {
        console.warn('Failed to request notification permission:', e)
    }
}

/**
 * Called when the parent popover opens — refresh browser permission state.
 */
function sync() {
    browserNotifPermission.value = getBrowserNotifPermission()
}

defineExpose({ sync })
</script>

<template>
    <section class="settings-section">
        <h3 class="settings-section-title">Notifications</h3>

        <!-- Browser notification permission (shown once for both) -->
        <div v-if="browserNotifPermission === 'unsupported'" class="notif-permission-row">
            <span class="setting-group-hint">Browser notifications not supported</span>
        </div>
        <div v-else-if="browserNotifPermission === 'denied'" class="notif-permission-row">
            <span class="setting-group-hint notif-denied">Browser notifications blocked</span>
        </div>
        <div v-else-if="browserNotifPermission === 'default'" class="notif-permission-row">
            <wa-button
                variant="neutral"
                appearance="outlined"
                size="small"
                @click="requestPermission"
            >Enable browser notifications</wa-button>
        </div>

        <!-- Agent finished working -->
        <div class="setting-group">
            <label class="setting-group-label">Agent finished working</label>
            <wa-select
                :value.prop="userTurnSound"
                @change="onUserTurnSoundChange"
                size="small"
            >
                <wa-option
                    v-for="opt in soundOptions"
                    :key="opt.value"
                    :value="opt.value"
                >{{ opt.label }}</wa-option>
            </wa-select>
            <a v-if="userTurnSound !== 'none'" href="#" class="notif-test-link" @click.prevent="testUserTurnSound">
                <wa-icon name="volume-up"></wa-icon> Test sound
            </a>
            <div v-if="browserNotifPermission === 'granted'" class="notif-browser-row">
                <wa-switch
                    :checked="userTurnBrowser"
                    @change="onUserTurnBrowserChange"
                    size="small"
                >Browser notification</wa-switch>
            </div>
        </div>

        <!-- Agent needs your attention -->
        <div class="setting-group">
            <label class="setting-group-label">Agent needs your attention</label>
            <wa-select
                :value.prop="pendingRequestSound"
                @change="onPendingRequestSoundChange"
                size="small"
            >
                <wa-option
                    v-for="opt in soundOptions"
                    :key="opt.value"
                    :value="opt.value"
                >{{ opt.label }}</wa-option>
            </wa-select>
            <a v-if="pendingRequestSound !== 'none'" href="#" class="notif-test-link" @click.prevent="testPendingRequestSound">
                <wa-icon name="volume-up"></wa-icon> Test sound
            </a>
            <div v-if="browserNotifPermission === 'granted'" class="notif-browser-row">
                <wa-switch
                    :checked="pendingRequestBrowser"
                    @change="onPendingRequestBrowserChange"
                    size="small"
                >Browser notification</wa-switch>
            </div>
        </div>

        <!-- External notifications (Apprise) -->
        <wa-divider></wa-divider>
        <div class="setting-group">
            <label class="setting-group-label">Push to your devices <wa-icon name="cloud" class="synced-icon"></wa-icon></label>
            <p class="setting-group-hint">
                Forward these notifications to external services (ntfy, Pushover, Telegram, …)
                using <a href="https://appriseit.com" target="_blank" rel="noopener">Apprise URLs</a>,
                e.g. <code>ntfy://ntfy.sh/your-secret-topic</code> —
                the <a href="https://appriseit.com/url-builder/" target="_blank" rel="noopener">URL builder</a>
                can compose one for your service.
            </p>
            <wa-input
                size="small"
                label="Public base URL"
                placeholder="https://twicc.example.com"
                :value.prop="publicBaseUrl"
                @change="onPublicBaseUrlChange"
            ></wa-input>
            <p class="setting-group-hint">
                Where you reach TwiCC from your devices — used to add a session link
                in notifications. Leave empty to omit the link.
            </p>
            <label class="setting-group-label">Targets</label>
            <div class="apprise-targets">
                <div v-for="(target, index) in externalRows" :key="index" class="apprise-target">
                    <div class="apprise-target-row">
                        <wa-input
                            class="apprise-url-input"
                            size="small"
                            placeholder="ntfy://ntfy.sh/your-secret-topic"
                            :value.prop="target.url"
                            @input="onExternalTargetUrlInput(index, $event)"
                            @change="onExternalTargetUrlChange(index, $event)"
                        ></wa-input>
                        <div class="apprise-target-controls">
                            <wa-button
                                size="small"
                                appearance="outlined"
                                :class="{ 'apprise-test-ok': target.tested === true, 'apprise-test-fail': target.tested === false }"
                                :disabled="!effectiveTargetUrl(index) || !!testingTargets[index]"
                                title="Send a test notification to this target"
                                @click="testExternalTarget(index)"
                            >
                                <wa-spinner v-if="testingTargets[index]" slot="start"></wa-spinner>
                                <wa-icon v-else slot="start" :name="externalTestIcon(target)"></wa-icon>
                                Test
                            </wa-button>
                            <wa-switch
                                class="apprise-enabled-switch"
                                size="small"
                                :checked="target.enabled"
                                title="Enabled"
                                @change="onExternalTargetEnabledChange(index, $event)"
                            ></wa-switch>
                            <wa-button
                                class="apprise-remove-button"
                                size="small"
                                appearance="plain"
                                variant="danger"
                                title="Remove this target"
                                @click="removeExternalTarget(index)"
                            >
                                <wa-icon name="trash"></wa-icon>
                            </wa-button>
                        </div>
                    </div>
                    <div class="apprise-target-events">
                        <wa-switch
                            size="small"
                            :checked="target.notifyUserTurn !== false"
                            @change="onExternalTargetEventChange(index, 'notifyUserTurn', $event)"
                        >Finished working</wa-switch>
                        <wa-switch
                            size="small"
                            :checked="target.notifyPendingRequest !== false"
                            @change="onExternalTargetEventChange(index, 'notifyPendingRequest', $event)"
                        >Needs attention</wa-switch>
                    </div>
                    <div v-if="targetTestErrors[index]" class="apprise-test-error">{{ targetTestErrors[index] }}</div>
                </div>
            </div>
            <wa-button size="small" appearance="outlined" @click="addExternalTarget">
                <wa-icon slot="start" name="plus"></wa-icon> Add target
            </wa-button>
            <p v-if="externalRows.length" class="setting-group-hint">
                Notifications are only sent to targets whose last test succeeded
                (green check). A green check means the service accepted the test
                message — confirm it actually arrived on your device.
            </p>
        </div>
    </section>
</template>

<style scoped>
.notif-test-link {
    display: inline-flex;
    align-items: center;
    gap: var(--wa-space-2xs);
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-brand-60);
    text-decoration: none;
    cursor: pointer;

    &:hover {
        text-decoration: underline;
    }

    wa-icon {
        font-size: var(--wa-font-size-xs);
    }
}

.notif-permission-row {
    padding-bottom: var(--wa-space-2xs);
}

.notif-browser-row {
    padding-left: var(--wa-space-2xs);
}

.notif-denied {
    color: var(--wa-color-danger-60);
}

.synced-icon {
    color: var(--wa-color-brand);
}

.apprise-targets {
    /* Size container for the narrow-layout query below. */
    container-type: inline-size;
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-s);
    margin-block: var(--wa-space-xs);
}

.apprise-target {
    margin-bottom: var(--wa-space-m);
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-xs);
}

/* space-between centers the enabled switch in the space left between the
   (input + Test) group and the remove button. */
.apprise-target-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    flex-wrap: wrap;
    column-gap: var(--wa-space-m);
    row-gap: var(--wa-space-xs);
}

.apprise-url-input {
    flex: 1;
    min-width: 8rem;
    font-family: var(--wa-font-family-code);
}

.apprise-remove-button::part(base) {
    xpadding-inline: var(--wa-space-2xs);
}

.apprise-target-controls {
    display: flex;
    align-items: center;
    gap: var(--wa-space-2xs);
    justify-content: space-between;
    margin-left: auto;
}

.apprise-enabled-switch {
    padding-left: 1em;
}

.apprise-test-ok wa-icon {
    color: var(--wa-color-success-60);
}

.apprise-test-fail wa-icon {
    color: var(--wa-color-danger-60);
}

.apprise-target-events {
    display: flex;
    gap: var(--wa-space-m);
    margin-block-start: var(--wa-space-2xs);
    padding-inline-start: var(--wa-space-s);
    flex-wrap: wrap;
    justify-content: flex-end;
}

.apprise-test-error {
    margin-top: var(--wa-space-3xs);
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-danger-60);
}
</style>
