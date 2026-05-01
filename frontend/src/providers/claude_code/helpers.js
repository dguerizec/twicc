import { BaseProviderHelpers } from '../baseHelpers'
import { PROVIDER } from '../../constants'
import { useClaudeCodeStore } from './store'

export class ClaudeCodeHelpers extends BaseProviderHelpers {
    static provider = PROVIDER.CLAUDE_CODE

    canSendMessage() {
        return useClaudeCodeStore().authenticated !== false
    }
}

export const claudeCodeHelpers = new ClaudeCodeHelpers()
