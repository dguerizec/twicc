import { ref, computed, watch, toValue } from 'vue'
import { useDataStore } from '../stores/data'
import { useAgentSettingsPresetsStore } from '../stores/agentSettingsPresets'
import { getProviderHelpers, getProviderStore } from '../providers'
import { resolveProjectAgentDefaults, ancestorChain } from '../utils/projectAgentDefaults'

// Sentinel value used by the popover selects to encode the "follow global
// default" choice. When set, the corresponding selected ref is null.
export const DEFAULT_SENTINEL = '__default__'

const SESSION_SETTING_FIELDS = ['permission_mode', 'selected_model', 'effort', 'thinking_enabled', 'claude_in_chrome', 'fast_mode', 'context_max']

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
    const selectedFastMode = ref(null)
    const selectedContextMax = ref(null)

    const activePermissionMode = ref(null)
    const activeModel = ref(null)
    const activeEffort = ref(null)
    const activeThinking = ref(null)
    const activeClaudeInChrome = ref(null)
    const activeFastMode = ref(null)
    const activeContextMax = ref(null)

    const SELECTED_REFS = {
        permission_mode: selectedPermissionMode,
        selected_model: selectedModel,
        effort: selectedEffort,
        thinking_enabled: selectedThinking,
        claude_in_chrome: selectedClaudeInChrome,
        fast_mode: selectedFastMode,
        context_max: selectedContextMax,
    }
    const ACTIVE_REFS = {
        permission_mode: activePermissionMode,
        selected_model: activeModel,
        effort: activeEffort,
        thinking_enabled: activeThinking,
        claude_in_chrome: activeClaudeInChrome,
        fast_mode: activeFastMode,
        context_max: activeContextMax,
    }

    // ─── Resolved defaults (project chain → global) ──────────────────────────
    // The baseline a NULL ("follow default") field resolves to: the inherited
    // per-project default (walking worktree_of / path ancestors, mirroring the
    // backend twicc.project_hierarchy) when the project sets one, else the
    // provider's global default. This is what the popover shows as "default",
    // what diff-marking compares against, and what "reset to default" lands on.
    // The session still stores NULL and the backend re-resolves the same chain
    // at create/resume (option B), so this stays display-only.
    // The provider's GLOBAL defaults (synced settings), with no project layer.
    // The bottom of every resolution: resolvedDefaults falls through to it, and
    // the reset stack's "Global defaults" target pins to it.
    const globalDefaults = computed(() => {
        const pStore = providerStore.value
        return {
            selected_model: pStore?.defaultModel,
            permission_mode: pStore?.defaultPermissionMode,
            effort: pStore?.defaultEffort,
            thinking_enabled: pStore?.defaultThinking,
            claude_in_chrome: pStore?.defaultClaudeInChrome,
            fast_mode: pStore?.defaultFastMode,
            context_max: pStore?.defaultContextMax,
        }
    })

    const resolvedDefaults = computed(() => {
        const g = globalDefaults.value
        const provider = session.value?.provider
        const projectId = session.value?.project_id
        const chain = (projectId && provider)
            ? resolveProjectAgentDefaults(projectId, provider, store.projects)
            : {}
        return {
            selected_model: chain.selected_model ?? g.selected_model,
            permission_mode: chain.permission_mode ?? g.permission_mode,
            effort: chain.effort ?? g.effort,
            thinking_enabled: chain.thinking_enabled ?? g.thinking_enabled,
            claude_in_chrome: chain.claude_in_chrome ?? g.claude_in_chrome,
            fast_mode: chain.fast_mode ?? g.fast_mode,
            context_max: chain.context_max ?? g.context_max,
        }
    })

    // The model that's effectively in use right now: the user's selection if
    // any, otherwise the resolved (project → global) default. Used to feed
    // ``context.effectiveModel`` into rendering hooks (capability gates,
    // help text, etc.).
    const effectiveModel = computed(() => selectedModel.value ?? resolvedDefaults.value.selected_model)

    // Whether the provider's auto-promote rule would kick in for the user's
    // current selection in the popover. The rule itself is provider-specific
    // (Claude Code: 200K + model supports 1M + usage > 85% of 200K → 1M);
    // we delegate to the provider helper, evaluated against the SELECTED
    // value (or the global default if none selected) — NOT against the
    // persisted ``session.context_max``, which can diverge from the user's
    // current pick and would otherwise trigger false positives on drafts.
    const isContextMaxForced = computed(() => {
        if (!session.value || !providerHelpers.value) return false
        const baseValue = selectedContextMax.value ?? resolvedDefaults.value.context_max
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
        selectedFastMode.value !== null ||
        selectedContextMax.value !== null
    )

    const hasDropdownsChanged = computed(() =>
        selectedModel.value !== activeModel.value ||
        selectedPermissionMode.value !== activePermissionMode.value ||
        selectedEffort.value !== activeEffort.value ||
        selectedThinking.value !== activeThinking.value ||
        selectedClaudeInChrome.value !== activeClaudeInChrome.value ||
        selectedFastMode.value !== activeFastMode.value ||
        selectedContextMax.value !== activeContextMax.value
    )

    const hasSettingsChanged = computed(() => hasDropdownsChanged.value)

    // The shape consumed by ``providerHelpers.getSummaryParts``. Held here
    // (not on the helpers) because it depends on this session's selections —
    // helpers operate over the rendered values, not the live refs.
    const summaryState = computed(() => {
        return {
            selected: {
                selected_model: selectedModel.value,
                permission_mode: selectedPermissionMode.value,
                effort: selectedEffort.value,
                thinking_enabled: selectedThinking.value,
                claude_in_chrome: selectedClaudeInChrome.value,
                fast_mode: selectedFastMode.value,
                context_max: selectedContextMax.value,
            },
            // Diff-marking compares against the RESOLVED default (project chain →
            // global), so a field equal to its inherited project default reads as
            // "default" rather than as an override.
            defaults: resolvedDefaults.value,
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
        if (typeof value === 'string' && value.startsWith('__reset__')) {
            applyResetTarget(resetStack.value.find(t => t.key === value))
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
        selectedFastMode.value = preset.fast_mode
    }

    function resetAllToDefaults() {
        selectedPermissionMode.value = null
        selectedModel.value = null
        selectedEffort.value = null
        selectedThinking.value = null
        selectedClaudeInChrome.value = null
        selectedFastMode.value = null
        selectedContextMax.value = null
    }

    // Resolve a full concrete bundle starting at a chain slice (nearest node
    // first), filling unset fields from the global defaults. Powers the reset
    // stack: "reset to <ancestor>" pins the draft to that ancestor's resolved
    // defaults, skipping the overrides of nearer projects.
    function resolveFromChainSlice(slice, provider, global) {
        const resolved = {}
        for (const node of slice) {
            const bundle = node.default_agent_settings?.[provider] || {}
            for (const field in bundle) {
                if (bundle[field] != null && !(field in resolved)) resolved[field] = bundle[field]
            }
        }
        return { ...global, ...resolved }
    }

    // The stack of reset targets surfaced under "Reset" in the popover:
    //   • "Project defaults"  — clear every override → follow the project's
    //     resolved defaults (NULL, so the fields render as "default").
    //   • one entry per ANCESTOR project that defines its own defaults — pins the
    //     draft to that ancestor's resolved defaults (concrete; skips the
    //     overrides of nearer projects).
    //   • "Global defaults"   — pins to the provider's global defaults (concrete).
    // When no project in the chain defines any defaults, collapses to a single
    // plain "Reset to defaults" (the follow target) — the pre-feature UX.
    const resetStack = computed(() => {
        const provider = session.value?.provider
        const projectId = session.value?.project_id
        const g = globalDefaults.value
        const chain = (provider && projectId) ? ancestorChain(projectId, store.projects) : []
        const ancestorSources = []
        for (let i = 1; i < chain.length; i++) {
            const node = chain[i]
            const bundle = node.default_agent_settings?.[provider]
            if (bundle && Object.keys(bundle).length) {
                ancestorSources.push({
                    key: `__reset__node:${node.id}`,
                    label: node.name || node.directory || node.id,
                    bundle: resolveFromChainSlice(chain.slice(i), provider, g),
                })
            }
        }
        const selfBundle = chain[0]?.default_agent_settings?.[provider]
        const hasProjectDefaults = (selfBundle && Object.keys(selfBundle).length) || ancestorSources.length > 0
        if (!hasProjectDefaults) {
            return [{ key: '__reset__follow', label: 'Reset to defaults', follow: true }]
        }
        return [
            { key: '__reset__follow', label: 'Project defaults', follow: true },
            ...ancestorSources,
            { key: '__reset__global', label: 'Global defaults', bundle: { ...g } },
        ]
    })

    // Apply a reset target: a "follow" target clears every override (NULL); a
    // concrete target forces the bundle's values so they override the project
    // resolution (that's why ancestor/global targets are concrete, not NULL).
    function applyResetTarget(target) {
        if (!target) return
        if (target.follow) {
            resetAllToDefaults()
            return
        }
        const b = target.bundle || {}
        selectedModel.value = b.selected_model ?? null
        selectedContextMax.value = b.context_max ?? null
        selectedEffort.value = b.effort ?? null
        selectedThinking.value = b.thinking_enabled ?? null
        selectedPermissionMode.value = b.permission_mode ?? null
        selectedClaudeInChrome.value = b.claude_in_chrome ?? null
        selectedFastMode.value = b.fast_mode ?? null
    }

    function restoreSettings() {
        selectedModel.value = activeModel.value
        selectedPermissionMode.value = activePermissionMode.value
        selectedEffort.value = activeEffort.value
        selectedThinking.value = activeThinking.value
        selectedClaudeInChrome.value = activeClaudeInChrome.value
        selectedFastMode.value = activeFastMode.value
        selectedContextMax.value = activeContextMax.value
    }

    // Resolve null → resolved default (project chain → global) for a settings
    // dict, so ``classify`` compares concrete values instead of "raw null vs
    // explicit".
    function resolveSettingsDefaults(settings) {
        const d = resolvedDefaults.value
        return {
            permission_mode: settings.permission_mode ?? d.permission_mode,
            selected_model: settings.selected_model ?? d.selected_model,
            effort: settings.effort ?? d.effort,
            thinking_enabled: settings.thinking_enabled ?? d.thinking_enabled,
            claude_in_chrome: settings.claude_in_chrome ?? d.claude_in_chrome,
            fast_mode: settings.fast_mode ?? d.fast_mode,
            context_max: settings.context_max ?? d.context_max,
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
            fast_mode: activeFastMode.value,
            context_max: activeContextMax.value,
        })
        const requested = resolveSettingsDefaults({
            permission_mode: selectedPermissionMode.value,
            selected_model: selectedModel.value,
            effort: selectedEffort.value,
            thinking_enabled: selectedThinking.value,
            claude_in_chrome: selectedClaudeInChrome.value,
            fast_mode: selectedFastMode.value,
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
        selectedFastMode.value = sess?.fast_mode ?? null
        selectedContextMax.value = sess?.context_max ?? null
        activePermissionMode.value = selectedPermissionMode.value
        activeModel.value = selectedModel.value
        activeEffort.value = selectedEffort.value
        activeThinking.value = selectedThinking.value
        activeClaudeInChrome.value = selectedClaudeInChrome.value
        activeFastMode.value = selectedFastMode.value
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

    // Keep the (selectedModel, contextMax, effort, fastMode, permissionMode)
    // tuple consistent against the provider's rules. The helper enforces
    // retired-model upgrade, then contextMax / effort demotion, fast-mode
    // clearing and permissionMode demotion against the model's capabilities.
    // Fires immediately so a session loading with stale settings gets
    // corrected on mount.
    watch(
        () => ({
            selectedModel: selectedModel.value ?? resolvedDefaults.value.selected_model,
            contextMax: selectedContextMax.value ?? resolvedDefaults.value.context_max,
            effort: selectedEffort.value ?? resolvedDefaults.value.effort,
            fastMode: selectedFastMode.value ?? resolvedDefaults.value.fast_mode,
            permissionMode: selectedPermissionMode.value ?? resolvedDefaults.value.permission_mode,
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
            if (adjusted.fastMode !== current.fastMode) {
                selectedFastMode.value = adjusted.fastMode
                activeFastMode.value = adjusted.fastMode
            }
            if (adjusted.permissionMode !== current.permissionMode) {
                selectedPermissionMode.value = adjusted.permissionMode
                activePermissionMode.value = adjusted.permissionMode
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
        selectedFastMode,
        selectedContextMax,
        activePermissionMode,
        activeModel,
        activeEffort,
        activeThinking,
        activeClaudeInChrome,
        activeFastMode,
        activeContextMax,
        SELECTED_REFS,
        ACTIVE_REFS,
        // derived
        effectiveModel,
        resolvedDefaults,
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
        resetStack,
        restoreSettings,
        resolveSettingsDefaults,
    }
}
