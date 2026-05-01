<script setup>
// SettingsPopover.vue - Settings button with popover panel
import { computed, nextTick, onBeforeUnmount, ref, useId, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useSettingsStore, SETTINGS_SCHEMA, getModelRegistry, modelSupports1m, modelSupportsEffortXhigh, modelSupportsEffortMax } from '../../stores/settings'
import { useDataStore } from '../../stores/data'
import { useAuthStore } from '../../stores/auth'
import { DISPLAY_MODE, COLOR_SCHEME, SESSION_TIME_FORMAT, DEFAULT_MAX_CACHED_SESSIONS, PERMISSION_MODE, PERMISSION_MODE_LABELS, PERMISSION_MODE_DESCRIPTIONS, getModelLabel, EFFORT, EFFORT_LABELS, THINKING, THINKING_LABELS, CLAUDE_IN_CHROME, CLAUDE_IN_CHROME_LABELS, CONTEXT_MAX, CONTEXT_MAX_LABELS, WA_THEME, WA_THEME_LABELS, WA_BRAND, WA_BRAND_LABELS } from '../../constants'
import NotificationSettings from './NotificationSettings.vue'
import AppTooltip from '../ui/AppTooltip.vue'
import ChangelogDialog from './ChangelogDialog.vue'
import ClaudePresetsDialog from './ClaudePresetsDialog.vue'
import { sendChangelogSeen, sendValidateUsageFile, sendValidateUsageDumpPath, sendValidateTmuxConfigPath } from '../../composables/useWebSocket'
import { vPopoverFocusFix } from '../../directives/vPopoverFocusFix'

const router = useRouter()
const store = useSettingsStore()
const dataStore = useDataStore()
const authStore = useAuthStore()

// Show logout button only when password-based auth is active
const showLogout = computed(() => authStore.passwordRequired && authStore.authenticated)
const logoutButtonId = useId()

function handleLogout() {
    router.push({ name: 'logout' })
}

// -- Section navigation --

const sections = [
    { id: 'global',        label: 'Global' },
    { id: 'claude',        label: 'Claude settings', navLabel: 'Claude', synced: true },
    { id: 'notifications', label: 'Notifications' },
    { id: 'sessions',      label: 'Sessions' },
    { id: 'title',         label: 'Title suggestion', navLabel: 'Titles', synced: true },
    { id: 'editor',        label: 'Editor' },
    { id: 'terminal',      label: 'Terminal' },
    { id: 'usage',         label: 'Claude quotas/usage', navLabel: 'Usage' },
]

const activeSection = ref('global')
const mobileShowContent = ref(false)

const activeSectionObj = computed(() =>
    sections.find(s => s.id === activeSection.value)
)

const activeSectionLabel = computed(() => {
    if (activeSection.value === 'shortcuts') return 'Keyboard shortcuts'
    return activeSectionObj.value?.label ?? ''
})

function selectSection(id) {
    activeSection.value = id
    mobileShowContent.value = true
    if (id === 'notifications') {
        nextTick(() => notificationSettingsRef.value?.sync())
    }
    if (id === 'usage') {
        usageFilePathInput.value = usageReadFilePath.value || ''
        usageFileValidation.value = null
        usageDumpPathInput.value = usageDumpFilePath.value || ''
        usageDumpValidation.value = null
    }
    if (id === 'terminal') {
        tmuxConfigPathInput.value = terminalTmuxConfigPath.value || ''
        tmuxConfigValidation.value = null
    }
}

function goBackToNav() {
    mobileShowContent.value = false
}

// -- Keyboard shortcuts data --

const shortcutGroups = computed(() => {
    const mod = store.isMac ? '⌘' : 'Ctrl'
    return [
        {
            label: 'Global',
            shortcuts: [
                { keys: [mod, 'K'], description: 'Open command palette' },
                { keys: [mod, 'Shift', 'F'], description: 'Open full-text search' },
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
                { keys: ['Quick triple Esc'], description: 'Emergency stop of the running Claude Code process' },
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
            label: 'Message input',
            shortcuts: [
                { keys: [mod, '↵'], description: 'Send message' },
                { keys: ['@'], description: 'Insert file path (after a space or at start)' },
                { keys: ['/'], description: 'Slash commands (at start of input)' },
                { keys: ['!'], description: 'Message history (at start of input)' },
                { keys: ['PageUp'], description: 'Message history (cursor on first line)' },
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
const claudePresetsDialogOpen = ref(false)
function openClaudePresetsDialog() {
    claudePresetsDialogOpen.value = true
}

// Settings from store
const displayMode = computed(() => store.getDisplayMode)
const fontSize = computed(() => store.getFontSize)
const colorScheme = computed(() => store.getColorScheme)
const sessionTimeFormat = computed(() => store.getSessionTimeFormat)
const showCosts = computed(() => store.areCostsShown)
const extraUsageOnlyWhenNeeded = computed(() => store.isExtraUsageOnlyWhenNeeded)
const maxCachedSessions = computed(() => store.getMaxCachedSessions)
const autoUnpinOnArchive = computed(() => store.isAutoUnpinOnArchive)
const titleGenerationEnabled = computed(() => store.isTitleGenerationEnabled)
const titleAutoApply = computed(() => store.isTitleAutoApply)
const titleSystemPrompt = computed(() => store.getTitleSystemPrompt)
const terminalUseTmux = computed(() => store.isTerminalUseTmux)
const terminalTmuxConfigPath = computed(() => store.getTerminalTmuxConfigPath)
const compactSessionList = computed(() => store.isCompactSessionList)
const claudeCodeDefaultPermissionMode = computed(() => store.getClaudeCodeDefaultPermissionMode)
const claudeCodeDefaultModel = computed(() => store.getClaudeCodeDefaultModel)
const claudeCodeDefaultEffort = computed(() => store.getClaudeCodeDefaultEffort)
const claudeCodeDefaultThinking = computed(() => store.getClaudeCodeDefaultThinking)
const claudeCodeDefaultClaudeInChrome = computed(() => store.getClaudeCodeDefaultClaudeInChrome)
const claudeCodeDefaultContextMax = computed(() => store.getClaudeCodeDefaultContextMax)
const waTheme = computed(() => store.getWaTheme)
const waBrand = computed(() => store.getWaBrand)
const showDiffs = computed(() => store.isShowDiffs)
const toolDiffWordWrap = computed(() => store.isToolDiffWordWrap)
const toolDiffSideBySide = computed(() => store.isToolDiffSideBySide)
const diffSideBySide = computed(() => store.isDiffSideBySide)
const editorWordWrap = computed(() => store.isEditorWordWrap)
const usageReadFileEnabled = computed(() => store.isClaudeCodeUsageReadFileEnabled)
const usageReadFilePath = computed(() => store.getClaudeCodeUsageReadFilePath)
const usageDumpFileEnabled = computed(() => store.isClaudeCodeUsageDumpFileEnabled)
const usageDumpFilePath = computed(() => store.getClaudeCodeUsageDumpFilePath)

// Usage file — local input + validation state
const usageFilePathInput = ref('')
const usageFileValidating = ref(false)
const usageFileValidation = ref(null) // { valid: boolean, message: string } | null
const usageFilePathModified = computed(() => usageFilePathInput.value.trim() !== (usageReadFilePath.value || ''))
const usageFileApplyIcon = computed(() => {
    if (usageFileValidation.value?.valid === false) return 'x-circle'
    if (usageFilePathModified.value) return 'triangle-exclamation'
    return 'check'
})

const usageDumpPathInput = ref('')
const usageDumpValidating = ref(false)
const usageDumpValidation = ref(null) // { valid: boolean, message: string } | null
const usageDumpPathModified = computed(() => usageDumpPathInput.value.trim() !== (usageDumpFilePath.value || ''))
const usageDumpApplyIcon = computed(() => {
    if (usageDumpValidation.value?.valid === false) return 'x-circle'
    if (usageDumpPathModified.value) return 'triangle-exclamation'
    return 'check'
})

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

// Check if the current prompt is the default
const isDefaultPrompt = computed(() => titleSystemPrompt.value === SETTINGS_SCHEMA.titleSystemPrompt)

// Server info for footer
const currentVersion = computed(() => dataStore.currentVersion)
const latestVersion = computed(() => dataStore.latestVersion)
const claudeStatus = computed(() => dataStore.claudeStatus)

/**
 * Status display configuration for Claude Code component statuses.
 * Maps Atlassian Statuspage status values to UI labels and CSS modifier classes.
 */
const CLAUDE_STATUS_DISPLAY = {
    operational: { label: 'Operational', modifier: 'ok' },
    degraded_performance: { label: 'Degraded', modifier: 'warning' },
    partial_outage: { label: 'Partial outage', modifier: 'warning' },
    major_outage: { label: 'Major outage', modifier: 'error' },
    under_maintenance: { label: 'Maintenance', modifier: 'info' },
}

const claudeStatusDisplay = computed(() => {
    return CLAUDE_STATUS_DISPLAY[claudeStatus.value] || { label: claudeStatus.value, modifier: 'ok' }
})

// Display mode options for the select
const displayModeOptions = [
    { value: DISPLAY_MODE.CONVERSATION, label: 'Conversation' },
    { value: DISPLAY_MODE.SIMPLIFIED, label: 'Simplified' },
    { value: DISPLAY_MODE.NORMAL, label: 'Detailed' },
    { value: DISPLAY_MODE.DEBUG, label: 'Debug' },
]

// Permission mode options for the select
const permissionModeOptions = Object.values(PERMISSION_MODE).map(value => ({
    value,
    label: PERMISSION_MODE_LABELS[value],
    description: PERMISSION_MODE_DESCRIPTIONS[value],
}))

// Model options for the select — built from the registry
const modelRegistryOptions = computed(() => {
    const registry = getModelRegistry()
    return {
        latest: registry.filter(e => e.latest),
        older: registry.filter(e => !e.latest),
    }
})

// Effort options for the select
const effortOptions = Object.values(EFFORT).map(value => ({
    value,
    label: EFFORT_LABELS[value],
}))

// Thinking options for the select (use string values for wa-select compatibility)
const thinkingOptions = [
    { value: 'true', label: THINKING_LABELS[true] },
    { value: 'false', label: THINKING_LABELS[false] },
]

// Claude in Chrome options for the select (use string values for wa-select compatibility)
const claudeInChromeOptions = [
    { value: 'true', label: CLAUDE_IN_CHROME_LABELS[true] },
    { value: 'false', label: CLAUDE_IN_CHROME_LABELS[false] },
]

// Context max options for the select (use string values for wa-select compatibility)
const contextMaxOptions = Object.values(CONTEXT_MAX).map(value => ({
    value: String(value),
    label: CONTEXT_MAX_LABELS[value],
}))

function formatRetirementDate(isoDate) {
    return new Date(isoDate + 'T00:00:00').toLocaleDateString(undefined, {
        month: 'short', day: 'numeric', year: 'numeric',
    })
}

const claudeCodeDefaultModelSupports1m = computed(() => modelSupports1m(claudeCodeDefaultModel.value))
const claudeCodeDefaultModelSupportsEffortXhigh = computed(() => modelSupportsEffortXhigh(claudeCodeDefaultModel.value))
const claudeCodeDefaultModelSupportsEffortMax = computed(() => modelSupportsEffortMax(claudeCodeDefaultModel.value))

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

function onUsageFileEnabledChange(event) {
    store.setClaudeCodeUsageReadFileEnabled(event.target.checked)
}

function onUsageFilePathInputChange(event) {
    usageFilePathInput.value = event.target.value
    // Clear previous validation error when user edits
    if (usageFileValidation.value) usageFileValidation.value = null
}

async function onUsageFilePathApply() {
    const path = usageFilePathInput.value.trim()
    if (!path) {
        usageFileValidation.value = null
        store.setClaudeCodeUsageReadFilePath('')
        return
    }
    usageFileValidating.value = true
    usageFileValidation.value = null
    try {
        const result = await sendValidateUsageFile(path)
        if (result.valid) {
            store.setClaudeCodeUsageReadFilePath(path)
        } else {
            usageFileValidation.value = result
        }
    } finally {
        usageFileValidating.value = false
    }
}

function onUsageDumpEnabledChange(event) {
    store.setClaudeCodeUsageDumpFileEnabled(event.target.checked)
}

function onUsageDumpPathInputChange(event) {
    usageDumpPathInput.value = event.target.value
    if (usageDumpValidation.value) usageDumpValidation.value = null
}

async function onUsageDumpPathApply() {
    const path = usageDumpPathInput.value.trim()
    if (!path) {
        usageDumpValidation.value = null
        store.setClaudeCodeUsageDumpFilePath('')
        return
    }
    usageDumpValidating.value = true
    usageDumpValidation.value = null
    try {
        const result = await sendValidateUsageDumpPath(path)
        if (result.valid) {
            store.setClaudeCodeUsageDumpFilePath(path)
        } else {
            usageDumpValidation.value = result
        }
    } finally {
        usageDumpValidating.value = false
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
 * Handle title system prompt change.
 */
function onTitleSystemPromptChange(event) {
    store.setTitleSystemPrompt(event.target.value)
}

/**
 * Toggle terminal tmux persistence.
 */
function onTmuxChange(event) {
    store.setTerminalUseTmux(event.target.checked)
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
 * Handle Claude Code default permission mode change.
 */
function onClaudeCodeDefaultPermissionModeChange(event) {
    store.setClaudeCodeDefaultPermissionMode(event.target.value)
}

function onClaudeCodeDefaultModelChange(event) {
    const newModel = event.target.value
    store.setClaudeCodeDefaultModel(newModel)
    if (!modelSupports1m(newModel) && store.getClaudeCodeDefaultContextMax === CONTEXT_MAX.EXTENDED) {
        store.setClaudeCodeDefaultContextMax(CONTEXT_MAX.DEFAULT)
    }
    if (store.claudeCodeDefaultEffort === EFFORT.MAX && !modelSupportsEffortMax(newModel)) {
        store.setClaudeCodeDefaultEffort(modelSupportsEffortXhigh(newModel) ? EFFORT.X_HIGH : EFFORT.HIGH)
    } else if (store.claudeCodeDefaultEffort === EFFORT.X_HIGH && !modelSupportsEffortXhigh(newModel)) {
        store.setClaudeCodeDefaultEffort(EFFORT.HIGH)
    }
}

function onClaudeCodeDefaultEffortChange(event) {
    store.setClaudeCodeDefaultEffort(event.target.value)
}

function onClaudeCodeDefaultThinkingChange(event) {
    store.setClaudeCodeDefaultThinking(event.target.value === 'true')
}

function onClaudeCodeDefaultClaudeInChromeChange(event) {
    store.setClaudeCodeDefaultClaudeInChrome(event.target.value === 'true')
}

function onClaudeCodeDefaultContextMaxChange(event) {
    store.setClaudeCodeDefaultContextMax(Number(event.target.value))
}

/**
 * Toggle compact session list.
 */
function onCompactSessionListChange(event) {
    store.setCompactSessionList(event.target.checked)
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
 * Reset title system prompt to default.
 */
function resetTitleSystemPrompt() {
    store.resetTitleSystemPrompt()
}

/**
 * Called when popover opens - reset mobile view and refresh notification state.
 */
function onPopoverShow() {
    mobileShowContent.value = false
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
    <wa-popover v-popover-focus-fix for="settings-trigger" placement="top" class="settings-popover" @wa-show="onPopoverShow">
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
                    <div class="setting-group">
                        <label class="setting-group-label">Show costs</label>
                        <wa-switch
                            :checked="showCosts"
                            @change="onShowCostsChange"
                            size="small"
                        >Enabled</wa-switch>
                    </div>
                </section>

                <!-- Claude Settings Section -->
                <section v-if="activeSection === 'claude'" class="settings-section">
                    <h3 class="settings-section-title">Claude settings <wa-icon name="cloud" class="synced-icon"></wa-icon></h3>
                    <div class="setting-group">
                        <label class="setting-group-label">Default permission mode</label>
                        <wa-select
                            :value.prop="claudeCodeDefaultPermissionMode"
                            @change="onClaudeCodeDefaultPermissionModeChange"
                            size="small"
                        >
                            <wa-option
                                v-for="option in permissionModeOptions"
                                :key="option.value"
                                :value="option.value"
                                :label="option.label"
                            >
                                <span>{{ option.label }}</span>
                                <span class="option-description">{{ option.description }}</span>
                            </wa-option>
                        </wa-select>
                    </div>
                    <div class="setting-group">
                        <label class="setting-group-label">Default model</label>
                        <wa-select
                            :value.prop="claudeCodeDefaultModel"
                            @change="onClaudeCodeDefaultModelChange"
                            size="small"
                        >
                            <wa-option
                                v-for="entry in modelRegistryOptions.latest"
                                :key="entry.selected_model"
                                :value="entry.selected_model"
                            >
                                {{ getModelLabel(entry.selected_model) }} (latest: {{ entry.version }})
                            </wa-option>
                            <wa-divider v-if="modelRegistryOptions.older.length"></wa-divider>
                            <wa-option
                                v-for="entry in modelRegistryOptions.older"
                                :key="entry.selected_model"
                                :value="entry.selected_model"
                            >
                                {{ getModelLabel(entry.selected_model) }} (until {{ formatRetirementDate(entry.retirement_date) }})
                            </wa-option>
                        </wa-select>
                    </div>
                    <div class="setting-group">
                        <label class="setting-group-label">Default context size</label>
                        <wa-select
                            :value.prop="String(claudeCodeDefaultContextMax)"
                            @change="onClaudeCodeDefaultContextMaxChange"
                            size="small"
                        >
                            <wa-option
                                v-for="option in contextMaxOptions"
                                :key="option.value"
                                :value="option.value"
                                :disabled="option.value === String(CONTEXT_MAX.EXTENDED) && !claudeCodeDefaultModelSupports1m"
                            >
                                {{ option.label }}{{ option.value === String(CONTEXT_MAX.EXTENDED) && !claudeCodeDefaultModelSupports1m ? ' (not available)' : '' }}
                            </wa-option>
                        </wa-select>
                    </div>
                    <div class="setting-group">
                        <label class="setting-group-label">Default effort</label>
                        <wa-select
                            :value.prop="claudeCodeDefaultEffort"
                            @change="onClaudeCodeDefaultEffortChange"
                            size="small"
                        >
                            <wa-option
                                v-for="option in effortOptions"
                                :key="option.value"
                                :value="option.value"
                                :disabled="(option.value === EFFORT.X_HIGH && !claudeCodeDefaultModelSupportsEffortXhigh) || (option.value === EFFORT.MAX && !claudeCodeDefaultModelSupportsEffortMax)"
                            >
                                {{ option.label }}{{ ((option.value === EFFORT.X_HIGH && !claudeCodeDefaultModelSupportsEffortXhigh) || (option.value === EFFORT.MAX && !claudeCodeDefaultModelSupportsEffortMax)) ? ' (not available)' : '' }}
                            </wa-option>
                        </wa-select>
                    </div>
                    <div class="setting-group">
                        <label class="setting-group-label">Default thinking</label>
                        <wa-select
                            :value.prop="String(claudeCodeDefaultThinking)"
                            @change="onClaudeCodeDefaultThinkingChange"
                            size="small"
                        >
                            <wa-option
                                v-for="option in thinkingOptions"
                                :key="option.value"
                                :value="option.value"
                            >
                                {{ option.label }}
                            </wa-option>
                        </wa-select>
                    </div>
                    <div class="setting-group">
                        <label class="setting-group-label">Default Chrome MCP</label>
                        <wa-select
                            :value.prop="String(claudeCodeDefaultClaudeInChrome)"
                            @change="onClaudeCodeDefaultClaudeInChromeChange"
                            size="small"
                        >
                            <wa-option
                                v-for="option in claudeInChromeOptions"
                                :key="option.value"
                                :value="option.value"
                            >
                                {{ option.label }}
                            </wa-option>
                        </wa-select>
                    </div>
                    <div class="setting-group">
                        <label class="setting-group-label">Presets</label>
                        <span class="setting-group-hint">
                            Define bundles of Claude settings to quickly apply to a session
                        </span>
                        <wa-button size="small" @click="openClaudePresetsDialog">
                            <wa-icon slot="start" name="sliders"></wa-icon>
                            Manage presets…
                        </wa-button>
                    </div>
                </section>

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
                        >Enabled (Haiku)</wa-switch>
                        <wa-switch
                            v-if="titleGenerationEnabled"
                            :checked="titleAutoApply"
                            @change="onTitleAutoApplyChange"
                            size="small"
                        >Auto-apply on new sessions</wa-switch>
                        <div v-if="titleGenerationEnabled" class="title-prompt-section">
                            <label class="setting-group-label">System prompt</label>
                            <wa-textarea
                                :value.prop="titleSystemPrompt"
                                @input="onTitleSystemPromptChange"
                                size="small"
                                rows="7"
                                resize="vertical"
                                class="title-prompt-textarea"
                            ></wa-textarea>
                            <div class="title-prompt-hint">
                                <span>Use <code>{text}</code> as placeholder.</span>
                                <wa-button
                                    v-if="!isDefaultPrompt"
                                    variant="neutral"
                                    appearance="outlined"
                                    size="small"
                                    @click.stop="resetTitleSystemPrompt"
                                >Reset to default</wa-button>
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
                        <span class="setting-group-hint">Tmux sessions are destroyed when Claude sessions are archived.</span>
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

                <!-- Claude quotas/usage Section -->
                <section v-if="activeSection === 'usage'" class="settings-section">
                    <h3 class="settings-section-title">Claude quotas/usage</h3>
                    <div class="setting-group">
                        <label class="setting-group-label">Show extra usage quota</label>
                        <wa-switch
                            :checked="extraUsageOnlyWhenNeeded"
                            @change="onExtraUsageOnlyWhenNeededChange"
                            size="small"
                        >Only when needed</wa-switch>
                    </div>
                    <div class="setting-group">
                        <label class="setting-group-label">Read usage from file <wa-icon name="cloud" class="synced-icon"></wa-icon></label>
                        <wa-switch
                            :checked="usageReadFileEnabled"
                            @change="onUsageFileEnabledChange"
                            size="small"
                            :disabled="usageDumpFileEnabled"
                        >Enabled</wa-switch>
                        <span class="setting-group-hint">
                            The Anthropic usage API is heavily rate-limited. If you already fetch usage data
                            from your own script and save the raw API response to a JSON file, you can provide
                            its path here. TwiCC will read from this file instead of calling the API directly.
                        </span>
                        <template v-if="usageReadFileEnabled">
                            <div class="usage-file-input-row">
                                <wa-input
                                    :value="usageFilePathInput"
                                    @input="onUsageFilePathInputChange"
                                    @keydown.enter="onUsageFilePathApply"
                                    placeholder="/path/to/usage.json"
                                    size="small"
                                    :disabled="usageFileValidating"
                                ></wa-input>
                                <wa-button
                                    size="small"
                                    variant="neutral"
                                    @click="onUsageFilePathApply"
                                    :disabled="usageFileValidating"
                                >
                                    <wa-spinner v-if="usageFileValidating" slot="start"></wa-spinner>
                                    <wa-icon v-else :name="usageFileApplyIcon" slot="start"></wa-icon>
                                    Apply
                                </wa-button>
                            </div>
                            <span class="setting-group-hint">Press Apply or Enter to validate and save the path.</span>
                            <wa-callout
                                v-if="usageFileValidation && !usageFileValidation.valid"
                                variant="danger"
                                size="small"
                                class="usage-file-validation"
                            >{{ usageFileValidation.message }}</wa-callout>
                        </template>
                    </div>
                    <div class="setting-group">
                        <label class="setting-group-label">Dump usage to file <wa-icon name="cloud" class="synced-icon"></wa-icon></label>
                        <wa-switch
                            :checked="usageDumpFileEnabled"
                            @change="onUsageDumpEnabledChange"
                            size="small"
                            :disabled="usageReadFileEnabled"
                        >Enabled</wa-switch>
                        <span class="setting-group-hint">
                            Save the raw API response to a JSON file each time TwiCC fetches usage data.
                            Useful if you want to share the data with other tools without extra API calls.
                        </span>
                        <template v-if="usageDumpFileEnabled">
                            <div class="usage-file-input-row">
                                <wa-input
                                    :value="usageDumpPathInput"
                                    @input="onUsageDumpPathInputChange"
                                    @keydown.enter="onUsageDumpPathApply"
                                    placeholder="/path/to/usage-dump.json"
                                    size="small"
                                    :disabled="usageDumpValidating"
                                ></wa-input>
                                <wa-button
                                    size="small"
                                    variant="neutral"
                                    @click="onUsageDumpPathApply"
                                    :disabled="usageDumpValidating"
                                >
                                    <wa-spinner v-if="usageDumpValidating" slot="start"></wa-spinner>
                                    <wa-icon v-else :name="usageDumpApplyIcon" slot="start"></wa-icon>
                                    Apply
                                </wa-button>
                            </div>
                            <span class="setting-group-hint">Press Apply or Enter to validate and save the path.</span>
                            <wa-callout
                                v-if="usageDumpValidation && !usageDumpValidation.valid"
                                variant="danger"
                                size="small"
                                class="usage-file-validation"
                            >{{ usageDumpValidation.message }}</wa-callout>
                        </template>
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
            <a href="https://github.com/sponsors/twidi" target="_blank" rel="noopener" class="settings-footer-sponsor">
                <span class="settings-footer-sponsor-icon"></span>
                Sponsor
            </a>
            ·
            <a
                href="https://status.claude.com/"
                target="_blank"
                rel="noopener"
                class="settings-footer-status"
                :class="`settings-footer-status--${claudeStatusDisplay.modifier}`"
                id="claude-status"
            >
                <span class="status-dot"></span>
                CC: {{ claudeStatusDisplay.label }}
            </a>
            <AppTooltip for="claude-status">Claude code status on Anthropic's side</AppTooltip>
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
    <ClaudePresetsDialog v-model:open="claudePresetsDialogOpen" />
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

@media (width < 640px) {
    .settings-sections .settings-section-title {
        display: none;
    }
}

@container sidebar (width <= 13rem) {
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
