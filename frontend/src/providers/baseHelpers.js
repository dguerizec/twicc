/**
 * Base class for per-provider frontend helpers.
 *
 * Mirrors the backend `BaseProviderHelpers` pattern: each provider ships a
 * subclass that overrides only the behaviours that differ from the neutral
 * defaults defined here. Generic frontend code resolves the right instance
 * via the registry in `providers/index.js` and never branches on provider
 * identity itself.
 */
export class BaseProviderHelpers {
    static provider = null

    /**
     * Whether the current frontend state allows sending a message to a
     * session of this provider. Default: always allowed. Providers override
     * to gate on auth, quota, or any other prerequisite.
     */
    canSendMessage() {
        return true
    }

    /**
     * Built-in slash commands provided by the provider's runtime/CLI.
     * Returned items are merged with the user-defined commands fetched
     * from the backend by the slash command picker. Default: no built-ins.
     */
    getBuiltInSlashCommands() {
        return []
    }

    // ─── Synced settings ownership ───────────────────────────────────────
    //
    // The settings store hosts a single localStorage blob and a single
    // outgoing payload to the backend, but each provider declares which
    // keys it owns. The orchestrator dispatches incoming/outgoing values
    // through ``applySyncedSettings`` / ``getSyncedSettings`` so the actual
    // state lives next to the rest of the provider's state.

    /**
     * Synced setting keys this provider owns. The orchestrator filters
     * incoming localStorage / WS payloads down to this set before
     * delegating. Default: provider owns no synced setting.
     */
    getSyncedSettingsKeys() {
        return []
    }

    /**
     * Apply a subset of synced settings (matching ``getSyncedSettingsKeys``)
     * to this provider's state. The dict may contain extra keys that are
     * not owned by this provider — they should be ignored. Default: no-op.
     */
    applySyncedSettings(/* settings */) {}

    /**
     * Read this provider's current synced settings as a flat dict, ready
     * to be merged into the localStorage blob and the outgoing payload.
     * Default: no settings exposed.
     */
    getSyncedSettings() {
        return {}
    }

    // ─── Per-session agent settings classification ───────────────────────
    //
    // Each provider classifies its per-session agent settings (model,
    // effort, etc.) into ``live`` / ``idle`` / ``startup`` buckets so the
    // agent manager knows when a change can be applied to a running
    // process. Mirrors the backend ``BaseProviderHelpers.AGENT_SETTINGS_CATEGORIES``.

    /**
     * Classification of this provider's per-session agent setting fields.
     * Shape: ``{ live: [...], idle: [...], startup: [...] }``. Default:
     * no classified fields.
     */
    getAgentSettingsCategories() {
        return { live: [], idle: [], startup: [] }
    }

    /**
     * Classify the diff between two settings dicts, grouping each changed
     * key under its category. Returns ``{ live, idle, startup }``.
     */
    classifyAgentSettingsChanges(current, requested) {
        const result = { live: [], idle: [], startup: [] }
        for (const [category, fields] of Object.entries(this.getAgentSettingsCategories())) {
            for (const field of fields) {
                if (current[field] !== requested[field]) result[category].push(field)
            }
        }
        return result
    }

    // ─── Agent settings consistency ──────────────────────────────────────
    //
    // Single point of truth for the rules that keep an agent settings bundle
    // self-consistent: the model can rule out certain ``contextMax`` /
    // ``effort`` values, retired models get auto-upgraded, etc. Mirrors the
    // backend ``BaseProviderHelpers.enforce_agent_settings_consistency``.
    // Generic call sites pass an effective settings dict (defaults already
    // applied) and apply the diff between input and output.

    /**
     * Return ``settings`` normalised against this provider's rules.
     *
     * The argument is a plain object using the frontend's camelCase field
     * names: ``selectedModel``, ``contextMax``, ``effort``, ``thinkingEnabled``,
     * ``claudeInChrome``, ``permissionMode`` (any subset). Returns a new
     * object with the same shape — call sites compare to detect what changed.
     *
     * Default: no-op (returns the same object). Providers override to add
     * model-driven rules.
     */
    enforceAgentSettingsConsistency(settings) {
        return settings
    }

    // ─── Per-field choice catalogue ──────────────────────────────────────
    //
    // Each provider declares the valid values + human labels for the agent
    // setting fields it owns (everything except ``selected_model``, which is
    // served separately via the model registry). Choice entries follow the
    // shape ``{ value, label, display_label?, description? }``. Generic call
    // sites pull lists from ``getFieldChoices(field)`` and lookups from
    // ``getChoiceLabel`` / ``getChoiceDisplayLabel``.

    /**
     * Map from agent setting field name (snake_case wire name) to its list
     * of choice entries. Default: empty (provider declares no choices).
     */
    getAgentSettingsChoices() {
        return {}
    }

    /**
     * Return the list of choice entries for ``field``, or ``[]`` when the
     * provider doesn't declare any. Order is preserved (used to drive the
     * select option order).
     */
    getFieldChoices(field) {
        return this.getAgentSettingsChoices()[field] ?? []
    }

    /**
     * Return the ``label`` of the choice for ``field`` whose ``value``
     * matches ``value`` strictly (``===``). Returns ``null`` when nothing
     * matches — call sites can fall back to a stringified value if they
     * want to surface the raw value.
     */
    getChoiceLabel(field, value) {
        const choice = this.getFieldChoices(field).find(c => c.value === value)
        return choice?.label ?? null
    }

    /**
     * Like ``getChoiceLabel`` but prefers ``display_label`` when present —
     * used by compact summary strips where ``label`` would be too long.
     */
    getChoiceDisplayLabel(field, value) {
        const choice = this.getFieldChoices(field).find(c => c.value === value)
        return choice?.display_label ?? choice?.label ?? null
    }

    /**
     * Whether this provider supports the agent setting ``field``. Derived
     * from ``getAgentSettingsCategories``: a field listed in any category
     * (live/idle/startup) is supported. UI components branch on this to
     * hide selects/commands the provider doesn't own.
     */
    supportsAgentSetting(field) {
        const categories = this.getAgentSettingsCategories()
        for (const fields of Object.values(categories)) {
            if (fields.includes(field)) return true
        }
        return false
    }

    // ─── Runtime context-max resolution ──────────────────────────────────
    //
    // Unlike ``enforceAgentSettingsConsistency`` (which is rules-only over a
    // settings dict), this hook can read live session state — typically
    // ``context_usage`` — so a provider can promote the effective window
    // when the persisted value would be too small. ``overrideModel`` lets
    // callers preview the value for a model that hasn't been saved yet.
    //
    // Default: return the session's persisted ``context_max`` as-is.

    getEffectiveContextMax(session, /* overrideModel */ _overrideModel) {
        return session?.context_max ?? null
    }
}
