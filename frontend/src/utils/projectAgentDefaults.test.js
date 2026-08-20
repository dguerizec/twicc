import { test } from 'node:test'
import assert from 'node:assert/strict'
import { resolveProjectAgentDefaults } from './projectAgentDefaults.js'

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
