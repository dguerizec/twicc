// frontend/src/utils/layoutDefaults.js
//
// Per-project default layout: frontend chain resolver. Mirrors the agent-defaults resolver
// (projectAgentDefaults.js) and reuses its ancestor walk. Resolution is a CREATION-TIME concern:
// it picks the layout a NEW session opens with, then the layout is frozen onto the session and
// freely edited (a later default change never touches an existing session). The backend mirror runs
// only when the CLI creates a session.
//
// Two sentinels, never conflated:
//   - a project's `default_layout_id` is NULL / undefined  → "inherit" (walk further up the chain);
//   - the id string `"single-pane"`                        → explicit no-docks (stops the walk);
//   - any named-layout id                                  → explicit choice (stops the walk).
// The global default (settings.json `defaultLayoutId`) is the chain's root and always concrete.

import { ancestorChain } from './projectAgentDefaults'

/**
 * Resolve a project's inherited default layout id (the worktree/path chain → global fallback).
 * @param {string} projectId
 * @param {Object} projectsById - the projects keyed by id (the store's `projects`)
 * @param {string|null} globalDefaultId - settings.json `defaultLayoutId` (null until synced)
 * @returns {string} a layout id — a named id or 'single-pane' (never null/inherit).
 */
export function resolveProjectLayoutId(projectId, projectsById, globalDefaultId) {
    if (projectId) {
        for (const node of ancestorChain(projectId, projectsById)) {
            const id = node.default_layout_id
            // First explicit choice wins (incl. 'single-pane'); NULL/undefined = inherit further up.
            if (id != null) return id
        }
    }
    return globalDefaultId || 'single-pane'
}
