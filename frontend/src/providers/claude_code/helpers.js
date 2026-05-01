import { BaseProviderHelpers } from '../baseHelpers'
import { PROVIDER } from '../../constants'
import { useClaudeCodeStore } from './store'

// Claude CLI's built-in slash commands. Hardcoded here because the CLI
// never exposes the list programmatically; entries are sourced from the
// CLI documentation.
const BUILTIN_SLASH_COMMANDS = [
    { name: 'compact', plugin_name: null, source: 'builtin', is_global: true, description: 'Clear conversation history but keep a summary in context', argument_hint: '[instructions for summarization]' },
    { name: 'cost', plugin_name: null, source: 'builtin', is_global: true, description: 'Show the cost of the current session', argument_hint: null },
    { name: 'context', plugin_name: null, source: 'builtin', is_global: true, description: 'Show the current context window usage', argument_hint: null },
    { name: 'init', plugin_name: null, source: 'builtin', is_global: true, description: 'Initialize a new CLAUDE.md file with codebase documentation', argument_hint: null },
    { name: 'loop', plugin_name: null, source: 'builtin', is_global: true, description: "Run a prompt or slash command on a recurring interval until the session ends (e.g. /loop 5m /foo, defaults to 10m)", argument_hint: '[interval] [command or prompt]' },
]

export class ClaudeCodeHelpers extends BaseProviderHelpers {
    static provider = PROVIDER.CLAUDE_CODE

    canSendMessage() {
        return useClaudeCodeStore().authenticated !== false
    }

    getBuiltInSlashCommands() {
        return BUILTIN_SLASH_COMMANDS
    }
}

export const claudeCodeHelpers = new ClaudeCodeHelpers()
