import { ClaudeCodeHelpers, claudeCodeHelpers } from './claude_code/helpers'

const PROVIDER_HELPERS = {
    [ClaudeCodeHelpers.provider]: claudeCodeHelpers,
}

export function getProviderHelpers(provider) {
    return PROVIDER_HELPERS[provider] ?? null
}
