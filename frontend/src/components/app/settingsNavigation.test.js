import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const source = readFileSync(new URL('./SettingsPopover.vue', import.meta.url), 'utf8')

test('orders settings from general controls through advanced peer settings', () => {
    const expected = [
        "id: 'general'",
        "id: 'notifications'",
        "id: 'providers'",
        '...providerSections.value.filter(s => s.enabled)',
        "id: 'sessions'",
        "id: 'layouts'",
        "id: 'title'",
        "id: 'editor'",
        "id: 'terminal'",
        "id: 'sharing'",
        "id: 'usage'",
        "id: 'peers'",
    ]

    let previous = -1
    for (const marker of expected) {
        const index = source.indexOf(marker)
        assert.ok(index > previous, `${marker} must follow the preceding navigation entry`)
        previous = index
    }
})

test('shows a semantic divider whenever an auxiliary entry is visible', () => {
    assert.match(source,
        /const hasUtilitySections = computed\(\(\) => !store\.isTouchDevice \|\| hasTips\.value \|\| hasHelp\.value\)/)
    assert.match(source,
        /<wa-divider v-if="hasUtilitySections" class="settings-nav-divider"><\/wa-divider>/)
    assert.doesNotMatch(source, /shortcuts-nav-divider/)
})
