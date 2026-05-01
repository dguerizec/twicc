import { ClaudeCodeHelpers, claudeCodeHelpers } from './claude_code/helpers'
import { claudeCodeWsHandler } from './claude_code/ws'

const PROVIDER_HELPERS = {
    [ClaudeCodeHelpers.provider]: claudeCodeHelpers,
}

const PROVIDER_WS_HANDLERS = {
    [ClaudeCodeHelpers.provider]: claudeCodeWsHandler,
}

export function getProviderHelpers(provider) {
    return PROVIDER_HELPERS[provider] ?? null
}

export function getProviderWsHandler(provider) {
    return PROVIDER_WS_HANDLERS[provider] ?? null
}
