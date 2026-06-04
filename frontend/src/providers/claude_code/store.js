// frontend/src/providers/claude_code/store.js

import { defineStore, acceptHMRUpdate } from 'pinia'
import { ref } from 'vue'
import { CONTEXT_MAX, EFFORT, PERMISSION_MODE } from './constants'

export const useClaudeCodeStore = defineStore('claudeCode', () => {
    // ─── Auth ────────────────────────────────────────────────────────────

    // Claude CLI authentication state (from claude_code:auth_updated messages).
    // null = unknown (no message received yet), true/false = known state.
    // Driven by the backend's auth_task (periodic check) and on-connect push.
    const authenticated = ref(null)

    function setAuthenticated(value) {
        authenticated.value = value
    }

    // ─── Anthropic statuspage ────────────────────────────────────────────

    // Anthropic statuspage component status (from claude_code:anthropic_status
    // messages). Defaults to 'operational' so the UI doesn't flash a warning
    // before the first push arrives.
    const anthropicStatus = ref('operational')

    function setAnthropicStatus(value) {
        anthropicStatus.value = value
    }

    // ─── Usage quota ─────────────────────────────────────────────────────

    // Per-provider usage data. ``null`` until the bootstrap seed or the
    // first ``usage_updated`` push arrives, then ``{ success, reason, raw,
    // computed }``. Consumers should treat ``null`` as "not yet loaded".
    const usage = ref(null)

    // True while a manual usage refresh requested from the sidebar is in
    // flight. Set by the "Refresh now" button (claude_code:check_usage),
    // cleared when the matching usage_updated (reason === 'manual') returns
    // below, or by a safety timeout in the caller.
    const usageRefreshing = ref(false)

    function setUsage(success, reason, raw, computed) {
        usage.value = { success, reason, raw, computed }
        // The user-initiated refresh round-trip just came back — stop the
        // "Refresh now" spinner, whether it succeeded or failed.
        if (reason === 'manual') usageRefreshing.value = false
    }

    function setUsageRefreshing(value) {
        usageRefreshing.value = !!value
    }

    // ─── Per-session agent settings defaults ─────────────────────────────
    //
    // Default values applied to new Claude Code sessions and used as the
    // fallback for the dropdowns in the message input. Persisted to
    // localStorage and synchronized to the backend via the synced settings
    // orchestrator (the keys are declared by ``ClaudeCodeHelpers.getSyncedSettingsKeys``
    // so the orchestrator dispatches incoming/outgoing values here).

    const defaultPermissionMode = ref(null)
    const defaultModel = ref(null)
    const defaultContextMax = ref(null)
    const defaultEffort = ref(null)
    const defaultThinking = ref(null)
    const defaultClaudeInChrome = ref(null)
    const defaultFastMode = ref(null)

    function setDefaultPermissionMode(value) {
        if (Object.values(PERMISSION_MODE).includes(value)) defaultPermissionMode.value = value
    }
    function setDefaultModel(value) {
        if (typeof value === 'string' && value.length > 0) defaultModel.value = value
    }
    function setDefaultContextMax(value) {
        if (Object.values(CONTEXT_MAX).includes(value)) defaultContextMax.value = value
    }
    function setDefaultEffort(value) {
        if (Object.values(EFFORT).includes(value)) defaultEffort.value = value
    }
    function setDefaultThinking(value) {
        if (typeof value === 'boolean') defaultThinking.value = value
    }
    function setDefaultClaudeInChrome(value) {
        if (typeof value === 'boolean') defaultClaudeInChrome.value = value
    }
    function setDefaultFastMode(value) {
        if (typeof value === 'boolean') defaultFastMode.value = value
    }

    // ─── Usage file source (read / dump) ────────────────────────────────
    //
    // Two mutually-exclusive modes for sourcing the Anthropic OAuth quota
    // data: ``read`` consumes a JSON file the user maintains externally
    // (validated by ``providers/claude_code/ws.js#sendValidateUsageFile``),
    // ``dump`` writes the live-fetched quota to the configured path. The
    // backend reconciles the active mode and either reads or writes; the
    // synced settings keep the UI in sync across clients via the helpers.

    const usageReadFileEnabled = ref(null)
    const usageReadFilePath = ref(null)
    const usageDumpFileEnabled = ref(null)
    const usageDumpFilePath = ref(null)

    function setUsageReadFileEnabled(value) {
        if (typeof value !== 'boolean') return
        usageReadFileEnabled.value = value
        // Mutually exclusive with dump mode.
        if (value) usageDumpFileEnabled.value = false
    }
    function setUsageReadFilePath(value) {
        if (typeof value === 'string') usageReadFilePath.value = value
    }
    function setUsageDumpFileEnabled(value) {
        if (typeof value !== 'boolean') return
        usageDumpFileEnabled.value = value
        // Mutually exclusive with read mode.
        if (value) usageReadFileEnabled.value = false
    }
    function setUsageDumpFilePath(value) {
        if (typeof value === 'string') usageDumpFilePath.value = value
    }

    // ─── Agent settings categories (live/idle/startup) ───────────────────
    //
    // Classification of the per-session agent setting fields by lifecycle
    // applicability, mirroring the backend ``BaseProviderHelpers.AGENT_SETTINGS_CATEGORIES``.
    // Seeded from the bootstrap payload ``providers.claude_code.agent_settings_categories``
    // and consumed via ``ClaudeCodeHelpers.classifyAgentSettingsChanges``.

    const agentSettingsCategories = ref({ live: [], idle: [], startup: [] })

    function setAgentSettingsCategories(value) {
        if (!value || typeof value !== 'object') return
        agentSettingsCategories.value = {
            live: Array.isArray(value.live) ? value.live : [],
            idle: Array.isArray(value.idle) ? value.idle : [],
            startup: Array.isArray(value.startup) ? value.startup : [],
        }
    }

    // ─── Model registry ──────────────────────────────────────────────────
    //
    // List of models exposed by the Claude Code provider, seeded from the
    // bootstrap payload ``providers.claude_code.model_registry``. Each entry
    // carries selection metadata (``selected_model``, ``model``, ``version``,
    // ``latest``, ``retirement_date``) and capability flags under
    // ``provider_extra`` (``supports_1m``, ``supports_effort_xhigh``,
    // ``supports_effort_max``). Consumed via ``ClaudeCodeHelpers``' capability
    // and retired-model methods.

    const modelRegistry = ref([])

    function setModelRegistry(registry) {
        modelRegistry.value = Array.isArray(registry) ? registry : []
    }

    return {
        authenticated,
        setAuthenticated,
        anthropicStatus,
        setAnthropicStatus,
        usage,
        setUsage,
        usageRefreshing,
        setUsageRefreshing,
        defaultPermissionMode,
        defaultModel,
        defaultContextMax,
        defaultEffort,
        defaultThinking,
        defaultClaudeInChrome,
        defaultFastMode,
        setDefaultPermissionMode,
        setDefaultModel,
        setDefaultContextMax,
        setDefaultEffort,
        setDefaultThinking,
        setDefaultClaudeInChrome,
        setDefaultFastMode,
        usageReadFileEnabled,
        usageReadFilePath,
        usageDumpFileEnabled,
        usageDumpFilePath,
        setUsageReadFileEnabled,
        setUsageReadFilePath,
        setUsageDumpFileEnabled,
        setUsageDumpFilePath,
        agentSettingsCategories,
        setAgentSettingsCategories,
        modelRegistry,
        setModelRegistry,
    }
})

if (import.meta.hot) {
    import.meta.hot.accept(acceptHMRUpdate(useClaudeCodeStore, import.meta.hot))
}
