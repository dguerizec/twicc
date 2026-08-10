// frontend/src/stores/codeComments.test.js
//
// Run with:  node --test src/stores/codeComments.test.js   (from the frontend dir)
//
// Covers formatComment's markdown output only — the `:::` container it emits
// and the way each source's selection is quoted. The container is parsed back
// with markdown-it (the same rule the renderer installs) so the assertions are
// about what the user actually sees, not about string shapes.

import test from 'node:test'
import assert from 'node:assert/strict'
import MarkdownIt from 'markdown-it'

import { formatComment, formatAllComments } from './codeComments.js'
import { installColonBlocks } from '../utils/markdownColonBlocks.js'

const md = MarkdownIt({ html: false, breaks: true })
installColonBlocks(md)

/** Root-level token types, as splitMarkdownBlocks would slice them. */
function rootTypes(source) {
    return md.parse(source, {}).filter(t => t.level === 0 && t.map).map(t => t.type)
}

/** Child block types directly inside the single container of `source`. */
function containerChildren(source) {
    const tokens = md.parse(source, {})
    const start = tokens.findIndex(t => t.type === 'container_open')
    const end = tokens.findIndex(t => t.type === 'container_close')
    // Opening tokens (nesting 1) and self-contained blocks like `fence` (nesting 0).
    return tokens.slice(start + 1, end).filter(t => t.level === 1 && t.nesting >= 0).map(t => t.type)
}

test('code mode fences the selection, with the language of the file', () => {
    const out = formatComment(
        { lineText: 'def f():\n    return 1', content: 'why?', filePath: 'src/a.py', lineFrom: 10, lineTo: 11 },
        { isSelectedText: true, quoteMode: 'code' },
    )
    assert.match(out, /^\n::: comment on selected text from `src\/a\.py` lines 10-11\n```python\n/)
    assert.deepEqual(containerChildren(out), ['fence', 'paragraph_open'])
})

test('quote mode blockquotes the selection', () => {
    const out = formatComment(
        { lineText: 'watcher reads from last_offset', content: 'and if truncated?' },
        { isSelectedText: true, quoteMode: 'quote' },
    )
    assert.match(out, /\n> watcher reads from last_offset\n/)
    assert.deepEqual(containerChildren(out), ['blockquote_open', 'paragraph_open'])
})

test('the comment stays outside the quote (no lazy continuation)', () => {
    const out = formatComment(
        { lineText: 'line one\nline two', content: 'my comment' },
        { isSelectedText: true, quoteMode: 'quote' },
    )
    const html = md.render(out)
    assert.doesNotMatch(html, /my comment[\s\S]*<\/blockquote>/)
    assert.match(html, /<\/blockquote>[\s\S]*<p>my comment<\/p>/)
})

test('an explicit language wins when there is no file path', () => {
    const out = formatComment(
        { lineText: 'x = 1', content: 'ok' },
        { isSelectedText: true, quoteMode: 'code', lang: 'python' },
    )
    assert.match(out, /```python\n/)
})

test('a source label lands in the container label', () => {
    const out = formatComment(
        { lineText: 'ENOTEMPTY', content: 'note' },
        { isSelectedText: true, sourceLabel: 'from terminal' },
    )
    assert.match(out, /^\n::: comment on selected text from terminal\n/)
})

test('an empty comment yields an excerpt container', () => {
    const out = formatComment({ lineText: 'plain', content: '' }, { isSelectedText: true, quoteMode: 'quote' })
    assert.match(out, /^\n::: excerpt of selected text\n/)
    assert.deepEqual(containerChildren(out), ['blockquote_open'])
})

test('a per-line file comment uses the same container', () => {
    const out = formatComment({ lineText: 'const a = 1', content: 'rename', filePath: 'src/a.js', lineNumber: 42 })
    assert.match(out, /^\n::: comment on `src\/a\.js` line 42\n```javascript\n/)
    assert.deepEqual(containerChildren(out), ['fence', 'paragraph_open'])
})

test('a colon run in the selection lengthens the marker', () => {
    const out = formatComment(
        { lineText: 'a\n:::\nb', content: 'note' },
        { isSelectedText: true, quoteMode: 'code' },
    )
    assert.match(out, /^\n:::: comment on/)
    // Still one container, and the inner `:::` stayed inside the fence.
    assert.deepEqual(containerChildren(out), ['fence', 'paragraph_open'])
})

test('a colon run in the comment lengthens the marker too', () => {
    const out = formatComment(
        { lineText: 'plain', content: 'see:\n::::\nx' },
        { isSelectedText: true, quoteMode: 'quote' },
    )
    assert.match(out, /^\n::::: comment on/)
    assert.deepEqual(rootTypes(out.trimStart()), ['container_open'])
})

test('several blocks in one draft stay separate, with the typed text between them', () => {
    // Three "Add to message" clicks, mixed modes, one with a colon clash in the
    // selection, and text the user typed in between.
    const draft = [
        'first:',
        formatComment(
            { lineText: 'x = 1', content: 'why?', filePath: 'a.py', lineFrom: 1, lineTo: 1 },
            { isSelectedText: true, quoteMode: 'code' },
        ),
        '\nthen:',
        formatComment({ lineText: 'prose', content: 'hm' }, { isSelectedText: true, quoteMode: 'quote' }),
        formatComment({ lineText: 'a\n:::\nb', content: 'note' }, { isSelectedText: true }),
        '\nthanks.',
    ].join('')

    assert.deepEqual(rootTypes(draft), [
        'paragraph_open',
        'container_open',
        'paragraph_open',
        'container_open',
        'container_open',
        'paragraph_open',
    ])
    // The `:::` inside the third selection stayed inside its fence.
    const html = md.render(draft)
    assert.equal(html.match(/md-container md-container-comment/g).length, 3)
})

test('formatAllComments yields one container per comment', () => {
    const all = formatAllComments([
        { filePath: 'b.js', lineNumber: 3, lineText: 'let x', content: 'const?' },
        { filePath: 'a.py', lineNumber: 9, lineText: 'pass', content: 'todo' },
    ])
    assert.deepEqual(rootTypes(all), ['container_open', 'container_open'])
})

test('inserted after existing text, it still opens its own block', () => {
    const out = formatComment({ lineText: 'a', content: 'b' }, { isSelectedText: true, quoteMode: 'quote' })
    assert.deepEqual(rootTypes(`already typed${out}\n`), ['paragraph_open', 'container_open'])
})
