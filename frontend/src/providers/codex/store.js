// frontend/src/providers/codex/store.js

import { defineStore, acceptHMRUpdate } from 'pinia'
import { ref } from 'vue'
import { CONTEXT_MAX, EFFORT, PERMISSION_MODE } from './constants'

export const useCodexStore = defineStore('codex', () => {
    // ─── Auth ────────────────────────────────────────────────────────────

    // Codex CLI authentication state (from codex:auth_updated messages).
    // null = unknown (no message received yet), true/false = known state.
    // Driven by the backend's auth_task (periodic check) and on-connect push.
    const authenticated = ref(null)

    function setAuthenticated(value) {
        authenticated.value = value
    }

    // ─── Per-session agent settings defaults ─────────────────────────────
    //
    // Default values applied to new Codex sessions and used as the fallback
    // for the dropdowns in the message input. Persisted to localStorage and
    // synchronized to the backend via the synced settings orchestrator.

    const defaultModel = ref(null)
    const defaultEffort = ref(null)
    const defaultPermissionMode = ref(null)
    const defaultContextMax = ref(null)

    function setDefaultModel(value) {
        if (typeof value === 'string' && value.length > 0) defaultModel.value = value
    }
    function setDefaultEffort(value) {
        if (Object.values(EFFORT).includes(value)) defaultEffort.value = value
    }
    function setDefaultPermissionMode(value) {
        if (Object.values(PERMISSION_MODE).includes(value)) defaultPermissionMode.value = value
    }
    function setDefaultContextMax(value) {
        if (Object.values(CONTEXT_MAX).includes(value)) defaultContextMax.value = value
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
        defaultModel,
        defaultEffort,
        defaultPermissionMode,
        defaultContextMax,
        setDefaultModel,
        setDefaultEffort,
        setDefaultPermissionMode,
        setDefaultContextMax,
        agentSettingsCategories,
        setAgentSettingsCategories,
        modelRegistry,
        setModelRegistry,
    }
})

if (import.meta.hot) {
    import.meta.hot.accept(acceptHMRUpdate(useCodexStore, import.meta.hot))
}
