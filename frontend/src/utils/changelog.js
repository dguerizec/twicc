// Changelog parser and fetcher
// Fetches CHANGELOG.md from GitHub, parses it into structured data for the ChangelogDialog.

const GITHUB_RAW_BASE = 'https://raw.githubusercontent.com/twidi/twicc/refs/heads/main/'
const CHANGELOG_URL = GITHUB_RAW_BASE + 'CHANGELOG.md'

/**
 * Fetch and parse the changelog.
 * In dev mode, fetches from the local backend endpoint; otherwise from GitHub.
 * @param {boolean} devMode - Whether the backend is running in dev mode
 * @returns {Promise<Array<{version: string, date: string|null, entries: Array}>>}
 */
export async function fetchChangelog(devMode = false) {
    const url = devMode ? '/api/changelog/' : CHANGELOG_URL
    const resp = await fetch(url)
    if (!resp.ok) throw new Error(`Failed to fetch changelog: ${resp.status}`)
    const versions = parseChangelog(await resp.text())
    // In non-dev mode, only keep real releases (version starts with a digit)
    return devMode ? versions : versions.filter(v => /^\d/.test(v.version))
}

/**
 * Parse a Keep-a-Changelog formatted markdown string into structured data.
 *
 * @param {string} markdown - Raw CHANGELOG.md content
 * @returns {Array<{version: string, date: string|null, entries: Array<{category: string, text: string, images: Array<{alt: string, path: string}>}>}>}
 */
export function parseChangelog(markdown) {
    const versions = []

    // Find all version headers: ## [Unreleased] or ## [1.2.3] - 2026-03-20
    const versionRegex = /^## \[(.+?)\](?:\s*-\s*(.+))?$/gm
    const versionHeaders = []
    let match

    while ((match = versionRegex.exec(markdown)) !== null) {
        versionHeaders.push({
            version: match[1],
            date: match[2]?.trim() || null,
            contentStart: match.index + match[0].length,
        })
    }

    for (let i = 0; i < versionHeaders.length; i++) {
        const header = versionHeaders[i]
        const contentEnd = i + 1 < versionHeaders.length
            ? markdown.lastIndexOf('\n', versionHeaders[i + 1].contentStart - versionHeaders[i + 1].version.length - 10)
            : markdown.length
        const content = markdown.slice(header.contentStart, contentEnd)

        const entries = parseVersionContent(content)
        if (entries.length > 0) {
            versions.push({
                version: header.version,
                date: header.date,
                entries,
            })
        }
    }

    return versions
}

/**
 * Parse the content within a single version section.
 */
function parseVersionContent(content) {
    const entries = []

    // Find category headers: ### Added, ### Changed, ### Fixed
    const categoryRegex = /^### (\w+)$/gm
    const categories = []
    let match

    while ((match = categoryRegex.exec(content)) !== null) {
        categories.push({
            name: match[1].toLowerCase(),
            contentStart: match.index + match[0].length,
        })
    }

    for (let i = 0; i < categories.length; i++) {
        const cat = categories[i]
        const contentEnd = i + 1 < categories.length ? categories[i + 1].contentStart - categories[i + 1].name.length - 5 : content.length
        const section = content.slice(cat.contentStart, contentEnd)

        // Parse top-level entries (lines starting with "- ")
        const lines = section.split('\n')
        let currentEntry = null

        for (const line of lines) {
            if (line.startsWith('- ')) {
                if (currentEntry) entries.push(currentEntry)
                currentEntry = {
                    category: cat.name,
                    text: line.slice(2),
                    images: [],
                }
            } else if (currentEntry && /^\s+- !\[/.test(line)) {
                // Image sub-item:   - ![Alt text](path/to/image.webp)
                const imgMatch = line.match(/^\s+- !\[([^\]]*)\]\(([^)]+)\)/)
                if (imgMatch) {
                    currentEntry.images.push({
                        alt: imgMatch[1],
                        path: imgMatch[2],
                    })
                }
            }
        }
        if (currentEntry) entries.push(currentEntry)
    }

    return entries
}

// Thin alias kept for the existing ChangelogDialog import. The canonical
// helper now lives in ./publicAsset.js and is reused by other features
// (e.g. tips) that need to load assets from frontend/public/. New callers
// should import `resolvePublicAssetUrl` directly.
export { resolvePublicAssetUrl as resolveImageLocalUrl } from './publicAsset.js'

/**
 * Resolve an image path to the GitHub raw URL (fallback).
 *
 * @param {string} path - Raw path from CHANGELOG
 * @returns {string} GitHub raw content URL
 */
export function resolveImageGitHubUrl(path) {
    return GITHUB_RAW_BASE + path
}
