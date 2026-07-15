// Run with: node --test src/utils/layoutDrag.test.js (from the frontend dir)
import test from 'node:test'
import assert from 'node:assert/strict'

import { dropZoneAt, layoutDropZones, moveLayoutTabOrder, orderLayoutTabs } from './layoutDrag.js'

test('orderLayoutTabs anchors fixed-center tabs and applies persisted tool order', () => {
    const tabs = [
        { id: 'main', fixedCenter: true },
        { id: 'files' },
        { id: 'git' },
        { id: 'terminal' },
    ]
    assert.deepEqual(orderLayoutTabs(tabs, ['terminal', 'files']).map((tab) => tab.id), [
        'main', 'terminal', 'files', 'git',
    ])
})

test('moveLayoutTabOrder preserves absent remembered tabs while reordering known tabs', () => {
    assert.deepEqual(moveLayoutTabOrder({
        tabOrder: ['files', 'optional-plan', 'git'],
        knownTabIds: ['files', 'git', 'terminal'],
        tabId: 'terminal',
        targetTabId: 'git',
        position: 'before',
    }), ['files', 'optional-plan', 'terminal', 'git'])
})

test('dropping back onto the source header keeps its order unchanged', () => {
    assert.deepEqual(moveLayoutTabOrder({
        tabOrder: ['files', 'git', 'terminal'],
        knownTabIds: ['files', 'git', 'terminal'],
        tabId: 'git',
        targetTabId: 'git',
        position: 'after',
    }), ['files', 'git', 'terminal'])
})

test('layoutDropZones creates every dock target and uses the existing dock extent', () => {
    const render = {
        mode: 'widescreen', viewport: { w: 1200, h: 800 },
        regions: [{ kind: 'col-left', x: 0, y: 0, w: 300, h: 800 }], gutters: [],
    }
    const zones = layoutDropZones(1200, 800, render)
    assert.deepEqual(zones.map((zone) => zone.dockId), [
        'left-top', 'left-bottom', 'center', 'right-top', 'right-bottom', 'bottom-left', 'bottom-right',
    ])
    assert.equal(zones[0].w, 300)
    assert.equal(dropZoneAt(zones, 100, 100)?.dockId, 'left-top')
    assert.equal(dropZoneAt(zones, 100, 500)?.dockId, 'left-bottom')
    assert.equal(dropZoneAt(zones, 900, 790)?.dockId, 'bottom-right')
})

test('mobile fallback exposes only the main-area reorder target', () => {
    assert.deepEqual(layoutDropZones(500, 700, { mode: 'tabs' }), [
        { dockId: 'center', x: 0, y: 0, w: 500, h: 700 },
    ])
})
