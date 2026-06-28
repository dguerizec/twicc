// Route names where per-tab navigation applies, shared by App.vue (the
// Alt+Ctrl+Shift keydown dispatch) and the command registry (route-gated
// "Go to … tab" commands), so the gate stays defined in one place.

// Terminal panel routes — session + project/worktree scopes.
export const TERMINAL_ROUTES = new Set([
    'session-terminal', 'projects-session-terminal',
    'project-terminal', 'projects-terminal',
])

// Workflows pane routes — session scope only.
export const WORKFLOW_ROUTES = new Set([
    'session-workflows', 'projects-session-workflows',
])
