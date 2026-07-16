// Run with: node --test src/utils/visualItems.test.js (from the frontend dir)
import test from 'node:test'
import assert from 'node:assert/strict'

import { DISPLAY_LEVEL, DISPLAY_MODE } from '../constants.js'
import { computeVisualItems } from './visualItems.js'

function makeItem(lineNum, displayLevel, groupHead = null) {
    return {
        line_num: lineNum,
        content: JSON.stringify({
            type: 'assistant',
            message: { content: [{ type: 'thinking', thinking: 'Working…' }] },
        }),
        kind: displayLevel === DISPLAY_LEVEL.ALWAYS ? 'assistant_message' : 'content_items',
        display_level: displayLevel,
        group_head: groupHead,
        group_tail: null,
    }
}

test('simplified visual items identify every externally grouped item', () => {
    const items = [
        makeItem(1, DISPLAY_LEVEL.COLLAPSIBLE, 1),
        makeItem(2, DISPLAY_LEVEL.COLLAPSIBLE, 1),
        makeItem(3, DISPLAY_LEVEL.ALWAYS),
    ]

    const visualItems = computeVisualItems(items, DISPLAY_MODE.SIMPLIFIED, [1])

    assert.deepEqual(
        visualItems.map(item => [item.lineNum, item.externallyGrouped]),
        [[1, true], [2, true], [3, false]],
    )
    assert.equal(visualItems[0].isGroupHead, true)
})
