import { ClaudeCodeHelpers, claudeCodeHelpers } from './claude_code/helpers'
import { claudeCodeWsHandler } from './claude_code/ws'
import { useClaudeCodeStore } from './claude_code/store'

const PROVIDER_HELPERS = {
    [ClaudeCodeHelpers.provider]: claudeCodeHelpers,
}

const PROVIDER_WS_HANDLERS = {
    [ClaudeCodeHelpers.provider]: claudeCodeWsHandler,
}

// Pinia store factory per provider — used by generic dispatchers (bootstrap
// seed, ``usage_updated`` WS) to write into the right provider-scoped store
// without knowing which provider it is.
const PROVIDER_STORE_FACTORIES = {
    [ClaudeCodeHelpers.provider]: useClaudeCodeStore,
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
