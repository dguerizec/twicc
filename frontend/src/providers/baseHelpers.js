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
    static label = null

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

    // ─── Authentication ──────────────────────────────────────────────────
    //
    // Some providers gate sending on an external authentication step (e.g.
    // a CLI login). The frontend surfaces that state via a persistent toast
    // and the sidebar callout; both consume the hooks below. A provider
    // that doesn't authenticate returns ``null`` from ``getAuthState`` and
    // the surface skips it entirely.

    /**
     * Getter function that returns the current auth state for this provider:
     * ``true`` (authenticated), ``false`` (not authenticated) or ``null``
     * (still unknown — no backend push received yet). Returned as a getter
     * (not a ``ref``) so callers can hand it straight to ``watch``; Pinia
     * unwraps refs read through the store, so the getter form is the only
     * way to keep reactivity across the helpers boundary.
     * Default: ``null`` (provider doesn't gate on auth — return ``null`` for
     * the helper itself, not a getter).
     */
    getAuthState() {
        return null
    }

    /**
     * Shell command the user must run to authenticate with this provider,
     * already prefixed by the configured TwiCC launch prefix. Used by the
     * persistent auth toast and the sidebar callout. Default: ``null``.
     */
    getAuthLoginCommand() {
        return null
    }

    /**
     * Ask the backend to re-check this provider's auth state right now,
     * bypassing the periodic poll. Bound to the toast / sidebar "Check
     * again" buttons. Default: no-op.
     */
    requestAuthRecheck() {}

    // ─── Usage quota surface ─────────────────────────────────────────────
    //
    // Providers that expose a quota / usage payload (consumed by the
    // sidebar quota block, the usage graph dialog, …) opt in by returning
    // ``true`` from ``tracksUsage`` and pointing ``getUsageExternalLink``
    // at their canonical external dashboard.

    /**
     * Whether this provider exposes a usage payload via its store. The
     * sidebar's rotation only includes providers that return ``true``.
     * Default: not tracked.
     */
    tracksUsage() {
        return false
    }

    /**
     * External link (provider's own usage dashboard) surfaced in the
     * stale-data tooltip of the sidebar quota block. Returns
     * ``{ url, label }`` or ``null`` when the provider doesn't ship one.
     * Default: ``null``.
     */
    getUsageExternalLink() {
        return null
    }

    // ─── Usage read/dump file settings ───────────────────────────────────
    //
    // Providers that track usage may also support sourcing the data from
    // a JSON file (read mode) or persisting the API response to one
    // (dump mode). The Settings popover loops over every provider that
    // ``tracksUsage()`` and reads/writes those fields through the hooks
    // below — each provider proxies them onto its own store, so the on-
    // disk format and synced settings stay namespaced per provider while
    // the UI is generic.
    //
    // Field names: ``read_enabled`` / ``read_path`` / ``dump_enabled`` /
    // ``dump_path``. Read and dump are mutually exclusive: a setter that
    // switches one to ``true`` must clear the other (the provider's
    // store enforces this).

    /**
     * Read this provider's persisted value for a usage-file field.
     * Returns ``null`` when the provider doesn't support usage files.
     * Default: not supported.
     */
    getUsageFileSetting(/* field */) {
        return null
    }

    /**
     * Write this provider's persisted value for a usage-file field.
     * Type validation is the store's responsibility — invalid values
     * are silently ignored. Default: no-op.
     */
    setUsageFileSetting(/* field, value */) {}

    // ─── Service status surface (Statuspage / health) ───────────────────
    //
    // Providers whose backend service publishes a public status (e.g.
    // Anthropic's statuspage for Claude Code) opt in by returning a
    // reactive getter from ``getServiceStatus`` and a display descriptor
    // from ``getServiceStatusDisplay``. The Settings popover footer
    // rotates across providers that expose one (same pattern as the
    // sidebar usage quota rotation).

    /**
     * Getter function returning the current service status string for
     * this provider, or ``null`` (still unknown). Returned as a getter
     * (not a ``ref``) so callers can hand it straight to ``watch`` /
     * ``computed`` while keeping reactivity across the helpers boundary.
     * Default: ``null`` — provider has no service status surface.
     */
    getServiceStatus() {
        return null
    }

    /**
     * Render-side descriptor for a status value. Returns
     * ``{ label, modifier, url, tooltip, shortLabel }`` or ``null`` when
     * the provider doesn't expose a status surface or doesn't recognise
     * the value.
     *
     * - ``label``: human-readable status name (e.g. "Operational")
     * - ``modifier``: one of ``ok`` / ``warning`` / ``error`` / ``info``,
     *   used by the footer to colour the dot
     * - ``url``: external link to the public status page
     * - ``tooltip``: hover text (e.g. "Claude code status on Anthropic's side")
     * - ``shortLabel``: 2–4 char compact label used in the footer ("CC", "CX")
     *
     * Default: ``null``.
     */
    getServiceStatusDisplay(/* status */) {
        return null
    }

    // ─── Per-provider default values for agent settings ──────────────────
    //
    // Generic surfaces that need to read or write the provider-scoped
    // default for a given agent setting field (the Settings popover's
    // provider section, the static palette commands, …) go through these
    // hooks instead of poking the provider store directly. Each provider
    // overrides them with its own field → store-binding mapping.

    /**
     * Read this provider's persisted default for ``field`` (the wire-name,
     * e.g. ``selected_model``). Returns ``null`` when the provider doesn't
     * own the field. Default: no defaults exposed.
     */
    getDefaultValue(/* field */) {
        return null
    }

    /**
     * Write this provider's persisted default for ``field``. Validation
     * (type, enum membership) is the store's responsibility — invalid
     * values are silently ignored. Default: no-op.
     */
    setDefaultValue(/* field, value */) {}

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
    // The agent settings bundle is a CLOSED set of fields shared by every
    // provider: ``selected_model``, ``effort``, ``thinking_enabled``,
    // ``permission_mode``, ``context_max``, ``claude_in_chrome``. The
    // bundle — and the matching ``Session`` row, the WS payload, and the
    // localStorage synced settings — has the same shape regardless of
    // which provider owns the running session. Each provider declares
    // which fields it actually uses by listing them in
    // ``getAgentSettingsCategories``; every other field is silently
    // ignored by that provider. New provider-specific session-level flags
    // follow the same pattern: add the column to ``Session`` and classify
    // it in the owning provider's categories — never split off into a
    // per-provider side table. See the matching backend comment on
    // ``Session.claude_in_chrome`` in ``src/twicc/core/models.py``.
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

    // ─── Model label formatter ───────────────────────────────────────────
    //
    // Generic call sites (toasts, preset summaries, command palette) need a
    // human-readable label for a ``selected_model`` value. Each provider
    // overrides with its own formatting; the neutral default returns the raw
    // value (or ``''`` when unset) so a missing override still renders
    // something readable.

    getModelLabel(selectedModel) {
        return selectedModel ?? ''
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

    // ─── Agent settings popover/summary rendering hooks ──────────────────
    //
    // The popover (per-session selects) and the summary strip share a single
    // generic Vue template. Whenever a provider needs to inject behaviour
    // that doesn't fit the catalogue (auto-promote, capability-based
    // disabling, "(latest: vX)" suffixes, etc.) it overrides one of the
    // hooks below — each one has a sensible default so providers only
    // implement what's specific.
    //
    // ``context`` is a plain object the popover assembles per render. Common
    // fields callers may set: ``effectiveModel``, ``isStarting``,
    // ``isContextMaxForced``, ``selectedValue``, ``defaultValue``,
    // ``processStateName``, ``hasMessageText``, ``hasCrons``. Hooks should
    // ignore keys they don't need.

    /**
     * Human label for a setting field — used as the row's ``<label>`` in the
     * popover. Default carries sensible English names for the fields the
     * Claude provider uses; providers override individual entries when their
     * terminology differs.
     */
    getFieldLabel(field) {
        const defaults = {
            selected_model: 'Model',
            permission_mode: 'Permission',
            effort: 'Effort',
            thinking_enabled: 'Thinking',
            claude_in_chrome: 'Claude built-in Chrome MCP',
            context_max: 'Context',
        }
        return defaults[field] ?? field
    }

    /**
     * Label of the "Default: …" pseudo-option of a setting's wa-select.
     * Default: model fields use ``getModelLabel``, everything else uses
     * ``getChoiceLabel``. Providers override to e.g. append a "(latest: vX)"
     * suffix on the model.
     */
    getDefaultValueLabel(field, value) {
        if (field === 'selected_model') return this.getModelLabel(value)
        return this.getChoiceLabel(field, value) ?? String(value ?? '')
    }

    /**
     * Whether a single choice option of a select should be disabled. The
     * popover surfaces a "(not available)" suffix on disabled options so
     * the user understands why. Default: never disabled.
     */
    isChoiceDisabled(/* field, choiceValue, context */) {
        return false
    }

    /**
     * Whether the entire wa-select for ``field`` should be disabled.
     * Default: disabled while the process is starting. Providers override
     * to also disable when a runtime override is in effect (e.g. Claude's
     * auto-promote-to-1M rule grays the context_max select out).
     */
    isFieldDisabled(field, context) {
        return !!context?.isStarting
    }

    /**
     * Help text rendered under a wa-select (between select and reset link).
     * Returns a string or null. Default: null. Providers override to surface
     * runtime-driven explanations like "1M not available for this model
     * version".
     */
    getFieldHelpText(/* field, context */) {
        return null
    }

    /**
     * Value the wa-select should display, given the user's persisted
     * selection. Most fields surface the selection as-is; some providers
     * override to show a runtime override instead (e.g. Claude shows
     * ``EXTENDED`` in the context_max select while the auto-promote rule is
     * active, even if the user's persisted value is null/200K).
     *
     * Returns the same string the matching ``<wa-option :value>`` exposes —
     * the popover uses it directly via ``:value.prop``. The sentinel for
     * "follow default" is the literal string ``'__default__'``.
     */
    getDisplayedSelectValue(_field, selectedValue /* , context */) {
        return selectedValue === null ? '__default__' : String(selectedValue)
    }

    /**
     * Build the parts list rendered by the inline summary in the popover
     * trigger button. Each entry is ``{ text, forced }`` — ``forced=true``
     * adds a dashed underline so the user notices a non-default choice.
     *
     * ``state`` shape:
     * ```
     * { selected: { selected_model, effort, …}, defaults: {…} }
     * ```
     *
     * Default: one part per supported field, in registration order.
     * Providers override to merge or reorder fields (e.g. Claude appends
     * "[1m]" to the model when context_max is forced to EXTENDED, and
     * collapses model+context into a single part).
     */
    getSummaryParts(state) {
        const parts = []
        const sel = state?.selected ?? {}
        const def = state?.defaults ?? {}
        const fieldOrder = ['selected_model', 'effort', 'thinking_enabled', 'permission_mode', 'claude_in_chrome', 'context_max']
        for (const field of fieldOrder) {
            if (!this.supportsAgentSetting(field)) continue
            const selected = sel[field] ?? null
            const defaultValue = def[field]
            const effective = selected ?? defaultValue
            const text = field === 'selected_model'
                ? this.getModelLabel(effective)
                : (this.getChoiceDisplayLabel(field, effective) ?? this.getChoiceLabel(field, effective) ?? String(effective ?? ''))
            const forced = selected !== null && selected !== undefined && selected !== defaultValue
            parts.push({ text, forced })
        }
        return parts
    }

    /**
     * Copy of the warning shown in the popover when applying the pending
     * changes will require a process stop/restart. ``context`` carries
     * ``processStateName`` ('assistant_turn' / 'user_turn' / etc.),
     * ``hasMessageText`` (boolean) and ``hasCrons`` (boolean). Default uses
     * the provider's ``label`` for the agent's display name.
     */
    getStartupWarningText(context) {
        const label = this.constructor.label ?? 'Agent'
        const prefix = context?.processStateName === 'assistant_turn'
            ? `Once ${label} finishes its current work, the`
            : 'The'
        if (context?.hasCrons) {
            const suffix = context.hasMessageText ? ', after which your message will be sent.' : '.'
            return `${prefix} ${label} process will be stopped to apply these settings, then resumed to restart the current cron jobs${suffix}`
        }
        const suffix = context?.hasMessageText
            ? 'Your message will be sent after the process restarts.'
            : 'Your next message will resume the session.'
        return `${prefix} ${label} process will be stopped to apply these settings. ${suffix}`
    }

    /**
     * Resolve the model registry into the option groups rendered by the
     * model wa-select. Each group is ``{ entries: [{ value, label }] }`` —
     * adjacent groups are separated by a wa-divider. Default: a single
     * group flattening the registry. Claude overrides to split latest vs
     * older with a divider in between.
     */
    getModelSelectGroups(registry) {
        return [{
            entries: (registry ?? []).map(e => ({
                value: e.selected_model,
                label: this.getModelLabel(e.selected_model),
            })),
        }]
    }

}
