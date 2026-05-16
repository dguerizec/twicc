// frontend/src/utils/twiccLaunch.js
//
// Shell prefix that re-invokes the same TwiCC distribution
// (e.g. ``uvx twicc`` or ``<sys.executable> -m twicc``). Computed
// server-side at startup and shipped to the frontend in the bootstrap
// payload — never mutated at runtime. Extracted out of the settings
// store to keep ``getAuthLoginCommand`` synchronous *and* avoid the
// helpers/settings circular import that breaks Vite HMR (see CLAUDE.md
// "Avoiding Circular Imports").

let _twiccLaunchPrefix = ''

/**
 * Set the launch prefix. Called once from main.js right after the
 * bootstrap payload is fetched, before any store / provider helper is
 * instantiated. Subsequent calls just overwrite (no listeners).
 */
export function setTwiccLaunchPrefix(value) {
    _twiccLaunchPrefix = value || ''
}

/**
 * Synchronous read of the launch prefix. Returns ``''`` if
 * ``setTwiccLaunchPrefix`` was never called (shouldn't happen in
 * production but keeps tests / partial setups from crashing).
 */
export function getTwiccLaunchPrefix() {
    return _twiccLaunchPrefix
}
