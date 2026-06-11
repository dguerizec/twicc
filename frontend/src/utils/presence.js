// frontend/src/utils/presence.js
// Human-presence heartbeat: tells the backend whether a human is actively at
// this TwiCC client, so it can hold "away-only" external (Apprise) notifications
// while the user is here and send them only once the user is away.
//
// A page can only observe its own tab, so presence is approximated per device
// class and the backend applies a per-class grace window (see twicc/presence.py):
//   - desktop: report genuine input activity (mouse/keyboard/scroll). Input
//     events only reach the focused tab, so they double as a "TwiCC is
//     foregrounded and the user is interacting" signal; when the user switches
//     tab/app or walks away (even leaving the tab focused), the pings stop and
//     the backend's grace eventually expires.
//   - mobile/touch: report a foreground heartbeat on a timer (being in the
//     foreground counts as present, even without touching) and stop when the
//     tab is backgrounded — the OS suspends it anyway.
//
// All pings are fire-and-forget; the send function no-ops when the WS is down,
// so pings simply resume after a reconnect.

// Cadence well under both backend grace windows (mobile = 30s, desktop = 5min),
// so a single dropped ping never prematurely expires presence.
const PING_INTERVAL_MS = 10000

// Touch-primary devices (phones, tablets) are classified as "mobile": their OS
// suspends background tabs and shows no desktop notification banner, so the
// backend treats backgrounding as "the user left" (short grace). A desktop with
// a touchscreen still exposes a fine pointer and stays "desktop".
function detectDeviceClass() {
    try {
        return window.matchMedia('(pointer: coarse)').matches ? 'mobile' : 'desktop'
    } catch {
        return 'desktop'
    }
}

let installed = false

/**
 * Install the presence heartbeat once at module load. Idempotent.
 * @param {(data: object) => boolean} send - WS send function (no-ops when disconnected)
 */
export function installPresenceHeartbeat(send) {
    if (installed) return
    installed = true

    const deviceClass = detectDeviceClass()
    const ping = () => send({ type: 'presence', device_class: deviceClass })

    if (deviceClass === 'mobile') {
        // Foreground heartbeat: ping on a timer while the tab is visible. When
        // hidden we skip (and the OS throttles/suspends background timers
        // anyway), so presence expires shortly after backgrounding.
        const tick = () => {
            if (document.visibilityState === 'visible') ping()
        }
        tick() // immediate, so returning to the app re-establishes presence at once
        setInterval(tick, PING_INTERVAL_MS)
        // Ping immediately on foreground so a quick flick back to the app
        // cancels a deferred push without waiting for the next interval.
        document.addEventListener('visibilitychange', () => {
            if (document.visibilityState === 'visible') ping()
        })
    } else {
        // Desktop: input-driven. Input events only fire on the focused tab, so
        // they signal "TwiCC foregrounded + user interacting". Throttle so a
        // burst of pointer moves sends at most one ping per interval.
        let lastSent = 0
        const onInput = () => {
            const now = Date.now()
            if (now - lastSent >= PING_INTERVAL_MS) {
                lastSent = now
                ping()
            }
        }
        for (const ev of ['pointerdown', 'pointermove', 'keydown', 'scroll', 'wheel', 'touchstart']) {
            window.addEventListener(ev, onInput, { passive: true })
        }
    }
}
