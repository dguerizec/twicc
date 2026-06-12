<script setup>
// SettingsPopover.vue - Settings button with popover panel
import { computed, nextTick, onBeforeUnmount, onMounted, ref, useId, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useSettingsStore, SETTINGS_SCHEMA } from '../../stores/settings'
import { useDataStore } from '../../stores/data'
import { useAuthStore } from '../../stores/auth'
import { useTipsStore } from '../../stores/tips'
import { getProviderHelpers, getProviderLabel, getProviderOptions, getRegisteredProviders, getProviderIcon } from '../../providers'
import { getActivationCharMetadata } from '../../utils/commandActivation'
import { DISPLAY_MODE, COLOR_SCHEME, SESSION_TIME_FORMAT, DEFAULT_MAX_CACHED_SESSIONS, WA_THEME, WA_THEME_LABELS, WA_BRAND, WA_BRAND_LABELS, SPONSOR_URL } from '../../constants'
import NotificationSettings from './NotificationSettings.vue'
import TipsSettings from '../settings/TipsSettings.vue'
import AppTooltip from '../ui/AppTooltip.vue'
import ChangelogDialog from './ChangelogDialog.vue'
import ProviderSettingsSection from './ProviderSettingsSection.vue'
import { sendChangelogSeen, sendValidateUsageDumpPath, sendValidateUsageFile, sendValidateTmuxConfigPath } from '../../composables/useWebSocket'
import { useProviderActivation } from '../../composables/useProviderActivation'
import { vPopoverFocusFix } from '../../directives/vPopoverFocusFix'

const router = useRouter()
const store = useSettingsStore()
const dataStore = useDataStore()
const authStore = useAuthStore()
const tipsStore = useTipsStore()

// Tips section is hidden from the nav (and the active-section watcher
// below redirects away from it) when no tip matches the current
// environment's constraints. Empty manifest, or all tips filtered out
// by platform / os / providers, → no entry. Reactive: re-evaluates when
// the manifest, the touch-device flag, the OS, or enabledProviders changes.
const availableTips = computed(() => tipsStore.getAvailableTips({
    platform: store._isTouchDevice ? 'mobile' : 'desktop',
    os: store.os,
    enabledProviders: store.enabledProviders,
}))
const hasTips = computed(() => availableTips.value.length > 0)

// Reactive set of currently enabled providers (derived from the settings store).
const enabledProviders = computed(() => new Set(store.enabledProviders))

// Show logout button only when password-based auth is active
const showLogout = computed(() => authStore.passwordRequired && authStore.authenticated)
const logoutButtonId = useId()

function handleLogout() {
    router.push({ name: 'logout' })
}

// -- Section navigation --

// Per-provider sections — one entry per registered provider, identified
// by ``provider_<key>`` so the activeSection check disambiguates them.
const providerSections = computed(() =>
    getRegisteredProviders().map(provider => {
        const helpers = getProviderHelpers(provider)
        const label = helpers.constructor.label ?? provider
        return {
            id: `provider_${provider}`,
            provider,
            label: `${label} settings`,
            navLabel: label,
            icon: getProviderIcon(provider),
            synced: true,
            enabled: enabledProviders.value.has(provider),
        }
    })
)

const sections = computed(() => [
    { id: 'global',                    label: 'Global' },
    { id: 'providers',                 label: 'Providers', synced: true },
    ...providerSections.value.filter(s => s.enabled),
    { id: 'notifications',             label: 'Notifications' },
    { id: 'sessions',      label: 'Sessions' },
    { id: 'title',         label: 'Title suggestion', navLabel: 'Titles', synced: true },
    { id: 'editor',        label: 'Editor' },
    { id: 'terminal',      label: 'Terminal' },
    { id: 'usage',         label: 'Providers quotas/usage', navLabel: 'Usage' },
])

const activeSection = ref('global')
const mobileShowContent = ref(false)
const popoverRef = ref(null)

// If the user is sitting on the Tips section when its nav entry
// disappears (e.g. they just toggled the last enabled provider that
// gated the only available tip), bounce them back to Global so the
// detail panel doesn't render an empty/orphaned TipsSettings.
watch(hasTips, (now) => {
    if (!now && activeSection.value === 'tips') {
        activeSection.value = 'global'
    }
})

function handleCloseRequest() {
    const el = popoverRef.value
    if (!el) return
    if (typeof el.hide === 'function') {
        el.hide()
    } else {
        // Fallback: toggle the open attribute. Web Awesome 3 wa-popover should
        // expose hide(); this is a defensive path.
        el.removeAttribute('open')
    }
}

onMounted(() => {
    window.addEventListener('twicc:close-settings-popover', handleCloseRequest)
})

onBeforeUnmount(() => {
    window.removeEventListener('twicc:close-settings-popover', handleCloseRequest)
})

const activeSectionObj = computed(() =>
    sections.value.find(s => s.id === activeSection.value)
)

const activeSectionLabel = computed(() => {
    if (activeSection.value === 'shortcuts') return 'Keyboard shortcuts'
    if (activeSection.value === 'tips') return 'Tips'
    return activeSectionObj.value?.label ?? ''
})

function selectSection(id) {
    activeSection.value = id
    mobileShowContent.value = true
    if (id === 'global') {
        worktreeDirInput.value = defaultWorktreeDirectory.value || ''
    }
    if (id === 'notifications') {
        nextTick(() => notificationSettingsRef.value?.sync())
    }
    if (id === 'usage') {
        // Seed local input/validation state from persisted values, one
        // entry per provider that tracksUsage.
        const fileInputs = {}
        const fileValidations = {}
        const dumpInputs = {}
        const dumpValidations = {}
        for (const provider of usageProviders.value) {
            fileInputs[provider] = getReadPath(provider)
            fileValidations[provider] = null
            dumpInputs[provider] = getDumpPath(provider)
            dumpValidations[provider] = null
        }
        usageFilePathInput.value = fileInputs
        usageFileValidation.value = fileValidations
        usageDumpPathInput.value = dumpInputs
        usageDumpValidation.value = dumpValidations
    }
    if (id === 'terminal') {
        tmuxConfigPathInput.value = terminalTmuxConfigPath.value || ''
        tmuxConfigValidation.value = null
    }
    if (id === 'title') {
        titleSystemPromptInput.value = titleSystemPrompt.value
    }
}

function goBackToNav() {
    mobileShowContent.value = false
}

// -- Keyboard shortcuts data --

// The session switcher cycles on the *physical* key above Tab (e.code
// 'Backquote'), which carries a different legend per layout (`` ` `` on QWERTY,
// `²` on AZERTY, `^` on QWERTZ…). Default to the QWERTY legend, then — on
// Chromium, the only engine exposing the Keyboard Map API — refine it to the
// actual character printed on the user's keyboard. Elsewhere the static label
// plus the "key above Tab" wording in the description keep it unambiguous.
const backquoteKeyLabel = ref('`')
onMounted(async () => {
    try {
        const layoutMap = await navigator.keyboard?.getLayoutMap?.()
        const label = layoutMap?.get('Backquote')
        if (label) backquoteKeyLabel.value = label
    } catch {
        // API unsupported or rejected — the QWERTY default stands on purpose.
    }
})

const shortcutGroups = computed(() => {
    const mod = store.isMac ? '⌘' : 'Ctrl'

    // Collect command activation chars across every enabled provider so
    // the cheat sheet stays in sync with what the message input actually
    // reacts to. Disabled providers are skipped — their activation chars
    // don't open the picker. A char shared by several providers (e.g.
    // ``/`` may be claimed by both Claude Code and Codex) collapses to a
    // single row whose description lists every provider that handles it.
    const charToProviderLabels = new Map()
    for (const provider of getRegisteredProviders()) {
        if (!enabledProviders.value.has(provider)) continue
        const helpers = getProviderHelpers(provider)
        const label = getProviderLabel(provider)
        for (const char of helpers?.getCommandActivationChars() ?? []) {
            if (!charToProviderLabels.has(char)) charToProviderLabels.set(char, [])
            const labels = charToProviderLabels.get(char)
            if (!labels.includes(label)) labels.push(label)
        }
    }

    const commandShortcuts = []
    for (const [char, labels] of charToProviderLabels.entries()) {
        const meta = getActivationCharMetadata(char)
        if (!meta) continue
        commandShortcuts.push({
            keys: [char],
            description: `${meta.tooltip} (at start of input — ${labels.join(', ')})`,
        })
    }

    return [
        {
            label: 'Global',
            shortcuts: [
                { keys: [mod, 'K'], description: 'Open command palette' },
                { keys: [mod, 'Shift', 'F'], description: 'Open full-text search' },
                // Always Ctrl (even on macOS, where ⌘+` is an OS shortcut), so these
                // deliberately don't use `mod`. The cycle key is the physical key
                // above Tab — backquoteKeyLabel resolves its legend per layout, and
                // the description names its position too.
                { keys: ['Ctrl', backquoteKeyLabel.value], description: 'Switch between recent sessions — hold Ctrl, tap the key above Tab (or ↑/↓) to cycle, release to switch' },
                { keys: ['Ctrl', 'Shift', backquoteKeyLabel.value], description: 'Same, through the sessions currently shown in the sidebar (tap Shift while open to toggle either source)' },
            ]
        },
        {
            label: 'Session tabs',
            shortcuts: [
                { keys: ['Alt', 'Shift', '1–4'], description: 'Jump to tab (Chat, Files, Git, Terminal)' },
                { keys: ['Alt', 'Shift', '←/→'], description: 'Previous / next tab' },
                { keys: ['Alt', 'Shift', '↑/↓'], description: 'Last visited tab' },
            ]
        },
        {
            label: 'Session chat',
            shortcuts: [
                { keys: ['Alt', 'Shift', 'M'], description: 'Focus message input — or the active pending request when one is open (from any session tab)' },
                { keys: ['Quick triple Esc'], description: 'Emergency stop of the running process' },
            ]
        },
        {
            label: 'Message input',
            shortcuts: [
                { keys: [mod, '↵'], description: 'Send message' },
                { keys: ['Alt', 'Shift', 'O'], description: 'Open / close Agent Settings popover' },
                { keys: ['@'], description: 'Insert file path (after a space or at start)' },
                ...commandShortcuts,
                { keys: ['!'], description: 'Message history (at start of input)' },
                { keys: ['PageUp'], description: 'Message history (cursor on first line)' },
            ]
        },
        {
            label: 'Pending request (approval / question)',
            shortcuts: [
                { keys: [mod, '↵'], description: 'Submit the form — Approve (or "Approve with changes" in edit mode, Submit for a question). Sends Deny / Cancel turn when that button is focused.' },
            ]
        },
        {
            label: 'Project home tabs',
            shortcuts: [
                { keys: ['Alt', 'Shift', '1–4'], description: 'Jump to tab (Stats, Files, Git, Terminal)' },
                { keys: ['Alt', 'Shift', '←/→'], description: 'Previous / next tab' },
                { keys: ['Alt', 'Shift', '↑/↓'], description: 'Last visited tab' },
            ]
        },
        {
            label: 'Terminal tabs',
            shortcuts: [
                { keys: ['Alt', 'Ctrl', 'Shift', '1–9'], description: 'Jump to terminal tab N' },
                { keys: ['Alt', 'Ctrl', 'Shift', '←/→'], description: 'Previous / next terminal tab' },
                { keys: ['Alt', 'Ctrl', 'Shift', '↑/↓'], description: 'Last visited terminal tab' },
            ]
        },
        {
            label: 'In-session search',
            shortcuts: [
                { keys: [mod, 'F'], description: 'Find in current session' },
                { keys: ['F3'], description: 'Next match (works without focus)' },
                { keys: ['Shift', 'F3'], description: 'Previous match (works without focus)' },
            ]
        },
        {
            label: 'Files / Git editor',
            shortcuts: [
                { keys: ['Alt', 'E'], description: 'Toggle edit mode for the current file' },
            ]
        },
        {
            label: 'Terminal',
            shortcuts: [
                { keys: ['Ctrl', 'C'], description: 'Copy selected text (instead of SIGINT)' },
                { keys: ['Ctrl', 'Shift', 'C'], description: 'Copy selected text' },
                { keys: ['Ctrl', 'D'], description: 'Send EOF / disconnect' },
            ]
        },
    ]
})

// WA theme/palette/brand options
const waThemeOptions = Object.values(WA_THEME).map(value => ({
    value,
    label: WA_THEME_LABELS[value],
}))

const waBrandOptions = Object.values(WA_BRAND).map(value => ({
    value,
    label: WA_BRAND_LABELS[value],
}))

// Color scheme options for the select
const colorSchemeOptions = [
    { value: COLOR_SCHEME.SYSTEM, label: 'System' },
    { value: COLOR_SCHEME.LIGHT, label: 'Light' },
    { value: COLOR_SCHEME.DARK, label: 'Dark' },
]

// Session time format options for the select
const sessionTimeFormatOptions = [
    { value: SESSION_TIME_FORMAT.TIME, label: 'Time' },
    { value: SESSION_TIME_FORMAT.RELATIVE_SHORT, label: 'Relative (short)' },
    { value: SESSION_TIME_FORMAT.RELATIVE_NARROW, label: 'Relative (narrow)' },
]

const notificationSettingsRef = ref(null)
const changelogDialogRef = ref(null)
const forcedChangelogOpen = ref(false)

// Settings from store
const defaultProvider = computed(() => store.getDefaultProvider)
const providerOptions = getProviderOptions()
const enabledProviderOptions = computed(() =>
    providerOptions.filter(opt => enabledProviders.value.has(opt.value))
)
function providerIconFor(provider) {
    return getProviderIcon(provider)
}

// ─── Provider activation helpers ─────────────────────────────────────

const { canDisableProvider, canEnableProvider, disableReasonFor, setProviderEnabled } = useProviderActivation()

function providerLabelFor(p) {
    return getProviderLabel(p)
}
function providerStateFor(p) {
    return dataStore.getProviderState(p)
}
function isSwitchDisabled(p) {
    // Enabled providers gate on the full disable check (transition, last,
    // active sessions). Disabled providers only gate on the transition.
    return enabledProviders.value.has(p) ? !canDisableProvider(p) : !canEnableProvider(p)
}
function reasonFor(p) {
    // Only enabled providers can be blocked from a disable attempt —
    // disabled providers either re-enable instantly or are gated by a
    // transition (shown as a spinner label, not a danger hint).
    if (!enabledProviders.value.has(p)) return null
    return disableReasonFor(p)
}
function transitionLabelFor(p) {
    const state = providerStateFor(p)
    if (state === 'starting') return 'Starting'
    if (state === 'stopping') return 'Stopping'
    return null
}
function onToggleProvider(p, event) {
    setProviderEnabled(p, event.target.checked)
}
const displayMode = computed(() => store.getDisplayMode)
const fontSize = computed(() => store.getFontSize)
const colorScheme = computed(() => store.getColorScheme)
const sessionTimeFormat = computed(() => store.getSessionTimeFormat)
const showCosts = computed(() => store.areCostsShown)
const extraUsageOnlyWhenNeeded = computed(() => store.isExtraUsageOnlyWhenNeeded)
// Same synced setting as the one in the Notifications section — mirrored here
// so a user browsing the Usage section doesn't miss the feature.
const notifyOnExtraUsageStart = computed(() => store.shouldNotifyOnExtraUsageStart)
const maxCachedSessions = computed(() => store.getMaxCachedSessions)
const autoUnpinOnArchive = computed(() => store.isAutoUnpinOnArchive)
const titleGenerationEnabled = computed(() => store.isTitleGenerationEnabled)
const titleAutoApply = computed(() => store.isTitleAutoApply)
const titleSystemPrompt = computed(() => store.getTitleSystemPrompt)
const titleSystemPromptInput = ref('')
const terminalUseTmux = computed(() => store.isTerminalUseTmux)
const terminalTmuxConfigPath = computed(() => store.getTerminalTmuxConfigPath)
const defaultWorktreeDirectory = computed(() => store.getDefaultWorktreeDirectory)
const compactSessionList = computed(() => store.isCompactSessionList)
const showMessageTimestamps = computed(() => store.areMessageTimestampsShown)
const waTheme = computed(() => store.getWaTheme)
const waBrand = computed(() => store.getWaBrand)
const showDiffs = computed(() => store.isShowDiffs)
const toolDiffWordWrap = computed(() => store.isToolDiffWordWrap)
const toolDiffSideBySide = computed(() => store.isToolDiffSideBySide)
const diffSideBySide = computed(() => store.isDiffSideBySide)
const editorWordWrap = computed(() => store.isEditorWordWrap)
// ─── Usage section: per-provider state (cross-provider) ─────────────
//
// Every registered provider that ``tracksUsage()`` may surface read +
// dump file settings. The Settings popover renders one block per
// provider, so all of read/dump local state (input value, in-flight
// validating flag, last validation result) is keyed by provider here.
// Persisted values live on each provider's own store, accessed via
// ``getProviderHelpers(provider).getUsageFileSetting(field)``.

const usageProviders = computed(() =>
    getRegisteredProviders().filter(
        p => enabledProviders.value.has(p) && getProviderHelpers(p)?.tracksUsage()
    )
)

// Reactive maps keyed by provider wire key.
const usageFilePathInput = ref({})         // { [provider]: string }
const usageFileValidating = ref({})        // { [provider]: boolean }
const usageFileValidation = ref({})        // { [provider]: {valid,message} | null }

const usageDumpPathInput = ref({})
const usageDumpValidating = ref({})
const usageDumpValidation = ref({})

function getReadEnabled(provider) {
    return !!getProviderHelpers(provider)?.getUsageFileSetting('read_enabled')
}
function getReadPath(provider) {
    return getProviderHelpers(provider)?.getUsageFileSetting('read_path') || ''
}
function getDumpEnabled(provider) {
    return !!getProviderHelpers(provider)?.getUsageFileSetting('dump_enabled')
}
function getDumpPath(provider) {
    return getProviderHelpers(provider)?.getUsageFileSetting('dump_path') || ''
}

function isReadPathModified(provider) {
    return (usageFilePathInput.value[provider] ?? '').trim() !== getReadPath(provider)
}
function readApplyIcon(provider) {
    if (usageFileValidation.value[provider]?.valid === false) return 'x-circle'
    if (isReadPathModified(provider)) return 'triangle-exclamation'
    return 'check'
}
function isDumpPathModified(provider) {
    return (usageDumpPathInput.value[provider] ?? '').trim() !== getDumpPath(provider)
}
function dumpApplyIcon(provider) {
    if (usageDumpValidation.value[provider]?.valid === false) return 'x-circle'
    if (isDumpPathModified(provider)) return 'triangle-exclamation'
    return 'check'
}

// Tmux config path — local input + validation state
const tmuxConfigPathInput = ref('')
const tmuxConfigValidating = ref(false)
const tmuxConfigValidation = ref(null) // { valid: boolean, message: string } | null
const tmuxConfigPathModified = computed(() => tmuxConfigPathInput.value.trim() !== (terminalTmuxConfigPath.value || ''))
const tmuxConfigApplyIcon = computed(() => {
    if (tmuxConfigValidation.value?.valid === false) return 'x-circle'
    if (tmuxConfigPathModified.value) return 'triangle-exclamation'
    return 'check'
})

// Default worktree directory — local input, committed to the store on Apply only.
const worktreeDirInput = ref('')
const worktreeDirModified = computed(() => worktreeDirInput.value.trim() !== (defaultWorktreeDirectory.value || ''))
const worktreeDirApplyIcon = computed(() => (worktreeDirModified.value ? 'triangle-exclamation' : 'check'))

// Check if the current prompt is the default
const isDefaultPrompt = computed(() => titleSystemPrompt.value === SETTINGS_SCHEMA.titleSystemPrompt)
const isTitleSystemPromptModified = computed(() => titleSystemPromptInput.value !== titleSystemPrompt.value)
const titleSystemPromptApplyIcon = computed(() => (isTitleSystemPromptModified.value ? 'triangle-exclamation' : 'check'))

// Server info for footer
const currentVersion = computed(() => dataStore.currentVersion)
const latestVersion = computed(() => dataStore.latestVersion)

// ─── Service status footer rotation ──────────────────────────────────
//
// Providers that publish a public service status (e.g. Anthropic's
// statuspage for Claude Code) opt in via ``helpers.getServiceStatus()``
// and ``helpers.getServiceStatusDisplay()``. The footer rotates among
// them every ``STATUS_ROTATION_INTERVAL_MS`` to keep the chrome compact;
// hover pauses the rotation (mirrors the sidebar usage rotation).

const STATUS_ROTATION_INTERVAL_MS = 15000

const _statusAwareProviders = computed(() =>
    getRegisteredProviders()
        .filter(p => enabledProviders.value.has(p))
        .map(provider => ({
            provider,
            helpers: getProviderHelpers(provider),
            getter: getProviderHelpers(provider).getServiceStatus(),
        }))
        .filter(({ getter }) => getter !== null)
)

const currentStatusProviderIndex = ref(0)

const currentStatusProvider = computed(() => {
    if (_statusAwareProviders.value.length === 0) return null
    return _statusAwareProviders.value[currentStatusProviderIndex.value % _statusAwareProviders.value.length]
})

const currentStatusDisplay = computed(() => {
    const entry = currentStatusProvider.value
    if (!entry) return null
    const status = entry.getter()
    if (!status) return null
    return entry.helpers.getServiceStatusDisplay(status)
})

const currentStatusIcon = computed(() => {
    const entry = currentStatusProvider.value
    return entry ? getProviderIcon(entry.provider) : null
})

const hasMultipleStatusProviders = computed(() => _statusAwareProviders.value.length > 1)

const statusFooterId = useId()
const statusFooterRef = ref(null)
const statusNextButtonId = useId()

let _statusRotationTimer = null
function _scheduleStatusRotation() {
    if (_statusRotationTimer) clearInterval(_statusRotationTimer)
    if (_statusAwareProviders.value.length <= 1) return
    _statusRotationTimer = setInterval(() => {
        if (statusFooterRef.value && statusFooterRef.value.matches(':hover')) return
        currentStatusProviderIndex.value =
            (currentStatusProviderIndex.value + 1) % _statusAwareProviders.value.length
    }, STATUS_ROTATION_INTERVAL_MS)
}
function _stopStatusRotation() {
    if (_statusRotationTimer) {
        clearInterval(_statusRotationTimer)
        _statusRotationTimer = null
    }
}
function cycleStatusProvider() {
    const total = _statusAwareProviders.value.length
    if (total <= 1) return
    currentStatusProviderIndex.value = (currentStatusProviderIndex.value + 1) % total
    // Restart the timer so the freshly selected provider gets the full
    // rotation interval rather than whatever remained on the previous tick.
    _scheduleStatusRotation()
}
_scheduleStatusRotation()
onBeforeUnmount(_stopStatusRotation)

// Re-schedule the rotation whenever the set of status-aware enabled providers changes.
watch(
    () => _statusAwareProviders.value.length,
    (newLen) => {
        if (currentStatusProviderIndex.value >= newLen) {
            currentStatusProviderIndex.value = 0
        }
        _stopStatusRotation()
        if (newLen > 1) {
            _scheduleStatusRotation()
        }
    }
)

// Display mode options for the select
const displayModeOptions = [
    { value: DISPLAY_MODE.CONVERSATION, label: 'Conversation' },
    { value: DISPLAY_MODE.SIMPLIFIED, label: 'Simplified' },
    { value: DISPLAY_MODE.NORMAL, label: 'Detailed' },
    { value: DISPLAY_MODE.DEBUG, label: 'Debug' },
]


/**
 * Handle default-provider change.
 */
function onDefaultProviderChange(event) {
    store.setDefaultProvider(event.target.value)
}

/**
 * Handle display mode change.
 */
function onDisplayModeChange(event) {
    store.setDisplayMode(event.target.value)
}

/**
 * Handle font size slider change.
 */
function onFontSizeChange(event) {
    store.setFontSize(event.target.value)
}

function onColorSchemeChange(event) {
    store.setColorScheme(event.target.value)
}

function onWaThemeChange(event) {
    store.setWaTheme(event.target.value)
}

function onWaBrandChange(event) {
    store.setWaBrand(event.target.value)
}

/**
 * Handle session time format change.
 */
function onSessionTimeFormatChange(event) {
    store.setSessionTimeFormat(event.target.value)
}

/**
 * Toggle costs display.
 */
function onShowCostsChange(event) {
    store.setShowCosts(event.target.checked)
}

/**
 * Toggle extra usage "only when needed" mode.
 */
function onExtraUsageOnlyWhenNeededChange(event) {
    store.setExtraUsageOnlyWhenNeeded(event.target.checked)
}

/**
 * Master switch for the "extra usage started" alert (same synced setting as the
 * one in the Notifications section).
 */
function onNotifyOnExtraUsageStartChange(event) {
    store.setNotifyOnExtraUsageStart(event.target.checked)
}

function onUsageFileEnabledChange(provider, event) {
    getProviderHelpers(provider)?.setUsageFileSetting('read_enabled', event.target.checked)
}

function onUsageFilePathInputChange(provider, event) {
    usageFilePathInput.value = { ...usageFilePathInput.value, [provider]: event.target.value }
    // Clear previous validation error when user edits
    if (usageFileValidation.value[provider]) {
        usageFileValidation.value = { ...usageFileValidation.value, [provider]: null }
    }
}

async function onUsageFilePathApply(provider) {
    const helpers = getProviderHelpers(provider)
    if (!helpers) return
    const path = (usageFilePathInput.value[provider] ?? '').trim()
    if (!path) {
        usageFileValidation.value = { ...usageFileValidation.value, [provider]: null }
        helpers.setUsageFileSetting('read_path', '')
        return
    }
    usageFileValidating.value = { ...usageFileValidating.value, [provider]: true }
    usageFileValidation.value = { ...usageFileValidation.value, [provider]: null }
    try {
        const result = await sendValidateUsageFile(provider, path)
        if (result.valid) {
            helpers.setUsageFileSetting('read_path', path)
        } else {
            usageFileValidation.value = { ...usageFileValidation.value, [provider]: result }
        }
    } finally {
        usageFileValidating.value = { ...usageFileValidating.value, [provider]: false }
    }
}

function onUsageDumpEnabledChange(provider, event) {
    getProviderHelpers(provider)?.setUsageFileSetting('dump_enabled', event.target.checked)
}

function onUsageDumpPathInputChange(provider, event) {
    usageDumpPathInput.value = { ...usageDumpPathInput.value, [provider]: event.target.value }
    if (usageDumpValidation.value[provider]) {
        usageDumpValidation.value = { ...usageDumpValidation.value, [provider]: null }
    }
}

async function onUsageDumpPathApply(provider) {
    const helpers = getProviderHelpers(provider)
    if (!helpers) return
    const path = (usageDumpPathInput.value[provider] ?? '').trim()
    if (!path) {
        usageDumpValidation.value = { ...usageDumpValidation.value, [provider]: null }
        helpers.setUsageFileSetting('dump_path', '')
        return
    }
    usageDumpValidating.value = { ...usageDumpValidating.value, [provider]: true }
    usageDumpValidation.value = { ...usageDumpValidation.value, [provider]: null }
    try {
        const result = await sendValidateUsageDumpPath(path)
        if (result.valid) {
            helpers.setUsageFileSetting('dump_path', path)
        } else {
            usageDumpValidation.value = { ...usageDumpValidation.value, [provider]: result }
        }
    } finally {
        usageDumpValidating.value = { ...usageDumpValidating.value, [provider]: false }
    }
}

/**
 * Handle max cached sessions slider change.
 */
function onMaxCachedSessionsChange(event) {
    store.setMaxCachedSessions(event.target.value)
}

/**
 * Toggle auto-unpin on archive.
 */
function onAutoUnpinOnArchiveChange(event) {
    store.setAutoUnpinOnArchive(event.target.checked)
}

/**
 * Toggle title generation.
 */
function onTitleGenerationChange(event) {
    store.setTitleGenerationEnabled(event.target.checked)
}

/**
 * Toggle title auto-apply.
 */
function onTitleAutoApplyChange(event) {
    store.setTitleAutoApply(event.target.checked)
}

/**
 * Handle title system prompt change: update only the local buffer.
 * The store (and backend sync) is only touched when Apply is clicked.
 */
function onTitleSystemPromptChange(event) {
    titleSystemPromptInput.value = event.target.value
}

function onTitleSystemPromptApply() {
    store.setTitleSystemPrompt(titleSystemPromptInput.value)
}

/**
 * Toggle terminal tmux persistence.
 */
function onTmuxChange(event) {
    store.setTerminalUseTmux(event.target.checked)
}

function onWorktreeDirInputChange(event) {
    worktreeDirInput.value = event.target.value
}

// Commit to the synced setting only when the user hits Apply (mirrors the tmux
// config path control). Trimmed; empty means "no default".
function onWorktreeDirApply() {
    store.setDefaultWorktreeDirectory(worktreeDirInput.value.trim())
}

function onTmuxConfigPathInputChange(event) {
    tmuxConfigPathInput.value = event.target.value
    if (tmuxConfigValidation.value) tmuxConfigValidation.value = null
}

async function onTmuxConfigPathApply() {
    const path = tmuxConfigPathInput.value.trim()
    if (!path) {
        tmuxConfigValidation.value = null
        store.setTerminalTmuxConfigPath('')
        return
    }
    tmuxConfigValidating.value = true
    tmuxConfigValidation.value = null
    try {
        const result = await sendValidateTmuxConfigPath(path)
        if (result.valid) {
            store.setTerminalTmuxConfigPath(path)
        } else {
            tmuxConfigValidation.value = result
        }
    } finally {
        tmuxConfigValidating.value = false
    }
}

/**
 * Toggle compact session list.
 */
function onCompactSessionListChange(event) {
    store.setCompactSessionList(event.target.checked)
}

/**
 * Toggle per-block message timestamps in the session view.
 */
function onShowMessageTimestampsChange(event) {
    store.setShowMessageTimestamps(event.target.checked)
}

/**
 * Toggle show diffs (auto-expand Edit/Write details).
 */
function onShowDiffsChange(event) {
    store.setShowDiffs(event.target.checked)
}

/**
 * Toggle tool diff word wrap default (for Edit/Write diffs in sessions).
 */
function onToolDiffWordWrapChange(event) {
    store.setToolDiffWordWrap(event.target.checked)
}

/**
 * Toggle tool diff side-by-side default (for Edit/Write diffs in sessions).
 */
function onToolDiffSideBySideChange(event) {
    store.setToolDiffSideBySide(event.target.checked)
}

/**
 * Toggle diff side-by-side default (for the editor/git panel).
 */
function onDiffSideBySideChange(event) {
    store.setDiffSideBySide(event.target.checked)
}

/**
 * Toggle editor word wrap default.
 */
function onEditorWordWrapChange(event) {
    store.setEditorWordWrap(event.target.checked)
}

/**
 * Reset title system prompt to default. Updates the store immediately
 * (per user choice) and resyncs the local buffer so Apply icon goes back
 * to the "synced" state.
 */
function resetTitleSystemPrompt() {
    store.resetTitleSystemPrompt()
    titleSystemPromptInput.value = titleSystemPrompt.value
}

/**
 * Called when popover opens - reset mobile view and refresh notification state.
 */
function onPopoverShow() {
    mobileShowContent.value = false
    // Seed the worktree-directory input from the persisted value (Global is the
    // default section, so selectSection('global') may not fire on open).
    worktreeDirInput.value = defaultWorktreeDirectory.value || ''
    if (activeSection.value === 'notifications') {
        nextTick(() => notificationSettingsRef.value?.sync())
    }
}

function openChangelog(options) {
    changelogDialogRef.value?.open(options)
}

function onOpenChangelogEvent() {
    openChangelog({ skipCombined: true })
}
window.addEventListener('open-changelog', onOpenChangelogEvent)
onBeforeUnmount(() => window.removeEventListener('open-changelog', onOpenChangelogEvent))

watch(() => dataStore.pendingChangelogVersion, (version) => {
    if (version) {
        forcedChangelogOpen.value = true
        changelogDialogRef.value?.open()
    }
})

function onChangelogClose() {
    if (forcedChangelogOpen.value) {
        forcedChangelogOpen.value = false
        const version = dataStore.pendingChangelogVersion
        if (version) {
            sendChangelogSeen(version)
        }
        dataStore.clearPendingChangelogVersion()
    }
}
</script>

<template>
    <wa-button id="settings-trigger" variant="neutral" appearance="filled-outlined" size="small">
        <wa-icon name="gear"></wa-icon><span>Settings</span>
    </wa-button>
    <AppTooltip for="settings-trigger">Toggle settings</AppTooltip>
    <wa-popover ref="popoverRef" v-popover-focus-fix for="settings-trigger" placement="top" class="settings-popover" @wa-show="onPopoverShow">
        <AppTooltip v-if="showLogout" :for="logoutButtonId">Logout</AppTooltip>
        <div class="settings-layout">
            <div class="settings-layout-inner" :class="{ 'showing-content': mobileShowContent }">
                <!-- Nav: section list -->
                <nav class="settings-nav">
                    <button
                        v-for="section in sections"
                        :key="section.id"
                        class="settings-nav-item"
                        :class="{ active: activeSection === section.id }"
                        @click="selectSection(section.id)"
                    >
                        <wa-icon
                            v-if="section.icon"
                            auto-width
                            family="brands"
                            :name="section.icon"
                            class="settings-nav-provider-icon"
                        ></wa-icon>
                        {{ section.navLabel || section.label }}
                        <wa-icon v-if="section.synced" name="cloud" class="synced-icon"></wa-icon>
                    </button>
                    <wa-divider class="settings-nav-divider shortcuts-nav-divider"></wa-divider>
                    <button
                        class="settings-nav-item shortcuts-nav-item"
                        :class="{ active: activeSection === 'shortcuts' }"
                        @click="selectSection('shortcuts')"
                    >
                        Shortcuts
                    </button>
                    <button
                        v-if="hasTips"
                        class="settings-nav-item tips-nav-item"
                        :class="{ active: activeSection === 'tips' }"
                        @click="selectSection('tips')"
                    >
                        Tips
                    </button>
                </nav>

                <wa-divider class="settings-vertical-divider" orientation="vertical"></wa-divider>

                <!-- Detail: section content -->
                <div class="settings-detail">
                    <div class="settings-detail-header" @click="goBackToNav">
                        <wa-button
                            variant="neutral"
                            appearance="plain"
                            size="small"
                        >
                            <wa-icon name="arrow-left"></wa-icon>
                        </wa-button>
                        <span class="settings-detail-header-title">
                            {{ activeSectionLabel }}
                            <wa-icon v-if="activeSectionObj?.synced" name="cloud" class="synced-icon"></wa-icon>
                        </span>
                    </div>
                    <div class="settings-sections">

                <!-- Global Section -->
                <section v-if="activeSection === 'global'" class="settings-section">
                    <h3 class="settings-section-title">Global</h3>
                    <div class="setting-group">
                        <label class="setting-group-label">Color scheme</label>
                        <wa-select
                            :value.prop="colorScheme"
                            @change="onColorSchemeChange"
                            size="small"
                        >
                            <wa-option
                                v-for="option in colorSchemeOptions"
                                :key="option.value"
                                :value="option.value"
                            >{{ option.label }}</wa-option>
                        </wa-select>
                    </div>
                    <div class="setting-group">
                        <label class="setting-group-label">Theme <wa-icon name="cloud" class="synced-icon"></wa-icon></label>
                        <wa-select
                            :value.prop="waTheme"
                            @change="onWaThemeChange"
                            size="small"
                        >
                            <wa-option
                                v-for="option in waThemeOptions"
                                :key="option.value"
                                :value="option.value"
                            >{{ option.label }}</wa-option>
                        </wa-select>
                    </div>
                    <div class="setting-group">
                        <label class="setting-group-label">Accent color <wa-icon name="cloud" class="synced-icon"></wa-icon></label>
                        <wa-select
                            :value.prop="waBrand"
                            @change="onWaBrandChange"
                            size="small"
                        >
                            <wa-option
                                v-for="option in waBrandOptions"
                                :key="option.value"
                                :value="option.value"
                            >{{ option.label }}</wa-option>
                        </wa-select>
                    </div>
                    <div class="setting-group">
                        <label class="setting-group-label">Font size ({{fontSize}}px)</label>
                        <wa-slider
                            :min.prop="12"
                            :max.prop="32"
                            :step.prop="1"
                            :value.prop="fontSize"
                            @input="onFontSizeChange"
                            size="small"
                        ></wa-slider>
                    </div>
                    <wa-divider></wa-divider>
                    <div class="setting-group">
                        <label class="setting-group-label">Time display</label>
                        <wa-select
                            :value.prop="sessionTimeFormat"
                            @change="onSessionTimeFormatChange"
                            size="small"
                            class="session-time-format-select"
                        >
                            <wa-option
                                v-for="option in sessionTimeFormatOptions"
                                :key="option.value"
                                :value="option.value"
                            >{{ option.label }}</wa-option>
                        </wa-select>
                    </div>
                    <div class="setting-group">
                        <label class="setting-group-label">Show costs</label>
                        <wa-switch
                            :checked="showCosts"
                            @change="onShowCostsChange"
                            size="small"
                        >Enabled</wa-switch>
                    </div>
                    <wa-divider></wa-divider>
                    <div class="setting-group">
                        <label class="setting-group-label">Default worktree directory <wa-icon name="cloud" class="synced-icon"></wa-icon></label>
                        <div class="worktree-dir-input-row">
                            <span class="worktree-dir-prefix">&lt;git root&gt;/</span>
                            <wa-input
                                :value="worktreeDirInput"
                                @input="onWorktreeDirInputChange"
                                @keydown.enter="onWorktreeDirApply"
                                placeholder=".worktrees"
                                size="small"
                            ></wa-input>
                            <wa-button
                                size="small"
                                variant="neutral"
                                @click="onWorktreeDirApply"
                            >
                                <wa-icon :name="worktreeDirApplyIcon" slot="start"></wa-icon>
                                Apply
                            </wa-button>
                        </div>
                        <span class="setting-group-hint">
                            Base directory for new git worktrees, relative to each project's git root
                            (<code>../</code> allowed). Pre-fills the path when creating a worktree.
                            A project can override this with its own absolute directory. Leave empty for no default.
                        </span>
                    </div>
                </section>

                <!-- Providers section -->
                <section v-if="activeSection === 'providers'" class="settings-section">
                    <h3 class="settings-section-title">Providers <wa-icon name="cloud" class="synced-icon"></wa-icon></h3>
                    <div class="activated-providers-block">
                        <h4>Activated providers</h4>
                        <p class="hint">
                            Disabling a provider stops all of its background tasks, prevents
                            creating new sessions or renaming existing ones, and hides its
                            settings section. Existing sessions remain readable.
                        </p>
                        <div class="provider-switches">
                            <div v-for="p in getRegisteredProviders()" :key="p" class="provider-switch-row">
                                <div class="provider-switch-line">
                                    <wa-switch
                                        class="provider-switch"
                                        :checked="enabledProviders.has(p)"
                                        :disabled="isSwitchDisabled(p)"
                                        @change="(e) => onToggleProvider(p, e)"
                                    >
                                        <wa-icon
                                            v-if="providerIconFor(p)"
                                            auto-width
                                            family="brands"
                                            :name="providerIconFor(p)"
                                            class="provider-switch-icon"
                                        ></wa-icon>
                                        {{ providerLabelFor(p) }}
                                    </wa-switch>
                                    <template v-if="transitionLabelFor(p)">
                                        <span class="transition-label">{{ transitionLabelFor(p) }}</span>
                                        <wa-spinner class="transition-spinner"></wa-spinner>
                                    </template>
                                </div>
                                <span v-if="reasonFor(p)" class="hint danger">{{ reasonFor(p) }}</span>
                            </div>
                        </div>
                    </div>
                    <div class="setting-group">
                        <label class="setting-group-label">Default provider for new sessions</label>
                        <wa-select
                            :value.prop="defaultProvider"
                            @change="onDefaultProviderChange"
                            size="small"
                        >
                            <wa-icon
                                v-if="providerIconFor(defaultProvider)"
                                slot="start"
                                auto-width
                                family="brands"
                                :name="providerIconFor(defaultProvider)"
                            ></wa-icon>
                            <wa-option
                                v-for="option in enabledProviderOptions"
                                :key="option.value"
                                :value="option.value"
                                :label="option.label"
                            >
                                <wa-icon
                                    v-if="providerIconFor(option.value)"
                                    auto-width
                                    family="brands"
                                    :name="providerIconFor(option.value)"
                                    class="provider-option-icon"
                                ></wa-icon>
                                {{ option.label }}
                            </wa-option>
                        </wa-select>
                    </div>
                </section>

                <!-- Per-provider sections — one block per registered provider, identified by its wire key. -->
                <template v-for="section in providerSections" :key="section.id">
                    <ProviderSettingsSection
                        v-if="activeSection === section.id"
                        :provider="section.provider"
                    />
                </template>

                <!-- Notifications Section -->
                <NotificationSettings v-if="activeSection === 'notifications'" ref="notificationSettingsRef" />

                <!-- Sessions Section -->
                <section v-if="activeSection === 'sessions'" class="settings-section">
                    <h3 class="settings-section-title">Sessions</h3>
                    <div class="setting-group">
                        <label class="setting-group-label">Display mode</label>
                        <wa-select
                            :value.prop="displayMode"
                            @change="onDisplayModeChange"
                            size="small"
                        >
                            <wa-option
                                v-for="option in displayModeOptions"
                                :key="option.value"
                                :value="option.value"
                            >{{ option.label }}</wa-option>
                        </wa-select>
                    </div>
                    <div class="setting-group">
                        <label class="setting-group-label">Diffs</label>
                        <wa-switch
                            :checked="showDiffs"
                            @change="onShowDiffsChange"
                            size="small"
                        >Auto open edits</wa-switch>
                        <wa-switch
                            :checked="toolDiffWordWrap"
                            @change="onToolDiffWordWrapChange"
                            size="small"
                        >Word wrap</wa-switch>
                        <wa-switch
                            :checked="toolDiffSideBySide"
                            @change="onToolDiffSideBySideChange"
                            size="small"
                        >Side by side</wa-switch>
                        <span class="setting-group-hint">Inactive if the screen is too narrow.</span>
                    </div>
                    <div class="setting-group">
                        <label class="setting-group-label">Message timestamps</label>
                        <wa-switch
                            :checked="showMessageTimestamps"
                            @change="onShowMessageTimestampsChange"
                            size="small"
                        >Show time under each message block</wa-switch>
                    </div>
                    <div class="setting-group">
                        <label class="setting-group-label">Auto-unpin on archive <wa-icon name="cloud" class="synced-icon"></wa-icon></label>
                        <wa-switch
                            :checked="autoUnpinOnArchive"
                            @change="onAutoUnpinOnArchiveChange"
                            size="small"
                        >Enabled</wa-switch>
                    </div>
                    <div class="setting-group">
                        <label class="setting-group-label">Compact session list</label>
                        <wa-switch
                            :checked="compactSessionList"
                            @change="onCompactSessionListChange"
                            size="small"
                        >Enabled</wa-switch>
                    </div>
                    <div class="setting-group">
                        <label class="setting-group-label">Session cache ({{ maxCachedSessions }})</label>
                        <wa-slider
                            :min.prop="1"
                            :max.prop="50"
                            :step.prop="1"
                            :value.prop="maxCachedSessions"
                            @input="onMaxCachedSessionsChange"
                            size="small"
                        ></wa-slider>
                        <span class="setting-group-hint">Number of sessions kept in memory for instant switching.</span>
                    </div>
                </section>

                <!-- Title Suggestion Section -->
                <section v-if="activeSection === 'title'" class="settings-section">
                    <h3 class="settings-section-title">Title suggestion <wa-icon name="cloud" class="synced-icon"></wa-icon></h3>
                    <div class="setting-group">
                        <wa-switch
                            :checked="titleGenerationEnabled"
                            @change="onTitleGenerationChange"
                            size="small"
                        >Enabled (Using Haiku for Claude, GPT 5.4 mini for Codex)</wa-switch>
                        <wa-switch
                            v-if="titleGenerationEnabled"
                            :checked="titleAutoApply"
                            @change="onTitleAutoApplyChange"
                            size="small"
                        >Auto-apply on new sessions</wa-switch>
                        <div v-if="titleGenerationEnabled" class="title-prompt-section">
                            <label class="setting-group-label">System prompt</label>
                            <wa-textarea
                                :value.prop="titleSystemPromptInput"
                                @input="onTitleSystemPromptChange"
                                size="small"
                                rows="7"
                                resize="vertical"
                                class="title-prompt-textarea"
                            ></wa-textarea>
                            <div class="title-prompt-hint">
                                <span>Use <code>{text}</code> as placeholder. Press Apply to save.</span>
                                <div class="title-prompt-actions">
                                    <wa-button
                                        v-if="!isDefaultPrompt"
                                        variant="neutral"
                                        appearance="outlined"
                                        size="small"
                                        @click.stop="resetTitleSystemPrompt"
                                    >Reset to default</wa-button>
                                    <wa-button
                                        size="small"
                                        variant="neutral"
                                        @click.stop="onTitleSystemPromptApply"
                                    >
                                        <wa-icon :name="titleSystemPromptApplyIcon" slot="start"></wa-icon>
                                        Apply
                                    </wa-button>
                                </div>
                            </div>
                        </div>
                    </div>
                </section>

                <!-- Editor Section -->
                <section v-if="activeSection === 'editor'" class="settings-section">
                    <h3 class="settings-section-title">Editor</h3>
                    <div class="setting-group">
                        <label class="setting-group-label">Display</label>
                        <wa-switch
                            :checked="editorWordWrap"
                            @change="onEditorWordWrapChange"
                            size="small"
                        >Word wrap</wa-switch>
                        <wa-switch
                            :checked="diffSideBySide"
                            @change="onDiffSideBySideChange"
                            size="small"
                        >Diff side by side</wa-switch>
                        <span class="setting-group-hint">Inactive if the screen is too narrow.</span>
                    </div>
                </section>

                <!-- Terminal Section -->
                <section v-if="activeSection === 'terminal'" class="settings-section">
                    <h3 class="settings-section-title">Terminal</h3>
                    <div class="setting-group">
                        <label class="setting-group-label">Persistent sessions (tmux) <wa-icon name="cloud" class="synced-icon"></wa-icon></label>
                        <wa-switch
                            :checked="terminalUseTmux"
                            @change="onTmuxChange"
                            size="small"
                        >Enabled</wa-switch>
                        <span class="setting-group-hint">Tmux sessions are destroyed when their agent session is archived.</span>
                    </div>
                    <div class="setting-group" v-if="terminalUseTmux">
                        <label class="setting-group-label">Tmux config file <wa-icon name="cloud" class="synced-icon"></wa-icon></label>
                        <div class="usage-file-input-row">
                            <wa-input
                                :value="tmuxConfigPathInput"
                                @input="onTmuxConfigPathInputChange"
                                @keydown.enter="onTmuxConfigPathApply"
                                placeholder="/path/to/tmux.conf (leave empty to ignore)"
                                size="small"
                                :disabled="tmuxConfigValidating"
                            ></wa-input>
                            <wa-button
                                size="small"
                                variant="neutral"
                                @click="onTmuxConfigPathApply"
                                :disabled="tmuxConfigValidating"
                            >
                                <wa-spinner v-if="tmuxConfigValidating" slot="start"></wa-spinner>
                                <wa-icon v-else :name="tmuxConfigApplyIcon" slot="start"></wa-icon>
                                Apply
                            </wa-button>
                        </div>
                        <span class="setting-group-hint">
                            TwiCC always runs tmux on a dedicated socket (<code>-L twicc</code>) and forces
                            <code>mouse off</code> after session creation — these invariants are required for
                            frontend selection and scroll to work. Your config is loaded first (so status bar,
                            colors, bindings apply), then the mouse option is overridden at the session level.
                            Leave empty to ignore any config. Applies to new terminals only.
                        </span>
                        <wa-callout
                            v-if="tmuxConfigValidation && !tmuxConfigValidation.valid"
                            variant="danger"
                            size="small"
                            class="usage-file-validation"
                        >{{ tmuxConfigValidation.message }}</wa-callout>
                    </div>
                </section>

                <!-- Tips Section -->
                <TipsSettings v-if="activeSection === 'tips'" />

                <!-- Providers quotas/usage Section -->
                <section v-if="activeSection === 'usage'" class="settings-section">
                    <h3 class="settings-section-title">Providers quotas/usage</h3>
                    <div class="setting-group">
                        <label class="setting-group-label">Show extra usage quota</label>
                        <wa-switch
                            :checked="extraUsageOnlyWhenNeeded"
                            @change="onExtraUsageOnlyWhenNeededChange"
                            size="small"
                        >Only when needed</wa-switch>
                    </div>
                    <div class="setting-group">
                        <label class="setting-group-label">When extra usage starts</label>
                        <wa-switch
                            :checked="notifyOnExtraUsageStart"
                            @change="onNotifyOnExtraUsageStartChange"
                            size="small"
                        >Notify me</wa-switch>
                        <span class="setting-group-hint">
                            Alerts you when a provider starts consuming its extra usage credits again
                            after a quiet period. See the
                            <a href="#" @click.prevent="selectSection('notifications')">Notifications</a>
                            tab for sound, browser and pushed-device options.
                        </span>
                    </div>
                    <template v-for="(provider, idx) in usageProviders" :key="provider">
                        <wa-divider v-if="idx === 0"></wa-divider>
                        <div class="provider-usage-block">
                            <h4 class="provider-usage-title">
                                <wa-icon
                                    v-if="providerIconFor(provider)"
                                    auto-width
                                    family="brands"
                                    :name="providerIconFor(provider)"
                                ></wa-icon>
                                {{ getProviderLabel(provider) }}
                            </h4>
                        <div class="setting-group">
                            <wa-switch
                                :checked="getReadEnabled(provider)"
                                @change="onUsageFileEnabledChange(provider, $event)"
                                size="small"
                                :disabled="getDumpEnabled(provider)"
                            >Read usage from file* <wa-icon name="cloud" class="synced-icon"></wa-icon></wa-switch>
                            <template v-if="getReadEnabled(provider)">
                                <div class="usage-file-input-row">
                                    <wa-input
                                        :value="usageFilePathInput[provider] ?? ''"
                                        @input="onUsageFilePathInputChange(provider, $event)"
                                        @keydown.enter="onUsageFilePathApply(provider)"
                                        placeholder="/path/to/usage.json"
                                        size="small"
                                        :disabled="!!usageFileValidating[provider]"
                                    ></wa-input>
                                    <wa-button
                                        size="small"
                                        variant="neutral"
                                        @click="onUsageFilePathApply(provider)"
                                        :disabled="!!usageFileValidating[provider]"
                                    >
                                        <wa-spinner v-if="usageFileValidating[provider]" slot="start"></wa-spinner>
                                        <wa-icon v-else :name="readApplyIcon(provider)" slot="start"></wa-icon>
                                        Apply
                                    </wa-button>
                                </div>
                                <span class="setting-group-hint">Press Apply or Enter to validate and save the path.</span>
                                <wa-callout
                                    v-if="usageFileValidation[provider] && !usageFileValidation[provider].valid"
                                    variant="danger"
                                    size="small"
                                    class="usage-file-validation"
                                >{{ usageFileValidation[provider].message }}</wa-callout>
                            </template>
                        </div>
                        <div class="setting-group">
                            <wa-switch
                                :checked="getDumpEnabled(provider)"
                                @change="onUsageDumpEnabledChange(provider, $event)"
                                size="small"
                                :disabled="getReadEnabled(provider)"
                            >Dump usage to file** <wa-icon name="cloud" class="synced-icon"></wa-icon></wa-switch>
                            <template v-if="getDumpEnabled(provider)">
                                <div class="usage-file-input-row">
                                    <wa-input
                                        :value="usageDumpPathInput[provider] ?? ''"
                                        @input="onUsageDumpPathInputChange(provider, $event)"
                                        @keydown.enter="onUsageDumpPathApply(provider)"
                                        placeholder="/path/to/usage-dump.json"
                                        size="small"
                                        :disabled="!!usageDumpValidating[provider]"
                                    ></wa-input>
                                    <wa-button
                                        size="small"
                                        variant="neutral"
                                        @click="onUsageDumpPathApply(provider)"
                                        :disabled="!!usageDumpValidating[provider]"
                                    >
                                        <wa-spinner v-if="usageDumpValidating[provider]" slot="start"></wa-spinner>
                                        <wa-icon v-else :name="dumpApplyIcon(provider)" slot="start"></wa-icon>
                                        Apply
                                    </wa-button>
                                </div>
                                <span class="setting-group-hint">Press Apply or Enter to validate and save the path.</span>
                                <wa-callout
                                    v-if="usageDumpValidation[provider] && !usageDumpValidation[provider].valid"
                                    variant="danger"
                                    size="small"
                                    class="usage-file-validation"
                                >{{ usageDumpValidation[provider].message }}</wa-callout>
                            </template>
                        </div>
                        </div>
                    </template>
                    <!-- Cross-provider explanations rendered once at the bottom, so each
                         provider block above stays compact (just toggles + path inputs).
                         The synced-icon lives next to each provider's read/dump
                         switches above, where the actual settings are stored. -->
                    <wa-divider></wa-divider>
                    <div class="setting-group usage-mode-explanation">
                        <label class="setting-group-label">* About read mode</label>
                        <span class="setting-group-hint">
                            If you already maintain a JSON file with usage data outside TwiCC (typically
                            because the provider's API is rate-limited), point to it here and TwiCC will
                            read from this file instead of calling the API directly.
                        </span>
                    </div>
                    <div class="setting-group usage-mode-explanation">
                        <label class="setting-group-label">** About dump mode</label>
                        <span class="setting-group-hint">
                            Save the raw API response to a JSON file each time TwiCC fetches usage data.
                            Useful if you want to share the data with other tools without extra API calls.
                        </span>
                    </div>
                </section>

                <!-- Keyboard Shortcuts Section -->
                <section v-if="activeSection === 'shortcuts'" class="settings-section shortcuts-section">
                    <h3 class="settings-section-title">Keyboard shortcuts</h3>
                    <div v-for="group in shortcutGroups" :key="group.label" class="shortcut-group">
                        <h4 class="shortcut-group-title">{{ group.label }}</h4>
                        <div class="shortcut-list">
                            <div v-for="(shortcut, i) in group.shortcuts" :key="i" class="shortcut-item">
                                <span class="shortcut-keys">
                                    <template v-for="(key, j) in shortcut.keys" :key="j">
                                        <span v-if="j > 0" class="shortcut-plus">+</span>
                                        <kbd>{{ key }}</kbd>
                                    </template>
                                </span>
                                <span class="shortcut-description">{{ shortcut.description }}</span>
                            </div>
                        </div>
                    </div>
                </section>

                    </div>
                </div>
            </div>
        </div>
        <wa-divider></wa-divider>
        <p class="settings-notice">
            <wa-icon name="cloud" class="synced-icon"></wa-icon>
            Sections and settings marked with a cloud icon are synced across all your devices.
        </p>
        <wa-divider></wa-divider>
        <footer v-if="currentVersion" class="settings-footer">
            <span class="settings-footer-version">
                <a href="https://github.com/twidi/twicc/" target="_blank" rel="noopener">TwiCC v{{ currentVersion }}</a><template v-if="store.isDevMode"> [dev]</template>
                <template v-if="latestVersion">
                    &rarr;
                    <a :href="latestVersion.releaseUrl" target="_blank" rel="noopener">v{{ latestVersion.version }} available</a>
                </template>
            </span>
            ·
            <a href="#" class="settings-footer-changes" @click.prevent="openChangelog()">Changes</a>
            ·
            <a :href="SPONSOR_URL" target="_blank" rel="noopener" class="settings-footer-sponsor">
                <span class="settings-footer-sponsor-icon"></span>
                Sponsor
            </a>
            ·
            <a
                v-if="currentStatusDisplay"
                ref="statusFooterRef"
                :href="currentStatusDisplay.url"
                target="_blank"
                rel="noopener"
                class="settings-footer-status"
                :class="`settings-footer-status--${currentStatusDisplay.modifier}`"
                :id="statusFooterId"
            >
                <wa-icon v-if="currentStatusIcon" family="brands" :name="currentStatusIcon" class="settings-footer-status-icon"></wa-icon>
                <span class="status-dot"></span>
                {{ currentStatusDisplay.label }}
            </a>
            <AppTooltip v-if="currentStatusDisplay" :for="statusFooterId">{{ currentStatusDisplay.tooltip }}</AppTooltip>
            <wa-icon
                v-if="currentStatusDisplay && hasMultipleStatusProviders"
                :id="statusNextButtonId"
                class="settings-footer-status-next"
                name="repeat"
                @click="cycleStatusProvider"
            ></wa-icon>
            <AppTooltip v-if="currentStatusDisplay && hasMultipleStatusProviders" :for="statusNextButtonId">Switch to the next provider</AppTooltip>
            <wa-button
                v-if="showLogout"
                :id="logoutButtonId"
                class="logout-button"
                variant="danger"
                appearance="plain"
                size="small"
                @click="handleLogout"
            >
                <wa-icon name="right-from-bracket"></wa-icon>
            </wa-button>
        </footer>
    </wa-popover>
    <ChangelogDialog ref="changelogDialogRef" @close="onChangelogClose" />
</template>

<style scoped>
#settings-trigger::part(label) {
    display: flex;
    gap: var(--wa-space-s);
}

.settings-popover {
    --max-width: 90vw;
    --arrow-size: 16px;
}

.settings-popover::part(body) {
    padding: 0;
}

/* -- Master-detail layout -- */

.settings-layout {
    display: flex;
    flex-direction: column;
    overflow: hidden;
    height: min(calc(90dvh - 8rem), 50rem);
    width: min(90vw, 700px);
}

.settings-layout-inner {
    display: flex;
    flex: 1;
    min-height: 0;
    width: 100%;
}

/* Nav panel (section list) */

.settings-nav {
    width: 200px;
    min-width: 200px;
    overflow-y: auto;
    padding: var(--wa-space-m);
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-2xs);
}

.settings-nav-item {
    all: unset;
    box-sizing: border-box;
    cursor: pointer;
    padding: var(--wa-space-xs) var(--wa-space-s);
    border-radius: var(--wa-border-radius-m);
    font-size: var(--wa-font-size-m);
    color: var(--wa-color-text);
    text-align: left;
    transition: background 0.15s;
    display: flex;
    align-items: center;
    gap: var(--wa-space-xs);
    wa-icon {
        margin-inline: 0 !important;
    }
}

.settings-nav-item:hover {
    background: var(--wa-color-surface);
}

.settings-nav-item.active {
    color: var(--wa-color-brand);
    font-weight: var(--wa-font-weight-semibold);
}

/* Vertical divider between nav and detail */

.settings-vertical-divider {
    --width: var(--divider-size);
    --spacing: 0;
    align-self: stretch;
    height: auto;
    min-height: 0;
}

/* Detail panel (section content) */

.settings-detail {
    flex: 1;
    min-width: 0;
    overflow-y: auto;
    padding: var(--wa-space-m);
}

/* Detail header (back button) - hidden on desktop */
.settings-detail-header {
    display: none;
}

/* -- Mobile: sliding panels -- */

@media (width < 640px) {
    .settings-layout {
        width: auto;
    }

    .settings-layout-inner {
        width: 200%;
        transition: transform 0.25s ease;
    }

    .settings-layout-inner.showing-content {
        transform: translateX(-50%);
    }

    .settings-nav {
        width: 50%;
        min-width: 50%;
        padding: var(--wa-space-s);
    }

    .settings-vertical-divider {
        display: none;
    }

    .settings-detail {
        width: 50%;
        padding: var(--wa-space-s);
    }

    .settings-detail-header {
        display: flex;
        align-items: center;
        gap: var(--wa-space-2xs);
        cursor: pointer;
        margin-bottom: var(--wa-space-s);
    }

    .settings-detail-header-title {
        font-weight: var(--wa-font-weight-bold);
        font-size: var(--wa-font-size-s);
        color: var(--wa-color-brand);
        display: flex;
        align-items: center;
        gap: var(--wa-space-xs);
    }

    .settings-nav-item.active {
        color: var(--wa-color-text);
        font-weight: inherit;
    }

    .settings-nav-item::after {
        content: '›';
        margin-left: auto;
        font-size: 1.3em;
        color: var(--wa-color-text-quiet);
    }
}

/* -- Scroll shadow indicators (progressive enhancement) -- */

@supports (container-type: scroll-state) {
    .settings-nav,
    .settings-detail {
        --_panel-pad: var(--wa-space-m);
        container-type: scroll-state;
    }

    .settings-nav::before,
    .settings-nav::after,
    .settings-detail::before,
    .settings-detail::after {
        --_shadow-color: color-mix(in srgb, var(--wa-color-text-normal) 12%, transparent);
        content: '';
        display: block;
        flex-shrink: 0;
        position: sticky;
        height: 16px;
        margin-inline: calc(-1 * var(--_panel-pad));
        z-index: 2;
        pointer-events: none;
        opacity: 0;
        transition: opacity 0.2s ease;
    }

    .settings-nav::before,
    .settings-detail::before {
        top: 0;
        translate: 0 calc(-1 * var(--_panel-pad));
        background: linear-gradient(to bottom, var(--_shadow-color), transparent);
    }

    .settings-nav::after,
    .settings-detail::after {
        bottom: 0;
        translate: 0 var(--_panel-pad);
        background: linear-gradient(to top, var(--_shadow-color), transparent);
    }

    /* Flex ordering for nav (flex-direction: column) */
    .settings-nav::before {
        order: -1;
    }

    .settings-nav::after {
        order: 9999;
    }

    @container scroll-state(scrollable: top) {
        .settings-nav::before,
        .settings-detail::before {
            opacity: 1;
        }
    }

    @container scroll-state(scrollable: bottom) {
        .settings-nav::after,
        .settings-detail::after {
            opacity: 1;
        }
    }

    @media (width < 640px) {
        .settings-nav,
        .settings-detail {
            --_panel-pad: var(--wa-space-s);
        }
    }
}

/* -- Settings notice (footer bar) -- */

.settings-notice {
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-text-quiet);
    margin: 0;
    display: flex;
    align-items: baseline;
    gap: var(--wa-space-xs);
    padding: var(--wa-space-xs) var(--wa-space-s);

    .synced-icon {
        font-size: 1em;
        position: relative;
        top: 0.1em;
        flex-shrink: 0;
    }
}

/* -- Section content styles -- */

.title-prompt-section {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-xs);
    margin-top: var(--wa-space-xs);
}

.title-prompt-textarea {
    font-family: var(--wa-font-family-code);
    font-size: var(--wa-font-size-xs);
}

.title-prompt-hint {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-xs);
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-text-quiet);

    .title-prompt-actions {
        display: flex;
        flex-wrap: wrap;
        gap: var(--wa-space-xs);
        align-items: center;
        justify-content: flex-end;
    }

    code {
        background: var(--wa-color-surface);
        padding: 0 var(--wa-space-2xs);
        border-radius: var(--wa-radius-s);
    }

    wa-button {
        align-self: end;
    }
}

.option-description {
    display: block;
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-quiet);
}

.synced-icon {
    color: var(--wa-color-brand);
}

/* -- Nav divider (horizontal, between settings sections and extra items) -- */

.settings-nav-divider {
    --spacing: var(--wa-space-2xs);
}

/* Hide shortcuts entry on touch devices (no keyboard) */
@media (pointer: coarse) {
    .shortcuts-nav-divider,
    .shortcuts-nav-item {
        display: none;
    }
}

/* -- Keyboard shortcuts section -- */

.shortcuts-section {
    gap: var(--wa-space-l) !important;
}

.shortcut-group {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-xs);
}

.shortcut-group-title {
    font-size: var(--wa-font-size-s);
    font-weight: var(--wa-font-weight-semibold);
    color: var(--wa-color-brand);
    margin: 0;
}

.shortcut-list {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-3xs);
}

.shortcut-item {
    display: flex;
    align-items: baseline;
    gap: var(--wa-space-m);
    font-size: var(--wa-font-size-s);
    line-height: 1.6;
}

.shortcut-keys {
    display: inline-flex;
    align-items: baseline;
    gap: 2px;
    flex-shrink: 0;
    min-width: 8rem;
}

.shortcut-plus {
    color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-2xs);
    padding: 0 1px;
}

kbd {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    min-width: 1.4em;
    padding: 0.1em var(--wa-space-2xs);
    font-family: var(--wa-font-family-sans);
    font-size: var(--wa-font-size-xs);
    line-height: 1.4;
    background: var(--wa-color-surface);
    border: 1px solid var(--wa-color-border);
    border-radius: var(--wa-border-radius-s);
    box-shadow: 0 1px 0 var(--wa-color-border);
    white-space: nowrap;
}

.shortcut-description {
    color: var(--wa-color-text);
}

/* -- Footer -- */

wa-popover > wa-divider {
    --width: var(--divider-size);
    --spacing: 0;
}

.settings-footer {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    column-gap: var(--wa-space-xs);
    padding: var(--wa-space-s);
    margin-right: 2rem;
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-text-quiet);
}

.settings-footer a {
    color: inherit;
    text-decoration: underline;
    text-decoration-style: dotted;
    text-underline-offset: 2px;
}

.settings-footer a:hover {
    color: var(--wa-color-text);
}

.logout-button {
    position: absolute;
    right: 0;
}

.settings-footer-version {
    white-space: nowrap;
}

.settings-footer-status {
    display: flex;
    align-items: center;
    gap: var(--wa-space-2xs);
    white-space: nowrap;
    text-decoration: none !important;
}

.settings-footer-status:hover {
    text-decoration: underline !important;
}

.settings-footer-status-next {
    cursor: pointer;
    margin-left: var(--wa-space-3xs);
}

.status-dot {
    display: inline-block;
    width: 8px;
    height: 8px;
    border-radius: 50%;
    flex-shrink: 0;
}

.settings-footer-status--ok .status-dot {
    background-color: var(--wa-color-success);
}

.settings-footer-status--warning .status-dot {
    background-color: var(--wa-color-warning);
}

.settings-footer-status--error .status-dot {
    background-color: var(--wa-color-danger);
}

.settings-footer-status--info .status-dot {
    background-color: var(--wa-color-primary);
}

/* -- Activated providers block -- */

.activated-providers-block {
    margin-bottom: var(--wa-space-l);
}

.activated-providers-block h4 {
    margin: 0 0 var(--wa-space-2xs);
}

.provider-switches {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-s);
}

.provider-switch-row {
    display: flex;
    flex-direction: column;
    align-items: flex-start;
    gap: var(--wa-space-2xs);
}

.provider-switch::part(label) {
    display: inline-flex;
    align-items: center;
    gap: var(--wa-space-xs);
}

.provider-switch-icon {
    font-size: 1.2em;
}

.provider-switch-line {
    display: flex;
    align-items: center;
    gap: var(--wa-space-s);
}

.transition-label {
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-neutral-fill-loud);
    font-style: italic;
}

.transition-spinner {
    font-size: 0.9em;
}

.hint {
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-neutral-fill-loud);
}

.hint.danger {
    color: var(--wa-color-danger-fill-loud);
}

</style>

<style>
/* Shared styles for settings sections (used by child components like NotificationSettings) */
.settings-sections .settings-section {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-m);
}

.settings-sections .settings-section-title {
    font-size: var(--wa-font-size-s);
    font-weight: var(--wa-font-weight-bold);
    margin: 0;
    color: var(--wa-color-brand);
    display: flex;
    align-items: center;
    gap: var(--wa-space-xs);
}

.settings-sections .setting-group {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-xs);
    > label ~ :not(label) {
        margin-left: var(--wa-space-s);
    }
}

.settings-sections .setting-group-label {
    font-size: var(--wa-font-size-m);
    font-weight: var(--wa-font-weight-semibold);
    display: flex;
    align-items: center;
    gap: var(--wa-space-xs);
}

.settings-sections .setting-group-hint {
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-quiet);
    font-style: italic;
}

.usage-file-input-row {
    display: flex;
    flex-wrap: wrap;
    gap: var(--wa-space-xs);
    align-items: center;

    wa-input {
        flex: 1;
    }

    wa-button {
        margin-left: auto;
    }
}

.usage-file-validation {
    margin-top: var(--wa-space-2xs);
}

.worktree-dir-input-row {
    display: flex;
    gap: var(--wa-space-2xs);
    align-items: center;

    wa-input {
        flex: 1;
    }
}

.worktree-dir-prefix {
    color: var(--wa-color-text-quiet);
    font-family: var(--wa-font-family-code);
    font-size: var(--wa-font-size-s);
    white-space: nowrap;
}

.usage-mode-explanation .setting-group-hint {
    /* Mode-explanation paragraphs are read once at the top of the section
       — keep the spacing tight so they read as a header block, not as a
       collection of independent settings. */
    margin-top: 0;
}

.provider-usage-block {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-m);
}

.provider-usage-block + .provider-usage-block {
    margin-top: var(--wa-space-m);
    padding-top: var(--wa-space-m);
    border-top: 1px solid var(--wa-color-surface-border);
}

.provider-usage-title {
    margin: 0;
    font-size: var(--wa-font-size-m);
    color: var(--wa-color-text-normal);
    display: flex;
    align-items: center;
    gap: var(--wa-space-xs);
}

.provider-option-icon {
    margin-right: 0.5em;
}

@media (width < 640px) {
    .settings-sections .settings-section-title {
        display: none;
    }
}

/* Sidebar footer fits, in order of decreasing width:
   1. toggle + command palette + full Settings (with the "Settings" label)
   2. toggle + command palette + Settings compacted to its gear icon  (here)
   3. toggle + Settings (the palette button drops out — see CommandPaletteButton)
   So this "compact Settings" threshold must stay ABOVE the palette button's
   own hide threshold; tune both together. (Only applies inside the `sidebar`
   container, i.e. the footer — not the home screen's fixed Settings button.) */
@container sidebar (width <= 15rem) {
    #settings-trigger {
        &::part(base) {
            padding: var(--wa-space-s);
        }
        & > span {
            display: none;
        }
    }
}
</style>
