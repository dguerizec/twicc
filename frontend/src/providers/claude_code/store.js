// frontend/src/providers/claude_code/store.js

import { defineStore, acceptHMRUpdate } from 'pinia'

export const useClaudeCodeStore = defineStore('claudeCode', {
    state: () => ({
        // Claude CLI authentication state (from claude_code:auth_updated messages).
        // null = unknown (no message received yet), true/false = known state.
        // Driven by the backend's auth_task (periodic check) and on-connect push.
        authenticated: null,
    }),

    actions: {
        setAuthenticated(authenticated) {
            this.authenticated = authenticated
        },
    },
})

if (import.meta.hot) {
    import.meta.hot.accept(acceptHMRUpdate(useClaudeCodeStore, import.meta.hot))
}
