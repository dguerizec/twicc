import { ref, computed, watch, toValue } from 'vue'
import { useDataStore } from '../stores/data'
import { useAgentSettingsPresetsStore } from '../stores/agentSettingsPresets'
import { getProviderHelpers, getProviderStore } from '../providers'
import { resolveProjectAgentDefaults, ancestorChain } from '../utils/projectAgentDefaults'
import { resolveProjectTrust } from '../utils/trust'

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

    // Effective trust of the session's project. NOT trusted (untrusted or
    // unknown) flips every permission-mode default source to the
    // `permission_mode_if_untrusted` layer and clamps the offered choices
    // (trust design §13.3). The backend independently enforces the same floor.
    const sessionIsUntrusted = computed(() => {
        const projectId = session.value?.project_id
        if (!projectId) return false
        return resolveProjectTrust(projectId, store.projects).state !== true
    })

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
    // frontend/src/utils/projectAgentDefaults.js) when the project sets one, else
    // the provider's global default. This is what the popover renders as the
    // "Default: X" option, what diff-marking compares against, and what every
    // reset lands on. In the snapshot model (option A) a NEW session is created
    // with these values FROZEN as concrete columns, so the baseline is purely a
    // display/diff reference — changing a default never moves a launched session.
    // See docs/plans/2026-06-09-project-agent-defaults-design.md.
    // The provider's GLOBAL defaults (synced settings), with no project layer.
    // The bottom of every resolution: resolvedDefaults falls through to it, and
    // the reset stack's "Global defaults" target pins to it.
    const globalDefaults = computed(() => {
        const pStore = providerStore.value
        return {
            selected_model: pStore?.defaultModel,
            permission_mode: pStore?.defaultPermissionMode,
            // Default-shaping pseudo-field (never a Session column): the
            // permission default used when the project is untrusted.
            permission_mode_if_untrusted: pStore?.defaultUntrustedPermissionMode,
            effort: pStore?.defaultEffort,
            thinking_enabled: pStore?.defaultThinking,
            claude_in_chrome: pStore?.defaultClaudeInChrome,
            fast_mode: pStore?.defaultFastMode,
            context_max: pStore?.defaultContextMax,
        }
    })

    // Collapse the trust-dependent permission layer of a resolved bundle: in an
    // untrusted project the effective permission default is the
    // `permission_mode_if_untrusted` value (falling back to the trusted one
    // only when nothing defines the untrusted layer — the backend clamp then
    // catches it). The pseudo-field never leaks out of the returned bundle.
    function collapseTrustPermission(bundle, untrusted) {
        const { permission_mode_if_untrusted: untrustedMode, ...rest } = bundle
        if (untrusted) rest.permission_mode = untrustedMode ?? rest.permission_mode
        return rest
    }

    const resolvedDefaults = computed(() => {
        const g = globalDefaults.value
        const provider = session.value?.provider
        const projectId = session.value?.project_id
        const chain = (projectId && provider)
            ? resolveProjectAgentDefaults(projectId, provider, store.projects)
            : {}
        return collapseTrustPermission({
            selected_model: chain.selected_model ?? g.selected_model,
            permission_mode: chain.permission_mode ?? g.permission_mode,
            permission_mode_if_untrusted:
                chain.permission_mode_if_untrusted ?? g.permission_mode_if_untrusted,
            effort: chain.effort ?? g.effort,
            thinking_enabled: chain.thinking_enabled ?? g.thinking_enabled,
            claude_in_chrome: chain.claude_in_chrome ?? g.claude_in_chrome,
            fast_mode: chain.fast_mode ?? g.fast_mode,
            context_max: chain.context_max ?? g.context_max,
        }, sessionIsUntrusted.value)
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

    // Trust clamp surfacing (trust design §13.4, same machinery as
    // isContextMaxForced): when the session's stored permission mode falls
    // outside the untrusted-allowed set (e.g. the project became untrusted
    // after creation), the backend silently runs it on the clamped value —
    // the popover must show THAT value, not the inert stored one.
    const isPermissionModeForced = computed(() => {
        if (!sessionIsUntrusted.value) return false
        const allowed = providerHelpers.value?.getUntrustedPermissionModes() ?? []
        if (!allowed.length) return false
        const value = selectedPermissionMode.value ?? resolvedDefaults.value.permission_mode
        return value != null && !allowed.includes(value)
    })

    // The value the backend actually applies when forced: the global untrusted
    // default (mirrors clamp_permission_mode_for_untrusted). Null when not
    // forced (or before the synced settings bootstrap).
    const clampedPermissionMode = computed(() => {
        if (!isPermissionModeForced.value) return null
        return providerStore.value?.defaultUntrustedPermissionMode ?? null
    })

    // ─── Aggregate state ─────────────────────────────────────────────────────
    // A field is "forced" when it diverges from its resolved default (project
    // chain → global). In the snapshot model every field is concrete, so this is
    // NOT "non-null" but "differs from the current resolved default" — matching
    // the summary's dashed-underline marking and gating the "Reset to defaults"
    // entry (disabled when nothing diverges).
    const anySettingForced = computed(() => {
        const d = resolvedDefaults.value
        const forced = (sel, def) => sel !== null && sel !== undefined && sel !== def
        return forced(selectedPermissionMode.value, d.permission_mode) ||
            forced(selectedModel.value, d.selected_model) ||
            forced(selectedEffort.value, d.effort) ||
            forced(selectedThinking.value, d.thinking_enabled) ||
            forced(selectedClaudeInChrome.value, d.claude_in_chrome) ||
            forced(selectedFastMode.value, d.fast_mode) ||
            forced(selectedContextMax.value, d.context_max)
    })

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
                // When the trust clamp overrides the stored mode, the strip
                // shows the value the agent actually runs with.
                permission_mode: clampedPermissionMode.value ?? selectedPermissionMode.value,
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
        // Trust-dependent field selection (trust design §13.3): an untrusted
        // project applies the preset's untrusted permission layer instead of
        // the trusted one (falling back to the global untrusted default).
        selectedPermissionMode.value = sessionIsUntrusted.value
            ? (preset.permission_mode_if_untrusted
                ?? globalDefaults.value.permission_mode_if_untrusted
                ?? preset.permission_mode)
            : preset.permission_mode
        selectedClaudeInChrome.value = preset.claude_in_chrome
        selectedFastMode.value = preset.fast_mode
    }

    // Re-pin every field to the current resolved default (project chain →
    // global) as CONCRETE values. The snapshot model has no "follow" (null);
    // resetting re-applies today's resolved defaults. Also used when the draft's
    // provider switches, to re-pin to the new provider's defaults.
    function resetAllToDefaults() {
        const d = resolvedDefaults.value
        selectedPermissionMode.value = d.permission_mode
        selectedModel.value = d.selected_model
        selectedEffort.value = d.effort
        selectedThinking.value = d.thinking_enabled
        selectedClaudeInChrome.value = d.claude_in_chrome
        selectedFastMode.value = d.fast_mode
        selectedContextMax.value = d.context_max
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
        return collapseTrustPermission({ ...global, ...resolved }, sessionIsUntrusted.value)
    }

    // The stack of reset targets surfaced under "Reset" in the popover. Every
    // target re-applies a CONCRETE resolved bundle (the snapshot model has no
    // "follow"/NULL):
    //   • "Project defaults" — the project's own resolved defaults (full chain).
    //   • one entry per ANCESTOR project that defines its own defaults — that
    //     ancestor's resolved defaults (skips the overrides of nearer projects).
    //   • "Global defaults"  — the provider's global defaults.
    // When no project in the chain defines any defaults, collapses to a single
    // "Reset to defaults" that re-applies the global bundle.
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
        const globalBundle = collapseTrustPermission({ ...g }, sessionIsUntrusted.value)
        if (!hasProjectDefaults) {
            return [{ key: '__reset__global', label: 'Reset to defaults', bundle: globalBundle }]
        }
        return [
            { key: '__reset__project', label: 'Project defaults', bundle: resolveFromChainSlice(chain, provider, g) },
            ...ancestorSources,
            { key: '__reset__global', label: 'Global defaults', bundle: globalBundle },
        ]
    })

    // Apply a reset target by forcing its concrete bundle onto every field. In
    // the snapshot model there is no "follow" target — each reset re-pins the
    // session to a resolved bundle (project / ancestor / global).
    function applyResetTarget(target) {
        if (!target) return
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

    // Drafts have no "Apply" step — every popover change is live. Mirror each selection onto the draft's
    // session fields and persist them to IndexedDB so they survive a reload (real sessions instead commit
    // on Send/Apply via the WS payload, and a draft's provider/hybrid persist through their own actions).
    // NOT immediate: the baseline is the just-synced session values, so opening a draft writes nothing;
    // only a genuine change (user edit, or the consistency watcher correcting a value) persists. Writing
    // the same values back via the store is idempotent — the session→ref watchers above don't re-loop.
    watch(
        [selectedPermissionMode, selectedModel, selectedEffort, selectedThinking,
            selectedClaudeInChrome, selectedFastMode, selectedContextMax],
        () => {
            if (!session.value?.draft) return
            store.setDraftAgentSettings(sessionId.value, {
                permission_mode: selectedPermissionMode.value,
                selected_model: selectedModel.value,
                effort: selectedEffort.value,
                thinking_enabled: selectedThinking.value,
                claude_in_chrome: selectedClaudeInChrome.value,
                fast_mode: selectedFastMode.value,
                context_max: selectedContextMax.value,
            })
        },
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
        sessionIsUntrusted,
        isPermissionModeForced,
        clampedPermissionMode,
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
