// frontend/src/providers/codex/store.js

import { defineStore, acceptHMRUpdate } from 'pinia'
import { ref } from 'vue'
import { CONTEXT_MAX, EFFORT, PERMISSION_MODE, UNTRUSTED_PERMISSION_MODES } from './constants'

export const useCodexStore = defineStore('codex', () => {
    // ─── Auth ────────────────────────────────────────────────────────────

    // Codex CLI authentication state (from codex:auth_updated messages).
    // null = unknown (no message received yet), true/false = known state.
    // Driven by the backend's auth_task (periodic check) and on-connect push.
    const authenticated = ref(null)

    function setAuthenticated(value) {
        authenticated.value = value
    }

    // ─── OpenAI statuspage ───────────────────────────────────────────────

    // OpenAI statuspage component status (from codex:openai_status messages).
    // Defaults to 'operational' so the UI doesn't flash a warning before the
    // first push arrives.
    const openaiStatus = ref('operational')

    function setOpenaiStatus(value) {
        openaiStatus.value = value
    }

    // ─── Usage quota ─────────────────────────────────────────────────────

    // Per-provider usage data. ``null`` until the bootstrap seed or the
    // first ``usage_updated`` push arrives, then ``{ success, reason, raw,
    // computed }``. Consumers should treat ``null`` as "not yet loaded".
    const usage = ref(null)

    // True while a manual usage refresh requested from the sidebar is in
    // flight. Set by the "Refresh now" button (codex:check_usage), cleared
    // when the matching usage_updated (reason === 'manual') returns below,
    // or by a safety timeout in the caller.
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

    // ─── Usage read/dump file settings (synced) ──────────────────────────
    //
    // ``read`` sources the usage payload from a local JSON file
    // (validated by the cross-provider ``validate_usage_file`` WS
    // handler), ``dump`` writes the live-fetched quota to the configured
    // path. The two modes are mutually exclusive.

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

    // ─── Daily quota warm-up ─────────────────────────────────────────────
    //
    // Local wall-clock time ("HH:MM", empty = disabled) at which the backend
    // fires a tiny throwaway request to open the subscription's 5-hour quota
    // window early in the day (see twicc.quota_wakeup_task). Synced across
    // devices like the defaults below.

    const quotaWakeupTime = ref(null)

    function setQuotaWakeupTime(value) {
        if (typeof value === 'string') quotaWakeupTime.value = value
    }

    // ─── Per-session agent settings defaults ─────────────────────────────
    //
    // Default values applied to new Codex sessions and used as the fallback
    // for the dropdowns in the message input. Persisted to localStorage and
    // synchronized to the backend via the synced settings orchestrator.

    const defaultModel = ref(null)
    const defaultEffort = ref(null)
    const defaultPermissionMode = ref(null)
    // Default permission mode seeded when a session is created in an
    // UNTRUSTED (or unknown-trust) project. Restricted to the
    // untrusted-allowed set (trust design §13.1).
    const defaultUntrustedPermissionMode = ref(null)
    const defaultContextMax = ref(null)
    const defaultFastMode = ref(null)

    function setDefaultModel(value) {
        if (typeof value === 'string' && value.length > 0) defaultModel.value = value
    }
    function setDefaultEffort(value) {
        if (Object.values(EFFORT).includes(value)) defaultEffort.value = value
    }
    function setDefaultPermissionMode(value) {
        if (Object.values(PERMISSION_MODE).includes(value)) defaultPermissionMode.value = value
    }
    function setDefaultUntrustedPermissionMode(value) {
        if (UNTRUSTED_PERMISSION_MODES.includes(value)) defaultUntrustedPermissionMode.value = value
    }
    function setDefaultContextMax(value) {
        if (Object.values(CONTEXT_MAX).includes(value)) defaultContextMax.value = value
    }
    function setDefaultFastMode(value) {
        if (typeof value === 'boolean') defaultFastMode.value = value
    }

    // ─── Agent settings categories (live/idle/startup) ───────────────────
    //
    // Seeded from the bootstrap payload ``providers.codex.agent_settings_categories``.

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
    // Seeded from the bootstrap payload ``providers.codex.model_registry``.

    const modelRegistry = ref([])

    function setModelRegistry(registry) {
        modelRegistry.value = Array.isArray(registry) ? registry : []
    }

    return {
        authenticated,
        setAuthenticated,
        openaiStatus,
        setOpenaiStatus,
        usage,
        setUsage,
        usageRefreshing,
        setUsageRefreshing,
        usageReadFileEnabled,
        usageReadFilePath,
        usageDumpFileEnabled,
        usageDumpFilePath,
        setUsageReadFileEnabled,
        setUsageReadFilePath,
        setUsageDumpFileEnabled,
        setUsageDumpFilePath,
        quotaWakeupTime,
        setQuotaWakeupTime,
        defaultModel,
        defaultEffort,
        defaultPermissionMode,
        defaultUntrustedPermissionMode,
        defaultContextMax,
        defaultFastMode,
        setDefaultModel,
        setDefaultEffort,
        setDefaultPermissionMode,
        setDefaultUntrustedPermissionMode,
        setDefaultContextMax,
        setDefaultFastMode,
        agentSettingsCategories,
        setAgentSettingsCategories,
        modelRegistry,
        setModelRegistry,
    }
})

if (import.meta.hot) {
    import.meta.hot.accept(acceptHMRUpdate(useCodexStore, import.meta.hot))
}
