/**
 * Patch @guolao/vue-monaco-editor DiffEditor disposal order.
 *
 * The library's onUnmounted disposes text models BEFORE the DiffEditorWidget,
 * which triggers Monaco's "TextModel got disposed before DiffEditorWidget model
 * got reset" error. The fix: dispose the editor first, then the models.
 *
 * Upstream issue: the onUnmounted callback in VueMonacoDiffEditor does:
 *   models.original.dispose()  // ← fires model event
 *   models.modified.dispose()  // ← fires model event
 *   diffEditorRef.dispose()    // ← too late, DiffEditorWidget already threw
 *
 * Fixed order:
 *   diffEditorRef.dispose()    // ← unsubscribes from model events
 *   models.original.dispose()  // ← safe now
 *   models.modified.dispose()  // ← safe now
 */

import { readFileSync, writeFileSync } from 'fs'
import { resolve, dirname } from 'path'
import { fileURLToPath } from 'url'

const __dirname = dirname(fileURLToPath(import.meta.url))
const filePath = resolve(__dirname, '../node_modules/@guolao/vue-monaco-editor/lib/es/index.js')

const BUGGY = [
    '(_d = (_c = models == null ? void 0 : models.original) == null ? void 0 : _c.dispose) == null ? void 0 : _d.call(_c);',
    '      (_f = (_e = models == null ? void 0 : models.modified) == null ? void 0 : _e.dispose) == null ? void 0 : _f.call(_e);',
    '      (_h = (_g = diffEditorRef.value) == null ? void 0 : _g.dispose) == null ? void 0 : _h.call(_g);',
].join('\n')

const FIXED = [
    '(_h = (_g = diffEditorRef.value) == null ? void 0 : _g.dispose) == null ? void 0 : _h.call(_g);',
    '      (_d = (_c = models == null ? void 0 : models.original) == null ? void 0 : _c.dispose) == null ? void 0 : _d.call(_c);',
    '      (_f = (_e = models == null ? void 0 : models.modified) == null ? void 0 : _e.dispose) == null ? void 0 : _f.call(_e);',
].join('\n')

const source = readFileSync(filePath, 'utf8')

if (source.includes(FIXED)) {
    console.log('[patch] @guolao/vue-monaco-editor: DiffEditor disposal order already patched.')
    process.exit(0)
}

if (!source.includes(BUGGY)) {
    console.warn('[patch] @guolao/vue-monaco-editor: could not find expected code to patch — library may have been updated.')
    process.exit(1)
}

writeFileSync(filePath, source.replace(BUGGY, FIXED), 'utf8')
console.log('[patch] @guolao/vue-monaco-editor: fixed DiffEditor disposal order (editor before models).')
