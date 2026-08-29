// Pure helpers for dock-tab drag and drop. Keeping the ordering and target geometry outside Vue
// makes pointer handling in SessionLayout small and lets the non-trivial edge decisions stay tested.

const clamp = (value, min, max) => Math.max(min, Math.min(max, value))

/** Apply the persisted order to the live tab roster. Fixed-center tabs remain anchored first. */
export function orderLayoutTabs(tabs, tabOrder) {
    const rank = new Map((tabOrder || []).map((id, index) => [id, index]))
    return (tabs || [])
        .map((tab, index) => ({ tab, index }))
        .sort((a, b) => {
            if (!!a.tab.fixedCenter !== !!b.tab.fixedCenter) return a.tab.fixedCenter ? -1 : 1
            const ar = rank.has(a.tab.id) ? rank.get(a.tab.id) : Number.MAX_SAFE_INTEGER
            const br = rank.has(b.tab.id) ? rank.get(b.tab.id) : Number.MAX_SAFE_INTEGER
            return ar - br || a.index - b.index
        })
        .map(({ tab }) => tab)
}

/**
 * Move one tab in the persisted global order. Filtering that order by dock gives each dock's order,
 * so a single list covers center, split docks, gutters and overlays without divergent memories.
 */
export function moveLayoutTabOrder({ tabOrder, knownTabIds, tabId, targetTabId = null, position = 'after' }) {
    const all = []
    const seen = new Set()
    for (const id of [...(tabOrder || []), ...(knownTabIds || [])]) {
        if (!id || seen.has(id)) continue
        seen.add(id)
        all.push(id)
    }
    if (!seen.has(tabId)) all.push(tabId)
    if (targetTabId === tabId) return all

    const without = all.filter((id) => id !== tabId)
    const targetIndex = targetTabId && targetTabId !== tabId ? without.indexOf(targetTabId) : -1
    if (targetIndex === -1) {
        without.push(tabId)
    } else {
        without.splice(targetIndex + (position === 'after' ? 1 : 0), 0, tabId)
    }
    return without
}

function edgeExtent(render, edge, fallback, max) {
    const regions = render?.regions || []
    const gutters = render?.gutters || []
    if (edge === 'left') {
        const farthest = Math.max(0, ...regions.filter((r) => r.kind === 'col-left').map((r) => r.x + r.w),
            ...gutters.filter((g) => g.edge === 'left').map((g) => g.x + g.w))
        return clamp(Math.max(fallback, farthest), fallback, max)
    }
    if (edge === 'right') {
        const viewportW = render?.viewport?.w || 0
        const farthest = Math.max(0, ...regions.filter((r) => r.kind === 'col-right').map((r) => viewportW - r.x),
            ...gutters.filter((g) => g.edge === 'right').map((g) => viewportW - g.x))
        return clamp(Math.max(fallback, farthest), fallback, max)
    }
    const viewportH = render?.viewport?.h || 0
    const farthest = Math.max(0, ...regions.filter((r) => r.kind === 'bottom').map((r) => viewportH - r.y),
        ...gutters.filter((g) => g.edge === 'bottom').map((g) => viewportH - g.y))
    return clamp(Math.max(fallback, farthest), fallback, max)
}

/**
 * Seven non-overlapping IDE-style target zones. Existing dock extents enlarge their edge target;
 * absent docks still get a generous hot zone, which is what lets a drop create them live.
 */
export function layoutDropZones(width, height, render) {
    if (!(width > 0 && height > 0)) return []
    // The mobile tab strip has no docks and no layout affordances: no target, so no drag at all.
    if (render?.mode === 'tabs') return []

    const baseSide = clamp(width * 0.2, 112, Math.max(112, width * 0.3))
    const baseBottom = clamp(height * 0.24, 108, Math.max(108, height * 0.34))
    let leftW = edgeExtent(render, 'left', baseSide, width * 0.34)
    let rightW = edgeExtent(render, 'right', baseSide, width * 0.34)
    const bottomH = Math.min(height, edgeExtent(render, 'bottom', baseBottom, height * 0.4))

    // Always leave a useful main-area target, even after unusually wide persisted dock fractions.
    const centerMin = Math.min(240, width * 0.38)
    const sideTotalMax = Math.max(0, width - centerMin)
    if (leftW + rightW > sideTotalMax) {
        const scale = sideTotalMax / (leftW + rightW || 1)
        leftW *= scale
        rightW *= scale
    }

    const upperH = height - bottomH
    const leftTopH = upperH / 2
    const centerW = width - leftW - rightW
    return [
        { dockId: 'left-top', x: 0, y: 0, w: leftW, h: leftTopH },
        { dockId: 'left-bottom', x: 0, y: leftTopH, w: leftW, h: upperH - leftTopH },
        { dockId: 'center', x: leftW, y: 0, w: centerW, h: upperH },
        { dockId: 'right-top', x: width - rightW, y: 0, w: rightW, h: leftTopH },
        { dockId: 'right-bottom', x: width - rightW, y: leftTopH, w: rightW, h: upperH - leftTopH },
        { dockId: 'bottom-left', x: 0, y: upperH, w: width / 2, h: bottomH },
        { dockId: 'bottom-right', x: width / 2, y: upperH, w: width - width / 2, h: bottomH },
    ]
}

export function dropZoneAt(zones, x, y) {
    return (zones || []).find((zone) =>
        x >= zone.x && x < zone.x + zone.w && y >= zone.y && y < zone.y + zone.h
    ) || null
}
