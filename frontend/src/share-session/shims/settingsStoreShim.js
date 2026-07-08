// Viewer-local settings for the share bundle. Reactive display mode + color
// scheme (viewer toggles), timestamps seeded from the share options. Cost is
// never shown in a share (areCostsShown stays false — the reused transcript
// components read it).
import { defineStore } from 'pinia'
import { applyShareColorScheme, getShareColorScheme } from '../theme'

export const useSettingsStore = defineStore('shareSettings', {
    state: () => ({
        displayMode: 'normal',            // bounded to <= max_display_mode by the app
        _colorScheme: getShareColorScheme(),
        areMessageTimestampsShown: true,
        areCostsShown: false,
    }),
    getters: {
        getDisplayMode: (s) => s.displayMode,
        _effectiveColorScheme: (s) => (s._colorScheme === 'dark'
            || (s._colorScheme !== 'light' && matchMedia('(prefers-color-scheme: dark)').matches)) ? 'dark' : 'light',
        // Names the reused CodeMirror path reads (CodeEditor/DiffEditor/useCodeMirror):
        // without these the code/diff blocks render light regardless of the viewer's
        // dark toggle and the font size is undefined.
        getEffectiveColorScheme() { return this._effectiveColorScheme },
        getWaTheme() { return this.waTheme },
        getFontSize: () => 13,
        showDiffs: () => false,
        isMac: () => /Mac/i.test(navigator.platform || ''),
        isTouchDevice: () => matchMedia('(pointer: coarse)').matches,
        isToolDiffSideBySide: () => false,
        isToolDiffWordWrap: () => true,
        isClaudeHybridEnabled: () => false,
        isTitleGenerationEnabled: () => false,
        getTitleSystemPrompt: () => '',
        waTheme: () => 'default',
        waBrand: () => 'cyan',
    },
    actions: {
        setDisplayMode(mode) { this.displayMode = mode; document.body.dataset.displayMode = mode },
        setColorScheme(mode) { this._colorScheme = mode; applyShareColorScheme(mode) },
        setToolDiffSideBySide() {}, setToolDiffWordWrap() {},
    },
})
