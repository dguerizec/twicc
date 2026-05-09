/**
 * JS port of Codex's ``ParsedCommand`` parser.
 *
 * Why we have this in the front: Codex's tool_use line carries the raw
 * ``input.command`` (a ``Vec<String>``), and the matching
 * ``parsed_cmd`` used to live on the ``event_msg.exec_command_end``
 * event — but the CLI no longer persists that event (TUI flipped to
 * ``persist_extended_history=false`` on 2026-04-30). This local parser
 * is therefore the **only** source of the Read / ListFiles / Search /
 * Unknown classification that drives the tool_use header label and
 * summary line.
 *
 * This is a pragmatic, lossy port — not the full Rust algorithm:
 *   - No tree-sitter validation of the bash script: we look for a
 *     short list of suspicious shell constructs and, if any are
 *     present, fall back to ``unknown``.
 *   - Argument parsing is positional + flag-strip rather than a true
 *     argv walker: we know which option values to skip for ``head``,
 *     ``tail``, ``sed``, ``rg``, ``grep`` and friends, but we won't
 *     match every esoteric short-flag combination the Rust parser
 *     handles. Cases we can't classify with confidence return
 *     ``unknown``, which is the safe behaviour.
 *
 * Output shape mirrors the Rust enum's serde representation
 * (``rename_all = "snake_case"``, tag ``type``):
 *   - ``{type: "read", cmd, name, path}``
 *   - ``{type: "list_files", cmd, path?}``
 *   - ``{type: "search", cmd, query?, path?}``
 *   - ``{type: "unknown", cmd}``
 */

// Binary classification — keep in sync with Codex's
// ``shell-command/src/parse_command.rs`` whitelists. ``awk`` and ``nl``
// only count as Read when a file argument is positional, otherwise they
// are formatting helpers in a pipeline; we resolve that via the file
// detection in the per-binary parsers below.
const READ_BINARIES = new Set([
    'cat', 'bat', 'batcat', 'less', 'more', 'head', 'tail', 'nl', 'awk',
])
const LIST_BINARIES = new Set([
    'ls', 'eza', 'exa', 'tree', 'du',
])
const SEARCH_BINARIES = new Set([
    'grep', 'egrep', 'fgrep', 'rg', 'rga', 'ag', 'ack', 'pt',
])
// Shell wrappers we know how to peel: ``bash -lc`` / ``sh -c`` / etc.
const SHELL_BINARIES = new Set(['bash', 'sh', 'dash', 'zsh', 'ash'])
// Pipeline stages that don't decide the variant of the surrounding
// command — they format / count / dedupe and let a sibling stage win.
const FORMATTING_HELPERS = new Set([
    'wc', 'tr', 'cut', 'sort', 'uniq', 'tee', 'column', 'yes', 'printf', 'xargs',
])

// Flags that take a value (the next token must be skipped when reading
// positional arguments). Per-binary; ``-n`` for ``head`` / ``tail``
// expects a count, while ``-n`` for ``rg`` / ``grep`` is a boolean.
const FLAGS_WITH_VALUE = {
    head: new Set(['-n', '-c', '--lines', '--bytes']),
    tail: new Set(['-n', '-c', '--lines', '--bytes']),
    sed: new Set(['-n', '-e', '-f']),
    rg: new Set(['-A', '-B', '-C', '-e', '-g', '-t', '-T', '--type', '--type-not', '--glob', '--max-count', '-m', '--max-depth', '--max-filesize', '--threads', '-j']),
    rga: new Set(['-A', '-B', '-C', '-e', '-g', '-t', '-T', '--type', '--type-not', '--glob', '--max-count', '-m']),
    grep: new Set(['-A', '-B', '-C', '-e', '-f', '--include', '--exclude', '--exclude-dir']),
    egrep: new Set(['-A', '-B', '-C', '-e', '-f']),
    fgrep: new Set(['-A', '-B', '-C', '-e', '-f']),
    ls: new Set(['-I', '--ignore', '--color', '--time-style']),
    eza: new Set(['-I', '--ignore-glob', '--time-style', '--colour']),
    exa: new Set(['-I', '--ignore-glob']),
    tree: new Set(['-L', '-P', '-I', '--filelimit', '--dirsfirst']),
    du: new Set(['-d', '--max-depth', '-t', '--threshold', '--block-size']),
    bat: new Set(['--theme', '--language', '-l', '--style', '--paging', '--color', '--decorations']),
    batcat: new Set(['--theme', '--language', '-l', '--style', '--paging', '--color']),
}

// rg / grep flags that flip the variant from Search to ListFiles.
const RG_LIST_ONLY_FLAGS = new Set(['--files', '-l', '--files-with-matches', '-L', '--files-without-match'])

// Bash constructs that defeat our naive split-on-pipe approach. When
// present anywhere in a ``bash -lc`` script we bail to ``unknown``.
// Order matters in the alternation: longer ``>>`` / ``2>&1`` / ``>&``
// patterns must precede the single ``>`` so they win.
const SUSPICIOUS_RE = /\$\(|<\(|>>|2>&1|2>|>&|>|`/

// Binaries that look like Read but are commonly used as pipeline
// formatting helpers when no file argument is present
// (``rg ... | head -n 50`` → drop ``head``). We classify them as Read
// only when a positional file is supplied; otherwise the surrounding
// pipeline picks the variant from another stage.
const OPTIONAL_FILE_READ_BINARIES = new Set(['head', 'tail', 'nl', 'awk'])

/**
 * Parse the command shape carried by a Codex tool_use. Two input
 * shapes are accepted:
 *   - **Array** (``Vec<String>``): the ``shell`` / ``local_shell``
 *     tools, which pass an argv. We peel ``bash -lc <script>`` shells
 *     when present, otherwise parse the argv directly.
 *   - **String**: the ``exec_command`` tool, whose ``input.cmd`` is a
 *     raw shell script (the runtime invokes it under bash by default).
 *     Treated like the inner script of a ``bash -lc`` wrapper.
 *
 * Always returns at least one element. Empty / missing input yields
 * ``[{type:"unknown", cmd:""}]``.
 */
export function parseCommand(command) {
    if (typeof command === 'string') {
        return parseShellScript(command)
    }
    if (!Array.isArray(command) || command.length === 0) {
        return [{ type: 'unknown', cmd: '' }]
    }

    // Shell wrapper: ``bash -lc "..."`` / ``sh -c "..."``.
    const script = extractShellScript(command)
    if (script !== null) {
        return parseShellScript(script)
    }

    return [parseSegment(command)]
}

/**
 * Detect the ``bash -lc <script>`` (or ``-c``) shape and return the
 * inner script string, or ``null`` when ``command`` is a direct argv.
 */
function extractShellScript(command) {
    if (command.length < 3) return null
    const head = basename(command[0])
    if (!SHELL_BINARIES.has(head)) return null
    const flag = command[1]
    if (flag !== '-c' && flag !== '-lc' && flag !== '-l' && flag !== '-i') return null
    // Common shape is ``[bash, -lc, script]``; trailing args after the
    // script (positional ``$0`` / ``$@``) are unusual for Codex but if
    // present we just take the script and ignore the rest.
    const script = command[2]
    return typeof script === 'string' ? script : null
}

/**
 * Parse a bash script string. Splits on ``|`` and ``&&`` (after a
 * suspicious-construct guard), parses each segment, drops formatting
 * helpers, and picks the surviving "main" segment. Returns multiple
 * ``ParsedCommand`` entries when several stages classify (mirroring
 * the Rust behaviour for cases like ``rg foo | head -n 50`` →
 * ``[Search]`` after dropping the formatting tail).
 */
function parseShellScript(script) {
    const trimmed = script.trim()
    if (!trimmed) return [{ type: 'unknown', cmd: '' }]
    if (SUSPICIOUS_RE.test(trimmed)) return [{ type: 'unknown', cmd: trimmed }]
    if (/(^|\s)(;|\|\||&)(\s|$)/.test(trimmed)) {
        // Sequencing or background — bail. ``&&`` is allowed and
        // handled by the splitter below.
        return [{ type: 'unknown', cmd: trimmed }]
    }

    // Split on top-level ``&&`` and ``|`` (no nesting since we ruled
    // out subshells / command substitution above).
    const segments = splitOnConnectors(trimmed)
    if (segments.length === 0) return [{ type: 'unknown', cmd: trimmed }]

    const parsedSegments = []
    for (const segment of segments) {
        const argv = shlexSplit(segment)
        if (argv.length === 0) continue
        // ``cd dir && rest`` — drop the ``cd`` segment so the surviving
        // segments still classify under their own merits.
        if (basename(argv[0]) === 'cd') continue
        parsedSegments.push(argv)
    }

    if (parsedSegments.length === 0) return [{ type: 'unknown', cmd: trimmed }]

    const classified = []
    for (const argv of parsedSegments) {
        const head = basename(argv[0])
        if (FORMATTING_HELPERS.has(head)) continue  // Drop pipe formatters.
        // ``head`` / ``tail`` / ``nl`` / ``awk`` without a file are
        // formatting helpers in this position; let the surrounding
        // pipeline supply the variant.
        if (OPTIONAL_FILE_READ_BINARIES.has(head) && !lastNonFlag(argv.slice(1), head)) {
            continue
        }
        // Same logic for ``sed`` without ``-n`` — pure stream editor,
        // no file to read.
        if (head === 'sed' && (!argv.includes('-n') || !lastNonFlag(argv.slice(1), 'sed'))) {
            continue
        }
        const parsed = parseSegment(argv)
        if (parsed.type === 'unknown') {
            // A single unknown stage poisons the whole script — match
            // the Rust behaviour where any unrecognised pipeline step
            // collapses to Unknown.
            return [{ type: 'unknown', cmd: trimmed }]
        }
        classified.push(parsed)
    }

    if (classified.length === 0) return [{ type: 'unknown', cmd: trimmed }]
    return classified
}

/**
 * Parse a single argv segment (no shell, no pipes). Returns one of the
 * ``ParsedCommand`` variants. The ``cmd`` field is reconstructed via a
 * naive ``shlexJoin`` — good enough for display, not round-trippable
 * through a real shell.
 */
function parseSegment(argv) {
    const head = basename(argv[0])
    const cmd = shlexJoin(argv)

    // ``git`` subcommand dispatch.
    if (head === 'git' && argv.length >= 2) {
        const sub = argv[1]
        if (sub === 'ls-files') {
            const path = lastNonFlag(argv.slice(2), 'git-ls-files')
            return { type: 'list_files', cmd, ...(path ? { path } : {}) }
        }
        if (sub === 'grep') {
            return parseGrepLike(argv.slice(2), 'git-grep', cmd)
        }
        return { type: 'unknown', cmd }
    }

    if (READ_BINARIES.has(head)) {
        const path = lastNonFlag(argv.slice(1), head)
        if (path) {
            // Skip ``head`` / ``tail`` / ``nl`` / ``awk`` when no file
            // argument is present — they're used as pipe stages then,
            // not as readers, and the caller will treat them as
            // formatting helpers anyway (see FORMATTING_HELPERS in the
            // shell-script path).
            const name = basename(path)
            return { type: 'read', cmd, name, path }
        }
        return { type: 'unknown', cmd }
    }

    if (head === 'sed') {
        // Only ``sed -n M,Np file`` counts as Read for us.
        const remaining = argv.slice(1)
        const hasN = remaining.includes('-n')
        if (!hasN) return { type: 'unknown', cmd }
        const path = lastNonFlag(remaining, 'sed')
        if (!path) return { type: 'unknown', cmd }
        return { type: 'read', cmd, name: basename(path), path }
    }

    if (LIST_BINARIES.has(head)) {
        const path = lastNonFlag(argv.slice(1), head)
        return { type: 'list_files', cmd, ...(path ? { path } : {}) }
    }

    if (SEARCH_BINARIES.has(head)) {
        return parseGrepLike(argv.slice(1), head, cmd)
    }

    if (head === 'find' && argv.length >= 2) {
        // ``find <path> [-name PATTERN]`` — pattern → search, no
        // pattern → list_files. We match a small subset of -name flags.
        const args = argv.slice(1)
        const path = args[0] && !args[0].startsWith('-') ? args[0] : null
        const nameIdx = args.findIndex((a) => a === '-name' || a === '-iname' || a === '-path' || a === '-ipath')
        if (nameIdx >= 0 && nameIdx + 1 < args.length) {
            return { type: 'search', cmd, query: args[nameIdx + 1], ...(path ? { path } : {}) }
        }
        return { type: 'list_files', cmd, ...(path ? { path } : {}) }
    }

    if (head === 'fd' && argv.length >= 2) {
        // ``fd PATTERN [path]`` is a search; ``fd PATH`` (single
        // positional that looks like a path) leans toward list_files.
        // Without the full Rust heuristic we treat positionals
        // conservatively: any explicit pattern → search.
        const args = stripFlags(argv.slice(1), 'fd')
        if (args.length === 0) return { type: 'list_files', cmd }
        const [maybePattern, maybePath] = args
        return { type: 'search', cmd, query: maybePattern, ...(maybePath ? { path: maybePath } : {}) }
    }

    return { type: 'unknown', cmd }
}

/**
 * Shared ``rg``/``grep``/``git grep`` argv parser. Picks Search vs
 * ListFiles based on the ``--files`` family of flags, then extracts a
 * pattern (first positional) and an optional path (second positional).
 */
function parseGrepLike(args, key, cmd) {
    const isListOnly = args.some((a) => RG_LIST_ONLY_FLAGS.has(a))
    const positionals = stripFlags(args, key)

    if (isListOnly) {
        const path = positionals.length > 0 ? positionals[positionals.length - 1] : null
        return { type: 'list_files', cmd, ...(path ? { path } : {}) }
    }

    if (positionals.length === 0) return { type: 'unknown', cmd }
    const [query, maybePath] = positionals
    const result = { type: 'search', cmd, query }
    if (maybePath) result.path = maybePath
    return result
}

/**
 * Drop flags from an argv slice. Honours per-binary ``--flag value``
 * forms via the ``FLAGS_WITH_VALUE`` table; everything else starting
 * with ``-`` is skipped, ``--`` ends flag processing.
 */
function stripFlags(args, key) {
    const valueFlags = FLAGS_WITH_VALUE[key] || new Set()
    const out = []
    for (let i = 0; i < args.length; i += 1) {
        const a = args[i]
        if (a === '--') {
            for (let j = i + 1; j < args.length; j += 1) out.push(args[j])
            break
        }
        if (a.startsWith('-')) {
            const eqIdx = a.indexOf('=')
            const flagName = eqIdx >= 0 ? a.slice(0, eqIdx) : a
            if (eqIdx === -1 && valueFlags.has(flagName)) {
                i += 1  // skip the value
            }
            continue
        }
        out.push(a)
    }
    return out
}

/**
 * Last non-flag token from ``args``, or ``null`` if all tokens are
 * flags. Honours ``FLAGS_WITH_VALUE`` like ``stripFlags``.
 */
function lastNonFlag(args, key) {
    const positionals = stripFlags(args, key)
    return positionals.length > 0 ? positionals[positionals.length - 1] : null
}

function basename(path) {
    if (!path) return ''
    const idx = path.lastIndexOf('/')
    return idx >= 0 ? path.slice(idx + 1) : path
}

// ─── Tiny shlex implementation ────────────────────────────────────────
// We avoid pulling in an npm dep for this; the parser only needs
// double-quote / single-quote handling and basic backslash escapes.
// Things we don't handle (and don't care about for parsing): variable
// expansion, ANSI-C $'...', heredocs.

function shlexSplit(input) {
    const out = []
    let buf = ''
    let inSingle = false
    let inDouble = false
    let i = 0
    const flush = () => {
        if (buf) {
            out.push(buf)
            buf = ''
        }
    }
    while (i < input.length) {
        const ch = input[i]
        if (inSingle) {
            if (ch === "'") { inSingle = false }
            else { buf += ch }
        } else if (inDouble) {
            if (ch === '"') { inDouble = false }
            else if (ch === '\\' && i + 1 < input.length) {
                const next = input[i + 1]
                if (next === '"' || next === '\\' || next === '$' || next === '`') {
                    buf += next
                    i += 1
                } else {
                    buf += ch
                }
            } else { buf += ch }
        } else if (ch === "'") { inSingle = true }
        else if (ch === '"') { inDouble = true }
        else if (ch === '\\' && i + 1 < input.length) {
            buf += input[i + 1]
            i += 1
        } else if (ch === ' ' || ch === '\t' || ch === '\n') {
            flush()
        } else {
            buf += ch
        }
        i += 1
    }
    flush()
    return out
}

function shlexJoin(argv) {
    return argv.map((a) => {
        if (/^[A-Za-z0-9_+\-./:=,@%]+$/.test(a)) return a
        if (a.includes("'")) return '"' + a.replace(/(["\\$`])/g, '\\$1') + '"'
        return "'" + a + "'"
    }).join(' ')
}

/**
 * Split a (suspicious-free) bash script on top-level ``|`` and
 * ``&&`` connectors. We're not in a quoted context detector — the
 * caller already ruled out subshells and command substitution — but
 * we do skip over single/double-quoted spans so a piped grep
 * pattern like ``grep 'foo|bar'`` doesn't get split mid-quote.
 */
function splitOnConnectors(script) {
    const parts = []
    let buf = ''
    let inSingle = false
    let inDouble = false
    let i = 0
    while (i < script.length) {
        const ch = script[i]
        if (inSingle) {
            if (ch === "'") inSingle = false
            buf += ch
        } else if (inDouble) {
            if (ch === '"') inDouble = false
            buf += ch
        } else if (ch === "'") { inSingle = true; buf += ch }
        else if (ch === '"') { inDouble = true; buf += ch }
        else if (ch === '|' && script[i + 1] !== '|') {
            parts.push(buf.trim())
            buf = ''
        } else if (ch === '&' && script[i + 1] === '&') {
            parts.push(buf.trim())
            buf = ''
            i += 1
        } else {
            buf += ch
        }
        i += 1
    }
    if (buf.trim()) parts.push(buf.trim())
    return parts.filter(Boolean)
}
