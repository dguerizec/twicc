# Dockable Layout — Implementation Handoff (2026-06-17)

Resume point after compaction. **Step 1 is implemented and verified live in the browser.**
Read order to resume: this doc → the step-1 plan
[`2026-06-16-dockable-layout-impl-step1.md`](./2026-06-16-dockable-layout-impl-step1.md) →
the design doc [`2026-06-16-dockable-layout-design.md`](./2026-06-16-dockable-layout-design.md)
→ the pure resolver `frontend/src/utils/layoutResolver.js` (the executable spec).

Branch/worktree: `layout` (`.worktrees/layout`). Its own dev instance runs via devctl
(frontend **5174**, backend **3501**). Work is committed on branch `layout` (not pushed, not
merged) — run `git log` for the breakdown; persistence is not wired so layout state is in-memory only.

## Status: what works (verified in a real browser)

- Default (nothing docked) view is **unchanged** — the existing `wa-tab-group` behaves as before.
- Per-tab **placement arrow** (▾) on each tool tab → dropdown (Center + 6 docks). Chat has none (pinned).
- Docking a tab renders it as a real dock region **with its live panel content** (Teleport — Terminal keeps its PTY across moves; no re-mount).
- Docked tab **leaves the center strip**; non-docked tabs stay in it.
- **Gutters** (minimize → edge rail, one icon per tab), **overlays** (95% peek), **swap**, sibling **merges**, **bottom→gutter**, mobile **tabs** mode — all driven by the pure resolver.
- **Sidebar-closed gutter padding** (CSS): bottom-gutter start inset (3rem / 1.5rem) and left-gutter end inset (3.25rem) — verified to the pixel.
- **Route/focus model** (the big work of this session — see its own section) — no URL loops, click-to-focus everywhere.

## Files

Created:
- `frontend/src/utils/layoutResolver.js` — verbatim port of the prototype resolver (fn renamed `resolveLayout`; exports `DOCKS`, `DEFAULT_CONFIG`, `resolveLayout`). 20/20 headless scenario tests pass.
- `frontend/src/composables/useSessionLayout.js` — measure → resolve → render wiring + actions.
- `frontend/src/components/session/layout/`:
  - `dockMeta.js` — `CENTER`, `DOCK_LABELS`, `DOCK_ICONS`, `PLACEMENT_OPTIONS`, `edgeOfDock()`.
  - `TabPlacementMenu.vue` — arrow + `wa-dropdown` placement menu.
  - `DockGutter.vue` — edge rail, per-tab icons, swap/restore/overlay.
  - `DockRegion.vue` — one shown dock region (nav-only `wa-tab-group` + placement arrow + minimize + teleport-target body + click-to-focus).
  - `LayoutOverlay.vue` — 95% peek + backdrop + nav-only bar + teleport-target body.
  - `SessionLayout.vue` — orchestrator: positions the center slot, renders dock regions/gutters/overlay, owns the root context classes + sidebar-closed gutter CSS.

Modified:
- `frontend/src/stores/data.js` — ephemeral `localState.sessionLayout` + getter `getSessionLayout` + actions `ensureSessionLayout, setTabDock, minimizeDock, restoreDock, setLayoutActiveSide, setLayoutActiveResize, setLayoutGroupActiveTab, clearSessionLayout`.
- `frontend/src/views/SessionView.vue` — the integration (host + Teleport + gating + center filtering + route/focus model).

## Architecture as built

Three layers (mirrors `computeVisualItems`): **measure** (`useElementSize` on a getter
`() => sessionLayoutRef.value?.$el`) → **resolve** (pure `resolveLayout`) → **render** (dumb,
absolute-positioned from the resolver's px rects; no layout math in components).

- **`SessionLayout` is always present**, fills the session content area, and hosts the
  existing center `wa-tab-group` as its default slot. `dockingRendered` = `isDockingActive &&
  measured && render.mode !== 'tabs'`. When false, the center fills via CSS (`inset:0`), no dock
  regions — behaves like before. When true, center + dock regions are positioned by px.
- **Teleport host (bounded):** only the 5 tool panels (Files, Git, Terminal, Artifacts,
  Orchestration) are mounted once in a hidden host in `SessionView` and `<Teleport>`-ed to
  their target (`targetKeyForTab`): `center:<id>` (their `wa-tab-panel` slot in the center
  group), `region:<dockId>` (a shown dock body), `overlay`, or null (hidden host). **Chat
  (`SessionItemsList`) and subagent panels are NOT teleported** — untouched in the center
  group (zero risk to the core chat). Teleport preserves logical parent → provide/inject still
  works after relocation.
- **Target registry:** `SessionView` owns `layoutTargets` (reactive), `registerLayoutTarget` /
  `unregisterLayoutTarget` passed down; `DockRegion`/`LayoutOverlay` register their body el;
  the center `wa-tab-panel` target divs register via stable `centerTargetSetters`.
- **Center tab visibility:** a tool tab shows in the center strip iff `showInCenter(id)` =
  `!dockingRendered || dockOf(id)==='center'`. `centerActiveTab` = the routed tab if it's a
  center tab, else `lastCenterTab` (so focusing a docked tab doesn't blank the center).

## Route / focus model (built this session — beyond the plans)

Agreed rule: **the route is the single pointer to the focused tab.** Generalized so it works
with multiple visible docked panes.

- `regionActiveTabId(region)`: route override (if the routed tab is in this region) → per-group
  memory (`activeByGroup`, keyed by `groupKeyOf`) → first content-bearing tab.
- **Click-to-focus (user-directed: "any focus in a pane should switch the URL"):**
  - Docks: `DockRegion` has `@pointerdown.capture` on its body → emits `select(activeTabId)` →
    `switchToTab`. Capture phase so it fires even if the panel stops propagation (xterm).
  - Center: `SessionView` `onCenterPointerDown` on the center `wa-tab-group` (`@pointerdown.capture`)
    → `switchToTab(centerActiveTab)`; skips clicks on the nav (`closest('[slot="nav"]')`) so tab
    clicks navigate on their own.
- **`ownsRoute` gate — only on `onTerminalNavigate`:** while docked, several panels are visible
  and each panel emits `@navigate` to sync the URL. **TerminalPanel *reactively* re-grabs the
  route** (`applyRouteTermIndex(undefined) → replaceToTerm(0)`) whenever it's visible but not the
  route owner — two panels then fight → infinite URL loop. So Terminal's navigate is gated to the
  focused tab. **Files/Git/Artifacts emit only on user actions** (verified) → their handlers are
  ungated (a user click focuses the panel + drives the URL, thanks to click-to-focus).
- **Minimize returns focus:** `onLayoutMinimize` — minimizing the dock that holds the focused tab
  hands focus back to the center (`switchToTab(centerActiveTab)`). `SessionLayout` bubbles
  `minimize` up to `SessionView` for this (routing is `SessionView`'s job).
- **Auto-focus-on-dock:** docking a tab still auto-focuses it (its `DockRegion` `wa-tab-group`
  fires `wa-tab-show` on mount). `DockRegion.onShow` ignores the **echo** of a programmatic
  `:active` (`event.detail.name === props.activeTabId`). Note WA does **not** fire `wa-tab-show`
  for an already-active tab.

## Bugs found & fixed live (don't reintroduce)

1. **`props.layout.render` is a ref** — `SessionLayout` must read composable refs via `.value`
   (`render`, `dockingRendered`, `openOverlayEdge`, `overlayActiveTab`). Reading them raw gave
   `undefined.find` → render crash + cascade.
2. **`useElementSize(componentRef)`** → `ResizeObserver.observe` got a non-Element. Pass a getter
   `() => sessionLayoutRef.value?.$el`.
3. **Placement menu wouldn't open** — `@click.stop`/`@pointerdown.stop` on the `wa-dropdown`
   *trigger* blocked the dropdown from opening. Moved `@click.stop` onto the `wa-dropdown` itself.
4. **Infinite URL loop** when two tool tabs docked + interacting — root cause = TerminalPanel's
   reactive route re-grab (see `ownsRoute` above).

## Decisions / deviations vs the plans

- Teleport host is **bounded to the 5 tool panels** (chat + subagents stay in the center group) —
  decided for low risk; not spelled out in the plan.
- **Click-to-focus** + **minimize-returns-focus** + the **ownsRoute(Terminal-only) loop fix** are
  new this session (the plan only sketched "route = single pointer").
- Auto-focus-on-dock is still **on** and flagged "to validate" — user hasn't decided to remove it.
- Subagent tabs **center-only is settled** (won't change).
- Resize splitters: still **not wired** (per plan).
- `optional`/`hasContent` empty-optional → gutter: still **deferred** (plan note stands).
- Icons are FA guesses (`folder`, `code-branch`, `terminal`, `image`, `diagram-project`,
  `comments`; dock icons `table-cells-large`/`table-columns`) — may want refining.

## Known issues / open (not yet fixed)

- **Open file in a docked panel clears visually when it loses focus** — FilesPanel/GitPanel
  sync-from-route clears `selectedFile` when their `routeRootKey` goes `undefined` (non-owner).
  It's restored on refocus via the remembered route. Proper fix: gate the panels' *sync-from-route*
  on route-ownership (a `routeOwner` prop), so non-owner panels ignore the route entirely. **Not done.**
- **Compact mode** (`@media max-height:900px`, the header tab dropdown) is **not reconciled** with
  docks — may look rough when docked + short.
- **Persistence not wired** — `sessionLayout` is ephemeral (resets on reload / KeepAlive eviction).
  The 3-tier (global → project → session, resolved at creation, like agent settings) is designed
  (see design doc) but not built.
- **Thresholds** are starting values — tune later (the resolver IS correctly reused; an earlier
  "stuck in widescreen" report was just a too-wide window, not a bug).

## What remains (categorized todo — none trivial, lots left)

- **Finish/validate step 1:** reconcile compact mode; decide on auto-focus-on-dock; validate
  overlays/swap edge cases at small sizes; pick final icons; empty-optional → gutter.
- **Focus model polish:** the file-clears-on-blur fix (panel `routeOwner` decoupling);
  tab lifecycle (run work on "became visible/focused", not on tab activation).
- **Resize UI:** sibling splitters + dock splitters (resolver emits `splitters`; UI wiring deferred,
  watch `wa-split-panel`/`wa-reposition` traps).
- **Persistence:** persist the intention; 3-tier resolution at creation (mirror
  `projectAgentDefaults.js` → `_resolveDraftAgentSettings` → `useSessionAgentSettings`); synced +
  IndexedDB; per-device localStorage override (v2).
- **Interactions/UX:** keyboard nav; maximize/restore; reset (project/default/tabbed); drag-and-drop
  placement; named layouts/presets; animations.
- **Polish/divers:** custom tab styling (vs native WA); coexistence edge case (bottom region +
  empty-optional bottom gutter); structural-vs-resize-min naming; keep docs/AGENTS.md/CLAUDE.md in
  sync if rules change.

## Testing notes (how this was verified — reuse for next time)

- Worktree dev instance: `cd .worktrees/layout && uv run ./devctl.py start` → http://localhost:5174.
- Tests run in a **separate Chrome tab on 5174** (distinct Pinia from the user's own instance).
- Drive layout state from the page console:
  `const data = document.querySelector('#app').__vue_app__.config.globalProperties.$pinia._s.get('data')`
  then `data.setTabDock(sid,'files','left-top')`, `data.minimizeDock(...)`, `data.clearSessionLayout(sid)`.
  Force focus via `…$router.push(...)`.
- URL-loop detection: sample `location.pathname` on a `setInterval` into `window.__us`, compress
  consecutive dups, assert few transitions / no flip.
- Headless resolver tests: copy the prototype `tests.js` to scratch as `.mjs`, a `run.mjs` importing
  `runChecks` + `resolveLayout` from the repo file → 20/20.
- SFC compile check without the worktree's node_modules: `@vue/compiler-sfc` from the **main** repo's
  `node_modules` (read-only `require`, no `.vite` cache writes) + `node --check` on the `.js` files.
  Do NOT run a full `vite build` against the main repo's node_modules (corrupts its `.vite` cache and
  can break the user's running instance).
