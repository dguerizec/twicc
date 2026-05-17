import { ref, computed, watch, toValue } from 'vue'
import { useDataStore } from '../stores/data'
import { useAgentSettingsPresetsStore } from '../stores/agentSettingsPresets'
import { getProviderHelpers, getProviderStore } from '../providers'

// Sentinel value used by the popover selects to encode the "follow global
// default" choice. When set, the corresponding selected ref is null.
export const DEFAULT_SENTINEL = '__default__'

const SESSION_SETTING_FIELDS = ['permission_mode', 'selected_model', 'effort', 'thinking_enabled', 'claude_in_chrome', 'context_max']

/**
 * Per-session agent settings state — provider-agnostic.
 *
 * Hosts the per-session refs (``selected*`` / ``active*``), the watchers
 * that keep them in sync with the DB and the provider's consistency rules,
 * and the aggregate flags consumers need (``hasDropdownsChanged``,
 * ``startupChanges``, ``isContextMaxForced``…). Provider-specific behaviour
 * is intentionally NOT part of this composable: rendering hooks live on
 * ``BaseProviderHelpers`` (``getDefaultValueLabel``, ``isFieldDisabled``,
 * ``getSummaryParts``, …) and are called by the consuming components with a
 * ``context`` assembled from this composable's exposed state.
 *
 * @param {() => string | string} sessionIdSource - The current session id
 *   (ref, getter, or static value).
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

    // The model that's effectively in use right now: the user's selection if
    // any, otherwise the provider's global default. Used to feed
    // ``context.effectiveModel`` into rendering hooks (capability gates,
    // help text, etc.).
    const effectiveModel = computed(() => selectedModel.value ?? providerStore.value?.defaultModel)

    // Whether the provider's auto-promote rule would kick in for the user's
    // current selection in the popover. The rule itself is provider-specific
    // (Claude Code: 200K + model supports 1M + usage > 85% of 200K → 1M);
    // we delegate to the provider helper, evaluated against the SELECTED
    // value (or the global default if none selected) — NOT against the
    // persisted ``session.context_max``, which can diverge from the user's
    // current pick and would otherwise trigger false positives on drafts.
    const isContextMaxForced = computed(() => {
        if (!session.value || !providerHelpers.value) return false
        const baseValue = selectedContextMax.value ?? providerStore.value?.defaultContextMax
        return providerHelpers.value.isContextMaxAutoPromoted(
            session.value, baseValue, effectiveModel.value,
        )
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

    // The shape consumed by ``providerHelpers.getSummaryParts``. Held here
    // (not on the helpers) because it depends on this session's selections —
    // helpers operate over the rendered values, not the live refs.
    const summaryState = computed(() => {
        const pStore = providerStore.value
        return {
            selected: {
                selected_model: selectedModel.value,
                permission_mode: selectedPermissionMode.value,
                effort: selectedEffort.value,
                thinking_enabled: selectedThinking.value,
                claude_in_chrome: selectedClaudeInChrome.value,
                context_max: selectedContextMax.value,
            },
            defaults: {
                selected_model: pStore?.defaultModel,
                permission_mode: pStore?.defaultPermissionMode,
                effort: pStore?.defaultEffort,
                thinking_enabled: pStore?.defaultThinking,
                claude_in_chrome: pStore?.defaultClaudeInChrome,
                context_max: pStore?.defaultContextMax,
            },
        }
    })

    // ─── Presets ─────────────────────────────────────────────────────────────
    // Sourced from the cross-provider presets store, keyed by the session's
    // provider. The on-disk format is shared, only the file path varies.
    // The "Manage…" button toggles ``presetsDialogOpen`` to open the
    // ``AgentSettingsPresetsDialog`` for the current provider.
    const presetsStore = useAgentSettingsPresetsStore()
    const presets = computed(() => presetsStore.getPresets(session.value?.provider))
    const hasPresets = computed(() => presets.value.length > 0)
    const presetsDialogOpen = ref(false)

    function handlePresetSelect(event) {
        const item = event.detail?.item
        const value = item?.value
        if (value === undefined || value === null || value === '') return
        if (value === '__reset__') {
            resetAllToDefaults()
            return
        }
        if (value === '__manage__') {
            presetsDialogOpen.value = true
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

    // Resolve null → global default for a settings dict (so ``classify``
    // compares concrete values instead of "raw null vs explicit").
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
    // The popover pairs this with ``providerHelpers.getStartupWarningText``
    // to format the human warning.
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
        // derived
        effectiveModel,
        isContextMaxForced,
        // aggregates
        anySettingForced,
        hasDropdownsChanged,
        hasSettingsChanged,
        summaryState,
        startupChanges,
        // presets
        presets,
        hasPresets,
        presetsDialogOpen,
        handlePresetSelect,
        // handlers
        resetAllToDefaults,
        restoreSettings,
        resolveSettingsDefaults,
    }
}
