import { test } from 'node:test'
import assert from 'node:assert/strict'
import { bundleSummaryParts, presetSummaryParts } from './presetFormat.js'

const labels = {
    context_max: { 200000: '200k' },
    effort: { medium: 'Medium', high: 'High' },
    permission_mode: { auto: 'Auto', yolo: 'YOLO' },
}

const helpers = {
    supportsAgentSetting: () => true,
    getSummaryModelLabel: value => ({ default_model: 'Default model', preset_model: 'Preset model' })[value] ?? value,
    getSummaryModelSuffix: () => '',
    getEffortIconSrc: value => `effort:${value}`,
    getChoiceDisplayLabel: (field, value) => labels[field]?.[value] ?? null,
    getChoiceLabel: (field, value) => labels[field]?.[value] ?? null,
    getChoiceIcon: (field, value) => field === 'permission_mode'
        ? { icon: `permission:${value}`, color: value }
        : null,
}

test('shows inherited defaults for nullable preset fields', () => {
    const parts = presetSummaryParts({
        model: null,
        context_max: null,
        effort: 'high',
        thinking: null,
        permission_mode: null,
        permission_mode_if_untrusted: null,
        claude_in_chrome: null,
        fast_mode: false,
    }, helpers, {
        defaults: {
            selected_model: 'default_model',
            context_max: 200000,
            effort: 'medium',
            thinking_enabled: false,
            permission_mode: 'auto',
            claude_in_chrome: true,
            fast_mode: true,
        },
    })

    assert.deepEqual(parts, [
        {
            text: 'Default model',
            effortSrc: 'effort:high',
            effortLabel: 'High',
            forced: false,
        },
        { text: '200k', forced: false },
        {
            field: 'thinking_enabled',
            on: false,
            forced: false,
            label: 'No thinking',
            groupWithPrevious: true,
        },
        {
            text: 'Auto',
            permissionIcon: 'permission:auto',
            permissionColor: 'auto',
            forced: false,
        },
        { field: 'fast_mode', on: false, forced: false, label: 'No fast mode' },
        { field: 'claude_in_chrome', on: true, forced: false, label: 'Chrome MCP' },
    ])
})

test('compares the resolved preset values with the current session values', () => {
    const [modelPart] = presetSummaryParts({
        model: null,
        effort: 'high',
    }, helpers, {
        defaults: {
            selected_model: 'default_model',
            effort: 'medium',
        },
        current: {
            selected_model: 'default_model',
            effort: 'medium',
        },
    })

    assert.equal(modelPart.text, 'Default model')
    assert.equal(modelPart.effortLabel, 'High')
    assert.equal(modelPart.forced, true)
})

test('shows inherited defaults for partial wire bundles', () => {
    const [modelPart] = bundleSummaryParts({ effort: 'high' }, helpers, {
        defaults: {
            selected_model: 'default_model',
            effort: 'medium',
        },
    })

    assert.deepEqual(modelPart, {
        text: 'Default model',
        effortSrc: 'effort:high',
        effortLabel: 'High',
        forced: false,
    })
})

test('uses only the untrusted permission layer in an untrusted project', () => {
    const permissionParts = presetSummaryParts({
        permission_mode: 'yolo',
        permission_mode_if_untrusted: null,
    }, helpers, {
        defaults: { permission_mode: 'auto' },
        untrusted: true,
    }).filter(part => part.permissionIcon)

    assert.deepEqual(permissionParts, [{
        text: 'Auto',
        permissionIcon: 'permission:auto',
        permissionColor: 'auto',
        forced: false,
    }])
})

test('uses only the trusted permission layer in a trusted project', () => {
    const permissionParts = presetSummaryParts({
        permission_mode: 'yolo',
        permission_mode_if_untrusted: 'auto',
    }, helpers, {
        defaults: { permission_mode: 'auto' },
        untrusted: false,
    }).filter(part => part.permissionIcon)

    assert.deepEqual(permissionParts, [{
        text: 'YOLO',
        permissionIcon: 'permission:yolo',
        permissionColor: 'yolo',
        forced: false,
    }])
})
