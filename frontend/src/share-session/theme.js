// Minimal theme init for the share bundle: apply the default WA theme/brand and a
// viewer color scheme (own localStorage key, defaulting to the OS preference).
// No dependency on the SPA settings store.
const KEY = 'twicc-share-color-scheme'

export function getShareColorScheme() {
    try { return localStorage.getItem(KEY) || 'system' } catch { return 'system' }
}

export function applyShareColorScheme(mode) {
    const isDark = mode === 'dark' || (mode !== 'light' && matchMedia('(prefers-color-scheme: dark)').matches)
    document.documentElement.classList.toggle('wa-dark', isDark)
    document.documentElement.dataset.colorScheme = isDark ? 'dark' : 'light'
    try { localStorage.setItem(KEY, mode) } catch { /* ignore */ }
}

export function initShareTheme() {
    document.documentElement.classList.add('wa-theme-default', 'wa-palette-default', 'wa-brand-cyan')
    document.documentElement.dataset.theme = 'default'
    applyShareColorScheme(getShareColorScheme())
    matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
        if (getShareColorScheme() === 'system') applyShareColorScheme('system')
    })
}
