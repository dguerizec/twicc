import { ClaudeCodeHelpers, claudeCodeHelpers } from './claude_code/helpers'
import { claudeCodeWsHandler } from './claude_code/ws'
import { useClaudeCodeStore } from './claude_code/store'
import { CodexHelpers, codexHelpers } from './codex/helpers'
import { codexWsHandler } from './codex/ws'
import { useCodexStore } from './codex/store'

const PROVIDER_HELPERS = {
    [ClaudeCodeHelpers.provider]: claudeCodeHelpers,
    [CodexHelpers.provider]: codexHelpers,
}

const PROVIDER_WS_HANDLERS = {
    [ClaudeCodeHelpers.provider]: claudeCodeWsHandler,
    [CodexHelpers.provider]: codexWsHandler,
}

// Pinia store factory per provider — used by generic dispatchers (bootstrap
// seed, ``usage_updated`` WS) to write into the right provider-scoped store
// without knowing which provider it is.
const PROVIDER_STORE_FACTORIES = {
    [ClaudeCodeHelpers.provider]: useClaudeCodeStore,
    [CodexHelpers.provider]: useCodexStore,
}

export function getProviderHelpers(provider) {
    return PROVIDER_HELPERS[provider] ?? null
}

export function getProviderWsHandler(provider) {
    return PROVIDER_WS_HANDLERS[provider] ?? null
}

export function getProviderStore(provider) {
    const factory = PROVIDER_STORE_FACTORIES[provider]
    return factory ? factory() : null
}

export function getRegisteredProviders() {
    return Object.keys(PROVIDER_HELPERS)
}

/**
 * Human-readable label for the given provider, suitable for inlining in
 * user-facing strings (toasts, tooltips, placeholders). Falls back to
 * ``'Agent'`` when the provider is unknown so callers don't have to guard.
 */
export function getProviderLabel(provider) {
    return PROVIDER_HELPERS[provider]?.constructor.label ?? 'Agent'
}

/**
 * ``[{ value, label }]`` pairs for every registered provider, intended to feed
 * a ``<wa-select>`` (e.g. the global default-provider setting). The label
 * comes from the helpers' static ``label`` field, falling back to the wire
 * key when a provider hasn't declared one.
 */
export function getProviderOptions() {
    return Object.entries(PROVIDER_HELPERS).map(([value, helpers]) => ({
        value,
        label: helpers.constructor.label ?? value,
    }))
}
