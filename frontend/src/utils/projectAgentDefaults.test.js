import { test } from 'node:test'
import assert from 'node:assert/strict'
import { resolveDraftProvider, resolveProjectAgentDefaults } from './projectAgentDefaults.js'

test('can resolve inherited project defaults without the current project values', () => {
    const projects = {
        root: {
            id: 'root',
            directory: '/workspace',
            default_agent_settings: {
                codex: { selected_model: 'root-model', effort: 'medium' },
            },
        },
        parent: {
            id: 'parent',
            directory: '/workspace/parent',
            default_agent_settings: {
                codex: { selected_model: 'parent-model', effort: null },
            },
        },
        child: {
            id: 'child',
            directory: '/workspace/parent/child',
            default_agent_settings: {
                codex: { selected_model: 'child-model', effort: 'high' },
            },
        },
    }

    assert.deepEqual(
        resolveProjectAgentDefaults('child', 'codex', projects, { includeSelf: false }),
        { selected_model: 'parent-model', effort: 'medium' },
    )
})

test('an explicit draft provider wins over the inherited default provider', () => {
    const projects = {
        root: {
            id: 'root',
            directory: '/repo',
            default_provider: 'codex',
        },
        worktree: {
            id: 'worktree',
            directory: '/repo/.worktrees/feature',
            worktree_of: 'root',
            default_provider: null,
        },
    }

    assert.equal(
        resolveDraftProvider('worktree', projects, 'codex', 'claude_code'),
        'claude_code',
    )
    assert.equal(resolveDraftProvider('worktree', projects, 'claude_code'), 'codex')
    assert.equal(resolveDraftProvider('missing', projects, 'claude_code'), 'claude_code')
})
