import { ref, computed, watch, toValue } from 'vue'
import { useDataStore } from '../stores/data'
import { getProviderHelpers, getProviderStore } from '../providers'
import { CONTEXT_MAX as CLAUDE_CODE_CONTEXT_MAX } from '../providers/claude_code/constants'

// Sentinel value used by the popover selects to encode the "follow global
// default" choice. When set, the corresponding selected ref is null.
export const DEFAULT_SENTINEL = '__default__'

const SESSION_SETTING_FIELDS = ['permission_mode', 'selected_model', 'effort', 'thinking_enabled', 'claude_in_chrome', 'context_max']

/**
 * Per-session agent settings state, helpers, and watchers — extracted from
 * MessageInput so the agent settings summary and popover can render
 * independently while sharing the same source of truth.
 *
 * The composable resolves provider helpers/store from ``session.provider``
 * so each session works with its own provider's catalog (choices, defaults,
 * label formatter, capability gates). For agent-setting fields a provider
 * doesn't expose, the corresponding select/option is silently dropped by
 * the consuming component via ``providerHelpers.value.supportsAgentSetting``.
 *
 * @param {() => string | string} sessionIdSource - The current session id (ref, getter, or static).
 * @returns Reactive bag of refs/computeds/handlers consumed by
 *   ``AgentSettingsSummary``, ``AgentSettingsPopover`` and ``MessageInput``.
 */
export function useSessionAgentSettings(sessionIdSource) {
    const store = useDataStore()

    const sessionId = computed(() => toValue(sessionIdSource))
    const session = computed(() => store.getSession(sessionId.value))
    const processState = computed(() => store.getProcessState(sessionId.value))
    const isStarting = computed(() => processState.value?.state === 'starting')
    const processIsActive = computed(() => {
        const state = processState.value?.state
        return state === 'assistant_turn' || state === 'user_turn'
    })

    const providerHelpers = computed(() => getProviderHelpers(session.value?.provider))
    const providerStore = computed(() => getProviderStore(session.value?.provider))

    // ─── Selected (per-session override) and active (currently applied) refs ──
    // null = "follow global default", explicit value = "forced for this session"
    const selectedPermissionMode = ref(null)
    const selectedModel = ref(null)
    const selectedEffort = ref(null)
    const selectedThinking = ref(null)
    const selectedClaudeInChrome = ref(null)
    const selectedContextMax = ref(null)

    const activePermissionMode = ref(null)
    const activeModel = ref(null)
    const activeEffort = ref(null)
    const activeThinking = ref(null)
    const activeClaudeInChrome = ref(null)
    const activeContextMax = ref(null)

    const SELECTED_REFS = {
        permission_mode: selectedPermissionMode,
        selected_model: selectedModel,
        effort: selectedEffort,
        thinking_enabled: selectedThinking,
        claude_in_chrome: selectedClaudeInChrome,
        context_max: selectedContextMax,
    }
    const ACTIVE_REFS = {
        permission_mode: activePermissionMode,
        selected_model: activeModel,
        effort: activeEffort,
        thinking_enabled: activeThinking,
        claude_in_chrome: activeClaudeInChrome,
        context_max: activeContextMax,
    }

    // ─── Provider-driven catalogs ────────────────────────────────────────────
    const permissionModeOptions = computed(() => providerHelpers.value?.getFieldChoices('permission_mode') ?? [])
    const effortOptions = computed(() => providerHelpers.value?.getFieldChoices('effort') ?? [])
    const thinkingOptions = computed(() => providerHelpers.value?.getFieldChoices('thinking_enabled') ?? [])
    const claudeInChromeOptions = computed(() => providerHelpers.value?.getFieldChoices('claude_in_chrome') ?? [])
    const contextMaxOptions = computed(() => providerHelpers.value?.getFieldChoices('context_max') ?? [])

    const modelRegistryOptions = computed(() => {
        const registry = providerHelpers.value?.getModelRegistry() ?? []
        return {
            latest: registry.filter(e => e.latest),
            older: registry.filter(e => !e.latest),
        }
    })

    // ─── Default-value labels (rendered as "Default: …" rows) ────────────────
    const defaultModelLabel = computed(() => {
        const helpers = providerHelpers.value
        if (!helpers) return ''
        const model = providerStore.value?.defaultModel
        const registry = helpers.getModelRegistry?.() ?? []
        const entry = registry.find(e => e.selected_model === model)
        if (entry) {
            return entry.latest
                ? `${helpers.getModelLabel(model)} (latest: ${entry.version})`
                : `${helpers.getModelLabel(model)}`
        }
        return helpers.getModelLabel(model)
    })
    const defaultContextMaxLabel = computed(() => providerHelpers.value?.getChoiceLabel('context_max', providerStore.value?.defaultContextMax) ?? '')
    const defaultEffortLabel = computed(() => providerHelpers.value?.getChoiceLabel('effort', providerStore.value?.defaultEffort) ?? '')
    const defaultThinkingLabel = computed(() => providerHelpers.value?.getChoiceLabel('thinking_enabled', providerStore.value?.defaultThinking) ?? '')
    const defaultClaudeInChromeLabel = computed(() => providerHelpers.value?.getChoiceLabel('claude_in_chrome', providerStore.value?.defaultClaudeInChrome) ?? '')
    const defaultPermissionModeLabel = computed(() => providerHelpers.value?.getChoiceLabel('permission_mode', providerStore.value?.defaultPermissionMode) ?? '')

    // ─── Capability/state derivations on top of selections ───────────────────
    // Whether the auto-promote-to-1M rule is active for this session. The rule
    // itself lives on the provider's helpers via ``getEffectiveContextMax``;
    // we just detect that the effective value diverges from what the user
    // actually picked (or defaulted to). The current selection feeds the
    // helper so it respects the live UI choice, not just the persisted one.
    const isContextMaxForced = computed(() => {
        const baseValue = selectedContextMax.value ?? providerStore.value?.defaultContextMax
        const effectiveModel = selectedModel.value ?? providerStore.value?.defaultModel
        return store.getEffectiveContextMax(sessionId.value, effectiveModel) !== baseValue
    })

    // The next three flags are Claude-only capability gates. We invoke them
    // through ``providerHelpers`` (optional chaining on the method) so a
    // provider that doesn't ship the method simply yields falsy — equivalent
    // to "this concept doesn't apply", which is what the disabled-option
    // checks expect.
    const isContextMaxForcedByModel = computed(() => {
        const effectiveModel = selectedModel.value ?? providerStore.value?.defaultModel
        return !providerHelpers.value?.modelSupports1m?.(effectiveModel)
    })
    const isEffortXhighAvailable = computed(() => {
        const effectiveModel = selectedModel.value ?? providerStore.value?.defaultModel
        return providerHelpers.value?.modelSupportsEffortXhigh?.(effectiveModel) ?? false
    })
    const isEffortMaxAvailable = computed(() => {
        const effectiveModel = selectedModel.value ?? providerStore.value?.defaultModel
        return providerHelpers.value?.modelSupportsEffortMax?.(effectiveModel) ?? false
    })

    const contextMaxSelectValue = computed(() => {
        if (isContextMaxForced.value) return String(CLAUDE_CODE_CONTEXT_MAX.EXTENDED)
        return selectedContextMax.value === null ? DEFAULT_SENTINEL : String(selectedContextMax.value)
    })

    // ─── Aggregate state ─────────────────────────────────────────────────────
    const anySettingForced = computed(() =>
        selectedPermissionMode.value !== null ||
        selectedModel.value !== null ||
        selectedEffort.value !== null ||
        selectedThinking.value !== null ||
        selectedClaudeInChrome.value !== null ||
        selectedContextMax.value !== null
    )

    const hasDropdownsChanged = computed(() =>
        selectedModel.value !== activeModel.value ||
        selectedPermissionMode.value !== activePermissionMode.value ||
        selectedEffort.value !== activeEffort.value ||
        selectedThinking.value !== activeThinking.value ||
        selectedClaudeInChrome.value !== activeClaudeInChrome.value ||
        selectedContextMax.value !== activeContextMax.value
    )

    const hasSettingsChanged = computed(() => hasDropdownsChanged.value)

    // ─── Settings summary parts (rendered by AgentSettingsSummary) ───────────
    const settingsSummaryParts = computed(() => {
        const helpers = providerHelpers.value
        const pStore = providerStore.value
        if (!helpers || !pStore) return []

        const effectiveModel = selectedModel.value ?? pStore.defaultModel
        const effectiveContextMax = selectedContextMax.value ?? pStore.defaultContextMax
        const effectiveEffort = selectedEffort.value ?? pStore.defaultEffort
        const effectiveThinking = selectedThinking.value ?? pStore.defaultThinking
        const effectiveChrome = selectedClaudeInChrome.value ?? pStore.defaultClaudeInChrome
        const effectivePermission = selectedPermissionMode.value ?? pStore.defaultPermissionMode

        const modelLabel = helpers.getModelLabel(effectiveModel)
        const modelDisplay = effectiveContextMax === CLAUDE_CODE_CONTEXT_MAX.EXTENDED
            ? `${modelLabel}[1m]`
            : modelLabel
        const modelForced = (selectedModel.value !== null && selectedModel.value !== pStore.defaultModel)
            || (selectedContextMax.value !== null && selectedContextMax.value !== pStore.defaultContextMax)

        return [
            { text: modelDisplay, forced: modelForced },
            { text: helpers.getChoiceDisplayLabel('effort', effectiveEffort), forced: selectedEffort.value !== null && selectedEffort.value !== pStore.defaultEffort },
            { text: helpers.getChoiceDisplayLabel('thinking_enabled', effectiveThinking), forced: selectedThinking.value !== null && selectedThinking.value !== pStore.defaultThinking },
            { text: helpers.getChoiceLabel('permission_mode', effectivePermission), forced: selectedPermissionMode.value !== null && selectedPermissionMode.value !== pStore.defaultPermissionMode },
            { text: helpers.getChoiceDisplayLabel('claude_in_chrome', effectiveChrome), forced: selectedClaudeInChrome.value !== null && selectedClaudeInChrome.value !== pStore.defaultClaudeInChrome },
        ]
    })

    // ─── Presets ─────────────────────────────────────────────────────────────
    // Sourced from the session's provider — each provider that supports
    // presets exposes ``settingsPresets`` on its store; others get an empty
    // array and the dropdown's "Presets" group renders nothing.
    const presets = computed(() => providerStore.value?.settingsPresets ?? [])
    const hasPresets = computed(() => presets.value.length > 0)
    const claudePresetsDialogOpen = ref(false)

    function handlePresetSelect(event) {
        const item = event.detail?.item
        const value = item?.value
        if (value === undefined || value === null || value === '') return
        if (value === '__reset__') {
            resetAllToDefaults()
            return
        }
        if (value === '__manage__') {
            claudePresetsDialogOpen.value = true
            return
        }
        const index = Number(value)
        if (!Number.isInteger(index)) return
        const preset = presets.value[index]
        if (!preset) return
        selectedModel.value = preset.model
        selectedContextMax.value = preset.context_max
        selectedEffort.value = preset.effort
        selectedThinking.value = preset.thinking
        selectedPermissionMode.value = preset.permission_mode
        selectedClaudeInChrome.value = preset.claude_in_chrome
    }

    function resetAllToDefaults() {
        selectedPermissionMode.value = null
        selectedModel.value = null
        selectedEffort.value = null
        selectedThinking.value = null
        selectedClaudeInChrome.value = null
        selectedContextMax.value = null
    }

    function restoreSettings() {
        selectedModel.value = activeModel.value
        selectedPermissionMode.value = activePermissionMode.value
        selectedEffort.value = activeEffort.value
        selectedThinking.value = activeThinking.value
        selectedClaudeInChrome.value = activeClaudeInChrome.value
        selectedContextMax.value = activeContextMax.value
    }

    // Resolve null → global default for a settings dict (so classify compares
    // concrete values instead of "raw null vs explicit").
    function resolveSettingsDefaults(settings) {
        const pStore = providerStore.value
        return {
            permission_mode: settings.permission_mode ?? pStore?.defaultPermissionMode,
            selected_model: settings.selected_model ?? pStore?.defaultModel,
            effort: settings.effort ?? pStore?.defaultEffort,
            thinking_enabled: settings.thinking_enabled ?? pStore?.defaultThinking,
            claude_in_chrome: settings.claude_in_chrome ?? pStore?.defaultClaudeInChrome,
            context_max: settings.context_max ?? pStore?.defaultContextMax,
        }
    }

    // ─── Startup-category change detection ───────────────────────────────────
    // Returns ``{ live, idle, startup }`` arrays of changed fields when a
    // process is active and the user has pending changes, else ``null``.
    // Consumers pair this with the provider's "process display name" to
    // build a human warning string.
    const startupChanges = computed(() => {
        if (!processIsActive.value || !hasDropdownsChanged.value) return null

        const current = resolveSettingsDefaults({
            permission_mode: activePermissionMode.value,
            selected_model: activeModel.value,
            effort: activeEffort.value,
            thinking_enabled: activeThinking.value,
            claude_in_chrome: activeClaudeInChrome.value,
            context_max: activeContextMax.value,
        })
        const requested = resolveSettingsDefaults({
            permission_mode: selectedPermissionMode.value,
            selected_model: selectedModel.value,
            effort: selectedEffort.value,
            thinking_enabled: selectedThinking.value,
            claude_in_chrome: selectedClaudeInChrome.value,
            context_max: selectedContextMax.value,
        })
        const changes = providerHelpers.value?.classifyAgentSettingsChanges(current, requested)
        return changes ?? { live: [], idle: [], startup: [] }
    })

    // ─── Watchers ────────────────────────────────────────────────────────────
    // Reset selected/active to the session's persisted values when sessionId
    // changes (and on first run).
    watch(sessionId, () => {
        const sess = store.getSession(sessionId.value)
        selectedPermissionMode.value = sess?.permission_mode ?? null
        selectedModel.value = sess?.selected_model ?? null
        selectedEffort.value = sess?.effort ?? null
        selectedThinking.value = sess?.thinking_enabled ?? null
        selectedClaudeInChrome.value = sess?.claude_in_chrome ?? null
        selectedContextMax.value = sess?.context_max ?? null
        activePermissionMode.value = selectedPermissionMode.value
        activeModel.value = selectedModel.value
        activeEffort.value = selectedEffort.value
        activeThinking.value = selectedThinking.value
        activeClaudeInChrome.value = selectedClaudeInChrome.value
        activeContextMax.value = selectedContextMax.value
    }, { immediate: true })

    // React when session data arrives from backend (e.g. after save or watcher
    // creates the row). Update active values to track DB; don't overwrite the
    // user's selection while a process is active.
    for (const field of SESSION_SETTING_FIELDS) {
        watch(
            () => store.getSession(sessionId.value)?.[field],
            (newValue) => {
                if (newValue === undefined) return
                ACTIVE_REFS[field].value = newValue
                if (!processIsActive.value) {
                    SELECTED_REFS[field].value = newValue
                }
            }
        )
    }

    // Keep the (selectedModel, contextMax, effort) triple consistent against
    // the provider's rules. The helper enforces retired-model upgrade, then
    // contextMax / effort demotion against the model's capabilities. Fires
    // immediately so a session loading with stale settings gets corrected on
    // mount.
    watch(
        () => ({
            selectedModel: selectedModel.value ?? providerStore.value?.defaultModel,
            contextMax: selectedContextMax.value ?? providerStore.value?.defaultContextMax,
            effort: selectedEffort.value ?? providerStore.value?.defaultEffort,
        }),
        (current) => {
            const helpers = providerHelpers.value
            if (!helpers) return
            const adjusted = helpers.enforceAgentSettingsConsistency(current)
            if (adjusted.selectedModel !== current.selectedModel) {
                selectedModel.value = adjusted.selectedModel
                activeModel.value = adjusted.selectedModel
            }
            if (adjusted.contextMax !== current.contextMax) {
                selectedContextMax.value = adjusted.contextMax
                activeContextMax.value = adjusted.contextMax
            }
            if (adjusted.effort !== current.effort) {
                selectedEffort.value = adjusted.effort
                activeEffort.value = adjusted.effort
            }
        },
        { immediate: true }
    )

    return {
        // context
        sessionId,
        session,
        processState,
        isStarting,
        processIsActive,
        providerHelpers,
        providerStore,
        // selected/active
        selectedPermissionMode,
        selectedModel,
        selectedEffort,
        selectedThinking,
        selectedClaudeInChrome,
        selectedContextMax,
        activePermissionMode,
        activeModel,
        activeEffort,
        activeThinking,
        activeClaudeInChrome,
        activeContextMax,
        SELECTED_REFS,
        ACTIVE_REFS,
        // catalogs
        permissionModeOptions,
        effortOptions,
        thinkingOptions,
        claudeInChromeOptions,
        contextMaxOptions,
        modelRegistryOptions,
        // default labels
        defaultModelLabel,
        defaultContextMaxLabel,
        defaultEffortLabel,
        defaultThinkingLabel,
        defaultClaudeInChromeLabel,
        defaultPermissionModeLabel,
        // capability/state
        isContextMaxForced,
        isContextMaxForcedByModel,
        isEffortXhighAvailable,
        isEffortMaxAvailable,
        contextMaxSelectValue,
        // aggregates
        anySettingForced,
        hasDropdownsChanged,
        hasSettingsChanged,
        settingsSummaryParts,
        startupChanges,
        // presets
        presets,
        hasPresets,
        claudePresetsDialogOpen,
        handlePresetSelect,
        // handlers
        resetAllToDefaults,
        restoreSettings,
        resolveSettingsDefaults,
    }
}
