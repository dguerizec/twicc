# Dockable Layout — Implementation Handoff (2026-06-17)

Resume point after compaction. **Step 1 is implemented and verified live in the browser.**
Read order to resume: this doc → the step-1 plan
[`2026-06-16-dockable-layout-impl-step1.md`](./2026-06-16-dockable-layout-impl-step1.md) →
the design doc [`2026-06-16-dockable-layout-design.md`](./2026-06-16-dockable-layout-design.md)
→ the pure resolver `frontend/src/utils/layoutResolver.js` (the executable spec).

Branch/worktree: `layout` (`.worktrees/layout`). Its own dev instance runs via devctl
(frontend **5174**, backend **3501**). Work is 6 commits on branch `layout` (not pushed, not
merged), now **rebased on top of `main`** — run `git log main..layout` for the breakdown.
Persistence is not wired, so layout state is in-memory only.

**Verification status:** step-1 docking + the route/focus model were verified live in the browser.
The later work this session — container-relative responsiveness and the compact-mode tab decoupling
— is implemented and **compile-verified (SFC + `node --check`) but not yet verified live**.

## Status: what works

- Default (nothing docked) view is **unchanged** — the existing `wa-tab-group` behaves as before.
- Per-tab **placement arrow** (▾) on each tool tab → dropdown (Center + 6 docks). Chat has none (pinned).
- Docking a tab renders it as a real dock region **with its live panel content** (Teleport — Terminal keeps its PTY across moves; no re-mount).
- Docked tab **leaves the center strip**; non-docked tabs stay in it.
- **Gutters** (minimize → edge rail, one icon per tab), **overlays** (95% peek), **swap**, sibling **merges**, **bottom→gutter**, mobile **tabs** mode — all driven by the pure resolver.
- **Sidebar-closed gutter padding** (CSS): bottom-gutter start inset (3rem / 1.5rem) and left-gutter end inset (3.25rem) — verified to the pixel.
- **Route/focus model** (the big work of this session — see its own section) — no URL loops, click-to-focus everywhere.
- **Container-relative responsiveness** (this session — see its section, not yet live-verified): the
  Files/Git/Artifacts `mobile-layout` and the chat/composer margins react to the panel's own width
  (dock / center zone), not the viewport.
- **Compact mode decoupled from the tabs** (this session — see its section, not yet live-verified):
  the tab bar stays inline in the content at all heights; compact only collapses the header's chrome.

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
- `frontend/src/views/SessionView.vue` — the integration (host + Teleport + gating + center filtering + route/focus model). Compact-tabs machinery removed (this session).

Modified for responsiveness + compact decoupling (this session):
- `frontend/src/composables/useContainerBreakpoint.js` — observes the component's own root when no
  selector is given.
- `FilesPanel.vue` / `GitPanel.vue` / `TerminalPanel.vue` — `routeOwner` prop (sync-from-route gate);
  Files/Git also drop the `.main-content` selector (observe self).
- `SessionItem.vue` / `MessageInput.vue` / `MessageSnippetsBar.vue` — chat/composer `@media`→`@container`.
- `MessageInput.vue` / `CollapsedBar.vue` — Family B sidebar-toggle clearance modulated by dock classes.
- `SessionLayout.vue` — adds `has-bottom-region` / `has-bottom-gutter` root classes (+ DockRegion/
  SessionLayout `pane-focus` plumbing for click-to-focus).
- `SessionHeader.vue` / `ProjectDetailPanel.vue` / `ProjectDetailHeader.vue` — compact-tab machinery
  removed (tabs stay inline).

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
- **`route-owner` prop — sync-from-route runs only for the focused panel.** Docking breaks the
  pre-docking equivalence *visible ⟺ focused ⟺ route owner*: a docked panel is `active` (rendered)
  without owning the URL. SessionView blanks a non-owner's route props (its params belong to
  whoever owns the URL), and each panel's sync-from-route watchers used to read the blanks as
  "nothing selected" and clear their open file / commit / terminal tab at blur. Fix: pass
  `:route-owner="ownsRoute(tab)"`; FilesPanel/GitPanel/TerminalPanel gate every sync-from-route
  watcher on it (`if (!props.routeOwner) return`, prop in the dep array). Defaults `true` →
  non-docked behaviour unchanged. This also kills TerminalPanel's reactive re-grab at the source
  (`applyRouteTermIndex` no longer runs when not owner), so the old infinite-URL-loop is gone
  **without** the `onTerminalNavigate` gate (removed — it blocked legit unfocused term-tab clicks).
- **Click-to-focus (user-directed: "any focus in a pane should switch the URL") — deferred &
  action-superseded.** A focus claim must NOT race the gesture's real action (open a file, switch
  a terminal tab). Two-part fix: (1) the claim is requested on the **`click`** (the *end* of the
  gesture — `pointerdown` fires ~16ms in, before the click, so a `pointerdown`+rAF claim still
  pre-empted the action — the bug that made the first attempt no-op); (2) it's resolved on the next
  rAF and cancelled by any navigation the gesture produced (sync for file/commit select, a watcher
  microtask for terminal-tab). `DockRegion` body `@click.capture` → emits `pane-focus` →
  SessionLayout `focus-pane` → SessionView `requestPaneFocus` (rAF). The center mirrors it
  (`onCenterClick`, skipping nav clicks). Every navigate handler + `onTabShow` + `onLayoutSelectTab`
  call `cancelPaneFocus`. `switchToTab` no-ops if the tab is already focused, so an action that
  already focused the pane makes the fallback claim a no-op. Result: one navigation per gesture, no
  transient, no revert.
- **Minimize returns focus:** `onLayoutMinimize` — minimizing the dock that holds the focused tab
  hands focus back to the center (`switchToTab(centerActiveTab)`). `SessionLayout` bubbles
  `minimize` up to `SessionView` for this (routing is `SessionView`'s job).
- **Auto-focus-on-dock:** docking a tab still auto-focuses it (its `DockRegion` `wa-tab-group`
  fires `wa-tab-show` on mount). `DockRegion.onShow` ignores the **echo** of a programmatic
  `:active` (`event.detail.name === props.activeTabId`). Note WA does **not** fire `wa-tab-show`
  for an already-active tab.

## Container-relative responsiveness (this session — not yet live-verified)

In the dockable layout a panel / the chat can be far narrower than the window, so width-responsive
behaviour keyed on the viewport is wrong. Three changes, all "react to the actual rendered width":

- **`useContainerBreakpoint` observes self.** With no `containerSelector` it observes the component's
  own root element (a real container query) instead of `.main-content`. `FilesPanel` / `GitPanel`
  (and Artifacts, same component) drop the selector → their split-panel↔stacked `mobile-layout`
  follows the dock region / center slot width (and their own width in ProjectDetailPanel). Breakpoint
  kept at **800px** (placeholder).
- **Chat/composer margins → container queries.** The chat's card width/spacing were already
  `@container session-items-list`; the leftover `@media (width<640px)` rules (markdown-toolbar offset
  in `SessionItem`, attachment gap in `MessageInput`, snippets single-line scroll in
  `MessageSnippetsBar`) became `@container` queries on `session-items-list` / `message-input`.
  **40rem = the old 640px** (placeholder).
- **Composer sidebar-toggle clearance (Family B), modulated by the dock layout.** `SessionLayout`
  exposes `has-bottom-region` / `has-bottom-gutter` next to `has-left-col` / `has-left-gutter`. The
  toolbar (`.message-input-toolbar`) and collapsed bar (`.collapsed-bar--sidebar-clearance`) left
  padding — only under `body.sidebar-closed` + `@media (width>=640px)` — is now, by priority:
  **0** if a bottom dock/gutter OR a left dock column is present; **reduced** (toolbar 1.5rem,
  collapsed 2rem — placeholders, = full − 2rem) if only a left gutter; else the **full** value
  (3.5rem / 4rem). Mobile + sidebar-open unchanged. (Geometry confirmed with the user: a left edge is
  always a full-height column XOR a full-height gutter, so the coarse `has-left-*` classes suffice.)

## Compact mode — tabs decoupled from it (this session — not yet live-verified)

Compact mode (`@media max-height:900px`; fires on most laptops once browser chrome is subtracted —
StatCounter: only native-1080p-at-100% stays above) used to **hide the inline tab nav and relocate
the tabs into the header** (a duplicated scrollable row in the expanded overlay + a tab dropdown in
the collapsed header). That hid the per-tab placement arrows (you couldn't dock a center tab on a
short screen) and duplicated the tab UI.

Now the **tabs always stay inline in the content** (same bar, size unchanged — shrinking deferred);
compact mode only collapses the header's **own** chrome (action buttons, revealed by the chevron),
and the tab dropdown is removed **with nothing in its place** (user's explicit choice — they'll judge
the look later). Net deletion across **both header families** — sessions (`SessionView`/`SessionHeader`)
and project/workspace (`ProjectDetailPanel`/`ProjectDetailHeader`): removed `compactTabs`,
`switchToTabAndCollapse`, the custom scroll machinery + observers, both `#compact-extra` slots, both
compact-tab dropdowns (props/handlers/CSS), and the `@media` rules that hid the inline navs. Compact
tweaks unrelated to the tabs (action-button hiding, divider, padding, stats nav list) are kept.

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

- **Persistence not wired** — `sessionLayout` is ephemeral (resets on reload / KeepAlive eviction).
  The 3-tier (global → project → session, resolved at creation, like agent settings) is designed
  (see design doc) but not built.
- **Layout thresholds/values are placeholders** — tune later: the resolver thresholds, the 800px
  container breakpoint (`useContainerBreakpoint`), the 40rem chat/composer `@container` thresholds,
  and the Family B reduced clearances (toolbar 1.5rem / collapsed 2rem, plus the collapsed-bar
  vertical `padding-block` choice). (The resolver IS correctly reused; an earlier "stuck in
  widescreen" report was just a too-wide window, not a bug.)
- **This session's later work is not yet live-verified** (container-relative responsiveness,
  compact-tabs decoupling) — compile-verified only.

## What remains (categorized todo — none trivial, lots left)

- **Finish/validate step 1:** live-verify this session's later work (responsiveness, compact decoupling);
  decide on auto-focus-on-dock; validate overlays/swap edge cases at small sizes; pick final icons;
  empty-optional → gutter. (Compact mode is now reconciled — tabs decoupled, see its section.)
- **Compact polish (deferred):** optionally **shrink** the inline tab bar in compact to reclaim some
  vertical space (user deferred sizing); judge the now-empty collapsed compact header.
- **Unify the sidebar-toggle clearance:** the gutter-icon insets (3/1.5/3.25rem in `SessionLayout`)
  and the Family B composer clearance both handle the bottom-left sidebar-reopen toggle — factor into
  one rule (flagged with the user as a future cleanup).
- **Focus model polish:** tab lifecycle (run work on "became visible/focused", not on tab
  activation). The file-clears-on-blur and focus-race bugs are now fixed (route-owner gating +
  deferred click-based focus claim — see the route/focus section). Overlay panels (`LayoutOverlay`)
  still have no focus claim — interacting with a peek overlay doesn't claim the route yet.
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
- SFC compile check: a scratch `.mjs` that `require`s `@vue/compiler-sfc` (the worktree's own
  `node_modules` works; the main repo's too — read-only `require`, no `.vite` cache writes) and runs
  `parse` + `compileScript` + `compileTemplate` + a brace-balance check on each `.vue`, plus
  `node --check` on the `.js` files. Do NOT run a full `vite build` against the main repo's
  node_modules (corrupts its `.vite` cache and can break the user's running instance).
