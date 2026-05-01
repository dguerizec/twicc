// frontend/src/providers/claude_code/store.js

import { defineStore, acceptHMRUpdate } from 'pinia'

export const useClaudeCodeStore = defineStore('claudeCode', {
    state: () => ({
        // Claude CLI authentication state (from claude_code:auth_updated messages).
        // null = unknown (no message received yet), true/false = known state.
        // Driven by the backend's auth_task (periodic check) and on-connect push.
        authenticated: null,

        // Anthropic statuspage component status (from claude_code:anthropic_status
        // messages). Defaults to 'operational' so the UI doesn't flash a warning
        // before the first push arrives.
        anthropicStatus: 'operational',
    }),

    actions: {
        setAuthenticated(authenticated) {
            this.authenticated = authenticated
        },
        setAnthropicStatus(status) {
            this.anthropicStatus = status
        },
    },
})

if (import.meta.hot) {
    import.meta.hot.accept(acceptHMRUpdate(useClaudeCodeStore, import.meta.hot))
}
