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
     * Activation prefixes this provider supports, in the order the snippets
     * bar should render their buttons. Each char drives one ``open-command``
     * button and one ``/api/projects/<id>/commands/?activation_char=<char>``
     * fetch when the picker opens. Providers that don't expose a command
     * vocabulary return ``[]`` (default). For example Claude Code returns
     * ``['/']``; Codex will eventually return ``['/', '$']``.
     */
    getCommandActivationChars() {
        return []
    }

    /**
     * Provider-specific note appended to the placeholder shown during
     * ``assistant_turn`` (after the generic "you can send a message now…"
     * sentence). Returns null when no note is needed. Example: Claude Code
     * appends a warning that the message won't appear in the conversation
     * history (the SDK queues it as in-band signal rather than persisting
     * it); providers that persist mid-turn messages (Codex via
     * ``turn/steer``) leave this null.
     */
    getPlaceholderAssistantTurnNote() {
        return null
    }

    /**
     * Built-in commands hardcoded by the provider's runtime/CLI for a given
     * activation prefix. Merged with the user-defined commands fetched from
     * the backend by the picker. The ``activationChar`` argument lets a
     * provider that exposes several prefixes (e.g. Codex's ``/`` and ``$``)
     * return a different list per prefix. Default: no built-ins.
     */
    getBuiltInCommands(/* activationChar */) {
        return []
    }

    /**
     * Build the parsed-content payload for the synthetic "optimistic" user
     * message — the bubble displayed in the chat the instant a user clicks
     * Send, before any backend round-trip. Provider-specific because the
     * item dispatcher in ``SessionItem.vue`` hands off to the provider's
     * own message renderer, which expects its own native JSONL shape (e.g.
     * Claude Code reads ``data.message.content[]``, Codex reads
     * ``data.payload.message``).
     *
     * Implementations return a plain object suitable for ``setParsedContent``
     * — it must include the ``syntheticKind`` marker
     * (``SYNTHETIC_ITEM.OPTIMISTIC_USER_MESSAGE.kind``) so visual-item
     * stabilization and cleanup (``recomputeVisualItems`` /
     * ``computeVisualItems``) treat it correctly.
     *
     * @param {string} text - The text the user just submitted (already trimmed).
     * @param {{ images?: Array, documents?: Array } | undefined} attachments
     *        Attachment blocks in SDK format. Providers that don't support
     *        attachments may safely ignore the argument.
     */
    buildOptimisticUserMessageContent(/* text, attachments */) {
        throw new Error(
            `buildOptimisticUserMessageContent() not implemented for provider ${this.constructor.provider}`,
        )
    }

    /**
     * Extract the user-facing text from a parsed user_message item, using
     * this provider's native JSONL shape. Used by generic frontend surfaces
     * that need to compare or preview user messages without knowing the
     * provider protocol.
     *
     * Providers that render user_message items or support optimistic messages
     * should override this. Return ``null`` when the parsed item is not a
     * user message or contains no text.
     *
     * @param {Object} parsed - Parsed session item content.
     * @returns {string|null}
     */
    extractUserMessageText(/* parsed */) {
        return null
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
     * ``{ label, modifier, url, tooltip }`` or ``null`` when the provider
     * doesn't expose a status surface or doesn't recognise the value.
     *
     * - ``label``: human-readable status name (e.g. "Operational")
     * - ``modifier``: one of ``ok`` / ``warning`` / ``error`` / ``info``,
     *   used by the footer to colour the dot
     * - ``url``: external link to the public status page
     * - ``tooltip``: hover text (e.g. "Claude code status on Anthropic's side")
     *
     * The provider identifier itself is shown via the ``PROVIDER_ICON`` map
     * in ``constants.js`` (rendered as ``<wa-icon>``), so the descriptor
     * does not carry a per-provider label here.
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

    // ─── Attachment capabilities ─────────────────────────────────────────
    //
    // Consumed by ``MessageInput.vue`` and ``addAttachment`` to gate the
    // file picker, the paste handler, and the per-file processing pipeline
    // (resize, max bytes) against what this provider can actually carry.
    // Each provider declares its own ceiling; generic code never branches
    // on the provider id itself.
    //
    // Shape:
    //   {
    //     images:            boolean,    // accept images at all?
    //     documents:         boolean,    // accept PDF / TXT?
    //     maxBytes:          number,     // hard per-file size cap (bytes)
    //     acceptedMimeTypes: string[],   // exact list, used both for the
    //                                    // <input accept> attribute and
    //                                    // for MIME validation
    //     resizeImages:      boolean,    // client-side downscale to
    //                                    // MAX_IMAGE_DIMENSION before
    //                                    // base64 encoding
    //   }

    /**
     * Attachment capabilities for this provider's send pipeline. Default:
     * provider accepts nothing — frontend hides the paperclip and refuses
     * all file picks / pastes. Providers override with the formats and
     * limits their runtime actually supports.
     */
    getAttachmentSupport() {
        return {
            images: false,
            documents: false,
            maxBytes: 0,
            acceptedMimeTypes: [],
            resizeImages: false,
        }
    }

    /**
     * Long-edge pixel cap to apply to images at send time for this
     * provider / model / batch-size combination, or ``null`` to leave
     * the stored images at their upload-time resolution.
     *
     * Images are stored at the shared ``MAX_IMAGE_DIMENSION`` (2576 px,
     * Opus 4.7's native resolution) regardless of the provider. The
     * send-time pipeline calls this hook with the (model, numImages)
     * context of the active turn so the provider can demand a tighter
     * cap — typically because the target model downscales below 2576
     * (Claude Sonnet/Haiku → 1568 px), or because Anthropic enforces a
     * 2000 px ceiling once a request carries more than 20 images.
     *
     * Returning ``null`` means "ship the stored blob as-is": correct
     * for Codex (its CLI does its own resize at 2048 px) and for
     * Claude Opus 4.7+ at ≤20 images.
     *
     * @param {{model?: string|null, numImages?: number}} _context
     * @returns {number|null} Pixel cap on the long edge, or null
     */
    getEffectiveImageDimension(/* { model, numImages } */) {
        return null
    }

}

/**
 * Base class for per-provider tool-rendering helpers consumed by the generic
 * `items/ToolUseContent.vue` shell. Mirrors the `BaseProviderHelpers` pattern:
 * each provider ships a subclass that overrides only the behaviours that
 * differ from the neutral defaults defined here. The shell never branches on
 * provider identity — it asks normalized questions and renders the answers.
 *
 * Two return shapes are used by the rendering hooks:
 *
 *   { component: VueComponent, props: object }   — explicit descriptor
 *   null                                          — fall back to JsonHumanView
 *
 * The `ctx` argument passed to `getInputRendering` / `getResultRendering`
 * carries the shell's runtime state. Common fields:
 *   - ``isSubagent``   — boolean; whether this tool_use lives in a subagent
 *   - ``backendPatch`` / ``originalFile`` / ``backendPatchLoading`` — for
 *     Edit/Write rendering when the backend has computed structured patches
 *   - ``sessionId`` / ``toolId`` — for input renderers that need to scan
 *     the store for a matching tool_result row on their own (e.g. Codex's
 *     ``apply_patch`` reading the linked ``patch_apply_end`` event)
 */
export class BaseToolHelpers {
    static provider = null

    // ─── Summary surface (header + working-assistant inline) ─────────────────────────────
    //
    // Producers: ToolUseContent.vue (header displayName branch),
    // WorkingAssistantMessage.vue (status line). Returned shape:
    //   { displayName: { name, namespace } | null,
    //     inline:      string | null }
    //
    // Variant-specific summary rendering (file/skill/grep/glob/web/todo)
    // flows through ``getSummaryRendering`` instead — that returns a
    // ``{ component, props }`` descriptor for the shell to mount.

    /** Build the summary descriptor for a tool_use. Default: minimal stub. */
    computeToolSummary(/* name, input, baseDir */) {
        return { displayName: null, inline: null }
    }

    /**
     * Convert a tool name + input to a gerund form for the
     * "{provider} is …ing" status line. Default: ``null`` (no verb known).
     */
    getVerb(/* name, input */) {
        return null
    }

    /**
     * Override for the rich card header label when the bare tool name is
     * not the right thing to display (e.g. a tool called ``TodoWrite``
     * should appear as ``Todo``). Returns a string, or ``null`` to let the
     * shell render the raw tool name (``name.replaceAll('__', ' ')``).
     *
     * The optional ``input`` argument lets providers compute a label that
     * depends on the tool_use's input — e.g. Codex's ``exec_command`` is
     * relabeled ``Read`` / ``List files`` / ``Grep`` / ``Exec`` based on
     * a parse of ``input.command``. The optional ``options`` carries
     * provider-specific extras (Codex uses ``{ wrapperType,
     * toolResultPayload }`` so its helper can prefer the official
     * ``parsed_cmd`` from the ``event_msg.*_end`` event when it has
     * arrived). Default helper ignores both.
     *
     * Note: this is for per-name static (or per-input) overrides; dynamic
     * overrides driven by complex input shapes (e.g. Task ``subagent_type``
     * with namespace) still flow through
     * ``computeToolSummary().displayName`` and the ``isTask && displayName``
     * template branch. Default: no override.
     */
    getHeaderLabel(/* name, input, options */) {
        return null
    }

    // ─── Input / Result rendering ────────────────────────────────────────

    /**
     * Return ``{ component, props }`` for the tool's input area, or ``null``
     * to fall back to ``JsonHumanView``. ``ctx`` carries the shell's runtime
     * state (see class docstring). Default: always fall back.
     */
    getInputRendering(/* name, input, ctx */) {
        return null
    }

    /**
     * Return ``{ component, props }`` for the tool's Result area, or ``null``
     * to fall back to ``JsonHumanView``. Called when the tool result has been
     * fetched and is non-empty. Default: always fall back.
     */
    getResultRendering(/* name, result, input, ctx */) {
        return null
    }

    /**
     * Return ``{ component, props }`` for the tool's summary description (the
     * text shown after the tool name and the em-dash separator in the rich
     * card header), or ``null`` to render no description.
     *
     * The optional ``options`` carries provider-specific extras (Codex
     * passes ``{ wrapperType, toolResultPayload }`` so its helper can
     * prefer the official ``parsed_cmd`` from the matching
     * ``event_msg.*_end`` event when available). Default helper ignores it.
     */
    getSummaryRendering(/* name, input, baseDir, options */) {
        return null
    }

    /**
     * Per-tool overrides for ``JsonHumanView`` when rendering the input
     * fallback (e.g. force ``Bash.command`` to render as a code block).
     * Default: no overrides.
     */
    getInputOverrides(/* name */) {
        return {}
    }

    /**
     * Per-tool overrides for ``JsonHumanView`` when rendering the result
     * fallback. Default: no overrides.
     */
    getResultOverrides(/* name */) {
        return {}
    }

    // ─── Tool input value extraction ─────────────────────────────────────
    //
    // Hooks the shell uses to read provider-specific values out of an opaque
    // tool input. Each provider knows the shape of its own input; the shell
    // only reads the answers and never branches on field names itself.

    /**
     * Path of the file this tool acts on, or ``null`` when the tool's input
     * doesn't carry one. Powers the per-tool code-comments grouping and is
     * the building block for the View-in-Files button (see
     * ``getOpenInFilesTarget``). Default: no file path.
     */
    getFilePath(/* name, input */) {
        return null
    }

    /**
     * Target for the View-in-Files button: ``{ filePath, lineHint }`` or
     * ``null`` when the button shouldn't surface for this tool. ``ctx`` may
     * carry runtime state inferred by the shell — currently
     * ``firstModifiedLine`` (computed from the backend patch for Edit/Write).
     * Default: derive from ``getFilePath`` with no line hint, so any provider
     * that overrides ``getFilePath`` automatically gets a working button.
     */
    getOpenInFilesTarget(name, input /*, ctx */) {
        const filePath = this.getFilePath(name, input)
        return filePath ? { filePath, lineHint: null } : null
    }

    /**
     * Input object the JsonHumanView fallback should render, or ``null`` when
     * the input is empty / fully consumed by other surfaces. Providers strip
     * fields they've already surfaced elsewhere (e.g. Claude Code's
     * ``description`` is rendered in the summary header). Default: returns
     * ``input`` unchanged when non-empty, ``null`` when empty.
     */
    getDisplayInputObject(name, input) {
        if (!input || Object.keys(input).length === 0) return null
        return input
    }

    /**
     * Number of tool_result events the shell should expect before the tool
     * is considered done. Most tools emit a single result; some emit two
     * (e.g. Claude Code's Bash with ``run_in_background``, or Codex tools
     * that have both a ``function_call_output`` and a richer
     * ``event_msg.*_end`` paired by ``call_id``). The shell uses the
     * answer to drive the running spinner.
     *
     * @param {string} name - Tool name as it appears in the tool_use.
     * @param {Object} input - Parsed tool input object.
     * @param {Object} [options] - Provider-specific extras. Codex passes
     *   ``{ wrapperType }`` (one of ``function_call``, ``custom_tool_call``,
     *   ``local_shell_call``, ``web_search_call``, ``image_generation_call``)
     *   so the helper can branch on the wrapper without re-parsing the
     *   raw JSONL line. Helpers that don't care just ignore it.
     * @returns {number} Default: 1.
     */
    getExpectedResultCount(/* name, input, options */) {
        return 1
    }

    /**
     * Number of tool_result rows the Result section needs to have in
     * memory before it can render anything useful. Distinct from
     * ``getExpectedResultCount``: the latter drives the *running
     * spinner* (was the tool ever marked complete?), while this one
     * gates the *Result rendering* (do we already have what we need
     * to show a meaningful body?).
     *
     * Most tools render their result as soon as a single row arrives,
     * so the default is 1 — including Claude Code's Bash with
     * ``run_in_background``: there, the first result already carries
     * the canonical "Command running in background" notice and the
     * second one is just a completion stub. Codex's ``apply_patch``
     * still raises it to 2 so the rich ``patch_apply_end`` row is
     * always part of what's displayed; ``exec_command`` returns 1 and
     * relies on :meth:`isToolRunning` to keep the spinner spinning
     * across the chain of polled chunks.
     *
     * Returning ``> resultData.length`` makes the shell:
     *   - render the "Result not yet available — checking again
     *     shortly…" message + spinner
     *   - keep polling the ``/tool-results/`` endpoint at the regular
     *     interval until the threshold is reached.
     *
     * @returns {number} Default: 1.
     */
    getRequiredResultCountForDisplay(/* name, input, options */) {
        return 1
    }

    /**
     * Whether the tool is still running (spinner ON, polling kept alive).
     *
     * Default: count-based — the tool is considered running until
     * ``toolState.resultCount`` reaches ``getExpectedResultCount``.
     * Providers can override to inspect richer signals when the result
     * count is variable. Codex's ``exec_command`` family overrides this
     * to read ``toolState.extra.is_terminated`` (set by
     * ``compute_link_extra`` on the closing chunk of the chain) since
     * the number of polling chunks is not known up-front.
     *
     * @param {string} name - Tool name as it appears in the tool_use.
     * @param {Object} input - Parsed tool input object.
     * @param {Object} [options] - Same shape as the other tool helpers.
     *   ``options.toolState`` is the per-tool aggregated state from the
     *   data store (notably ``resultCount`` and ``extra``).
     * @returns {boolean} Default: ``resultCount < expectedCount``.
     */
    isToolRunning(name, input, options) {
        const resultCount = options?.toolState?.resultCount ?? 0
        const expected = this.getExpectedResultCount(name, input, options)
        return resultCount < expected
    }

    /**
     * Whether the Result section should aggregate output across multiple
     * tool_result rows for this tool (chained ``function_call_output``
     * chunks for Codex's ``exec_command`` family) before handing the
     * concatenated body to the rendering helper.
     *
     * Default: ``false`` (the renderer receives the single row from
     * ``displayResult`` as-is). Providers that own a chained-result
     * tool override this to ``true`` so the shell precomputes the
     * aggregated payload and exposes it to ``getResultRendering`` via
     * ``options.aggregatedExecOutput``.
     *
     * @returns {boolean} Default: false.
     */
    shouldAggregateExecOutput(/* name */) {
        return false
    }

    /**
     * Compute the aggregated payload to feed ``getResultRendering`` for
     * a chained-result tool. Only invoked by the shell when
     * :meth:`shouldAggregateExecOutput` returned ``true`` for this
     * tool, so the default implementation can stay null.
     *
     * Providers walk their result chain (e.g. Codex iterates the
     * ``toolState.toolResultLineNums`` to concatenate every
     * ``function_call_output``'s body and parse the trailing status)
     * and return the aggregate ready for rendering. The shape is
     * provider-specific — the shell only exposes it via
     * ``options.aggregatedExecOutput``.
     *
     * @param {string} _name - tool name (e.g. ``"exec_command"``,
     *   ``"local_shell_call"``). Providers that handle several chained
     *   tools with different output dialects branch on this.
     * @param {string} _toolId - tool_use_id whose chain to aggregate.
     * @param {Object} _options - same shape as the other helpers; in
     *   particular carries ``getSessionItem`` / ``getToolState`` so the
     *   helper can reach the chain rows in the data store.
     * @returns {*} Default: ``null``.
     */
    getAggregatedExecOutput(/* name, toolId, options */) {
        return null
    }

    /**
     * Pick / transform the value passed to ``getResultRendering`` and
     * ``JsonHumanView`` from the raw array of ToolResultLink rows that
     * the shell loaded for this tool. Returning ``undefined`` leaves the
     * default behaviour in place (single → object, multiple → array);
     * any other return value (including ``null``) overrides it.
     *
     * Use case: tools that accumulate several ToolResultLinks for the
     * same call_id but where only one of them carries the user-facing
     * payload. Codex's MCP tools, for example, get both a
     * ``function_call_output`` (LLM-facing text) and an
     * ``event_msg.mcp_tool_call_end`` (parsed result with
     * ``structuredContent``) — only the latter is worth showing in the
     * Result section. The hook lets the provider helper pick it out
     * without changing how ``ToolResultLink`` rows are stored or counted.
     *
     * @param {string} _name - Tool name.
     * @param {Array} _resultData - Array of parsed result rows (same
     *   shape as ``data.results`` from the ``/tool-results/`` endpoint).
     * @param {Object} _options - Provider-specific extras.
     * @returns {*} ``undefined`` to keep default; any other value to
     *   override.
     */
    transformDisplayResult(/* name, resultData, options */) {
        return undefined
    }

    // ─── Capability flags driving shell-level features ───────────────────

    /**
     * Whether this tool modifies a file (so the shell shows ``+N -N`` stats
     * in the header and may auto-open it for live diffs). Default: false.
     */
    isFileChangeTool(/* name */) {
        return false
    }

    /**
     * Whether this tool is an "Agent / Task" spawner (the shell shows the
     * View-Agent button + spinner / robot icon). Default: false.
     */
    isAgentTool(/* name */) {
        return false
    }

    /**
     * Whether the shell should auto-open the details for this tool when the
     * item arrives live via WebSocket and the user has ``settings.showDiffs``
     * enabled. Default: false.
     */
    shouldAutoOpenLive(/* name, input */) {
        return false
    }

    /**
     * Whether the Result section should remain visible when this tool
     * errored, even though there is no specialized input renderer claiming
     * it. Used for tools whose error message is too terse on its own (e.g.
     * Bash's "Exit code N" — the user wants to see the full stdout/stderr
     * via the Result fallback). The shell also falls back to showing the
     * Result for the special ``'Unknown error'`` text regardless of what
     * this method returns. Default: false.
     */
    showsResultOnError(/* name */) {
        return false
    }

    /**
     * Whether the Result section should be shown when this tool has a
     * specialized input renderer AND the error is the special
     * ``'Unknown error'`` text. Used to surface diagnostic detail for tools
     * whose specialized input UI alone isn't enough (Edit/Write opt in;
     * TodoWrite stays hidden). Default: false.
     */
    showsResultOnUnknownError(/* name */) {
        return false
    }

    /**
     * Whether the error text for this tool should be rendered as Markdown
     * (via ``MarkdownContent``) instead of plain text. Used for tools whose
     * error message is itself markdown content (e.g. ExitPlanMode emits a
     * markdown-formatted plan that the user wants to read rendered).
     * Default: false (render as plain text).
     */
    errorIsMarkdown(/* name */) {
        return false
    }

    // ─── File-change stats (header ``+N -N``) ────────────────────────────
    //
    // The shell does the layout (``+N -N``); the helper provides the values.
    // ``toolState`` is the dataStore.getToolState(...) entry (carries
    // ``extra`` JSON when the backend computed stats). ``isSubagent`` flips
    // the source — backend stats for the main session, computed-from-input
    // for subagents.

    /**
     * Compute ``{ lines_added, lines_removed? }`` for the ``+N -N`` header,
     * or ``null`` when this tool doesn't carry stats. Default: null.
     */
    computeFileChangeStats(/* name, input, toolState, isSubagent */) {
        return null
    }

    // ─── Backend-patch fetcher (used by Edit/Write to draw full-file diffs)
    //
    // Some providers post-process the tool_result item to attach a
    // structured patch and the original file content. The shell handles
    // the fetch (the parsed item is read from the store or via
    // ``loadSessionItemsRanges``); the helper decides whether the fetch is
    // needed and how to extract the data.

    /**
     * Whether the shell should fetch the tool_result item and pass extracted
     * data through ``ctx`` to ``getInputRendering``. Default: false.
     */
    needsBackendPatchFetch(/* name */) {
        return false
    }

    /**
     * Extract ``{ patch, originalFile }`` from a parsed tool_result item, or
     * ``null`` when the data is absent. Default: never extracts.
     */
    extractBackendPatchData(/* parsedToolResult */) {
        return null
    }
}
