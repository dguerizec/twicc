import { defineStore } from 'pinia'
import { markRaw } from 'vue'

/**
 * App-level pool of live terminal instances.
 *
 * The UI shows only one top-level context at a time (a session OR a project OR a
 * workspace OR the global view), so a shared terminal never needs to render in two
 * places at once — there is exactly ONE live instance per terminal, relocated
 * (via <Teleport>) to whichever panel is currently active. This lets a terminal
 * be "attached" from a parent scope into a child panel without any backend
 * multiplexer, for tmux AND non-tmux alike.
 *
 * A terminal is identified by its HOME identity: key = `${contextKey}#${index}`.
 *
 * Keep-alive rule: a descriptor (hence its instance) lives while it has a TARGET
 * (some active panel renders it) OR appears in some panel's ATTACHMENTS. When
 * neither holds it is dropped → the instance unmounts → its WS closes (tmux: the
 * server session persists; non-tmux: the shell ends). So an un-attached terminal
 * behaves exactly as before (torn down when you leave its view); an attached one
 * stays alive and moves between views.
 */
export const useTerminalPoolStore = defineStore('terminalPool', {
    state: () => ({
        // key → descriptor { contextKey, index, projectId, sessionId, cwd, startMode, label }
        descriptors: {},
        // key → HTMLElement teleport target (absent → rendered in the hidden holder)
        targets: {},
        // key → boolean: is this the active tab of the panel currently displaying it
        activeFlags: {},
        // key → terminal API (toolbar/extra-keys), registered by each instance
        apis: {},
        // panelContextKey → [key, …]: terminals each panel has attached (persist
        // across in-app navigation; lost on page reload)
        attachments: {},
    }),

    getters: {
        keyFor: () => (contextKey, index) => `${contextKey}#${index}`,
        getDescriptor: (state) => (key) => state.descriptors[key],
        getTarget: (state) => (key) => state.targets[key],
        getApi: (state) => (key) => state.apis[key] || null,
        attachmentsFor: (state) => (panelCtx) => state.attachments[panelCtx] || [],
        // Keys whose home context is `ctx` and that currently have a live instance
        // (used to discover attachable non-tmux terminals: only connected ones).
        liveKeysForContext: (state) => (ctx) =>
            Object.keys(state.descriptors).filter((k) => state.descriptors[k].contextKey === ctx),
        isAttachedSomewhere: (state) => (key) =>
            Object.values(state.attachments).some((list) => list.includes(key)),
    },

    actions: {
        /** A panel publishes one of its tab slots (own or attached) into the pool. */
        setSlot(key, descriptor, el, isActiveTab) {
            if (!this.descriptors[key]) {
                this.descriptors[key] = { ...descriptor }
            } else {
                // Keep mutable bits (label, cwd, startMode) fresh from the owner.
                Object.assign(this.descriptors[key], descriptor)
            }
            if (el) {
                this.targets[key] = markRaw(el)
            } else {
                delete this.targets[key]
            }
            this.activeFlags[key] = !!isActiveTab
        },

        /**
         * Point an existing descriptor's slot without touching the descriptor
         * itself (used for ATTACHED tabs, whose descriptor is owned by the home
         * panel — the borrowing panel only relocates it and flags active-ness).
         */
        setSlotTarget(key, el, isActiveTab) {
            if (!this.descriptors[key]) return
            if (el) this.targets[key] = markRaw(el)
            else delete this.targets[key]
            this.activeFlags[key] = !!isActiveTab
        },

        /** A panel releases a slot (inactive / unmount / tab removed). */
        clearSlot(key, el) {
            // Release the slot only if this caller is still the one displaying the
            // key. A shared key (a pinned/forced terminal teleported between panels)
            // may have been reclaimed by another panel that now owns the target; the
            // panel we're leaving must NOT clobber it — otherwise the reclaiming
            // panel is left with a parked, target-less (blank) instance. `el`
            // omitted → clear unconditionally (own, non-shared tabs).
            if (el != null && this.targets[key] !== el) return
            delete this.targets[key]
            delete this.activeFlags[key]
            this._gc(key)
        },

        /** Attach an ancestor terminal into a panel (keeps it alive + lets it move). */
        attach(panelCtx, key, descriptor) {
            const list = this.attachments[panelCtx] || (this.attachments[panelCtx] = [])
            if (!list.includes(key)) list.push(key)
            if (!this.descriptors[key]) {
                this.descriptors[key] = { ...descriptor }
            }
        },

        /** Detach an attached terminal from a panel (never kills it). */
        detach(panelCtx, key) {
            const list = this.attachments[panelCtx]
            if (list) {
                const i = list.indexOf(key)
                if (i !== -1) list.splice(i, 1)
                if (list.length === 0) delete this.attachments[panelCtx]
            }
            this._gc(key)
        },

        registerApi(key, api) {
            this.apis[key] = api
        },
        unregisterApi(key) {
            delete this.apis[key]
        },

        /**
         * The instance for `key` reported its PTY exited (Ctrl+D, `exit`, shell
         * crash, tmux session killed). Remove it from every panel's attachments,
         * then drop it unless it's a MAIN terminal still being displayed — those
         * stay mounted so the panel can show its reconnect overlay.
         */
        onExit(key) {
            for (const ctx of Object.keys(this.attachments)) {
                const list = this.attachments[ctx]
                const i = list.indexOf(key)
                if (i !== -1) list.splice(i, 1)
                if (list.length === 0) delete this.attachments[ctx]
            }
            const d = this.descriptors[key]
            if (!d) return
            // The shell is dead: drop the instance. A MAIN terminal that is
            // currently displayed is kept so the panel can show its reconnect
            // overlay; everything else (secondaries, and parked/persistent mains)
            // is removed regardless of `persist` — `persist` only survives
            // navigation, never a real exit.
            if (d.index === 0 && this.targets[key]) return
            delete this.targets[key]
            delete this.activeFlags[key]
            delete this.descriptors[key]
        },

        /** Drop a descriptor (→ unmount its instance) once nothing references it. */
        _gc(key) {
            if (this.targets[key]) return
            if (this.isAttachedSomewhere(key)) return
            // `persist` terminals (non-tmux ancestor-scope ones) have no
            // server-side session to re-attach to, so they must stay alive across
            // navigation to remain connected and attachable from a child panel.
            // They are dropped only on PTY exit (onExit) or explicit disconnect.
            if (this.descriptors[key]?.persist) return
            delete this.descriptors[key]
            delete this.activeFlags[key]
            // apis[key] is removed by the instance's own unmount hook
        },
    },
})
