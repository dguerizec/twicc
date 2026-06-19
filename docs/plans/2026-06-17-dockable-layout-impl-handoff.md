# Dockable Layout — Implementation Handoff (2026-06-17, last updated 2026-06-19)

Resume point after compaction. **The feature is functionally complete** — docking + route/focus +
maximize/restore, and the full persistence stack (per-session `Session.layout`, named-layouts catalog
with save/select/manage, 3-tier defaults, per-scope menu rows, alphabetical listing). All live-verified.
Read order to resume: this doc → the step-1 plan
[`2026-06-16-dockable-layout-impl-step1.md`](./2026-06-16-dockable-layout-impl-step1.md) →
the design doc [`2026-06-16-dockable-layout-design.md`](./2026-06-16-dockable-layout-design.md)
→ the pure resolver `frontend/src/utils/layoutResolver.js` (the executable spec).

Branch/worktree: `layout` (`.worktrees/layout`). Its own dev instance runs via devctl
(frontend **5174**, backend **3501**). The branch is **42 commits** on top of `main` — not pushed, not
merged. Run `git log main..layout` for the breakdown. (Last rebased on `daa4a599`; `main` has since
advanced to `54fd6b15` — ~22 commits, so a re-rebase is due before merge.)
**Persistence is COMPLETE** (steps 1–4 of the layout-persistence plan — see
`docs/plans/2026-06-19-layout-persistence-impl-plan.md`). Layout state survives reload, syncs across
devices, new sessions open with a resolved global/project default, the named-layouts catalog has a
save/select menu **and** a rename/delete-with-reassignment manager (step 4), the menu surfaces per-scope
default rows (worktree → project → global, no duplication with the named list), and every layout list is
alphabetical. **Requires the dev to restart their instance** — migrations `0109` (`Session.layout`) +
`0110` (`Project.default_layout_id`). What's left there is only the explicitly-deferred v2 items
(per-device localStorage override, per-project named layouts, schema `version`, CLI catalog commands).

**Verification status:** verified **live in the browser** — step-1 docking, the route/focus model,
the overlay route-derivation + focus lifecycle, swap-on-navigate (incl. browser back/forward), the
clearance (refined per dock context 2026-06-19), and the icons (tab + dock-placement, serve + render).
The container-relative responsiveness and the compact-mode tab decoupling carried over from the prior
session were **confirmed live 2026-06-19** (A1/A2 + all of B, plus the reworked A3 clearance) — nothing
layout-side is compile-verified-only anymore. The **fullscreen-artifact-over-gutters** fix (see Bugs log
#9) was diagnosed and verified live by DOM hit-test in Chrome.

> Icon set is **Font Awesome Free** — confirmed at the network level: icons load from `ka-f.fontawesome.com`
> (the free kit host; pro would be `ka-p` + a `?token`), and no kit is configured (`kitCode = ""`). The
> "Pro" comment inside the served SVG files is just FA's generic build header, not a license signal.

> The dated sections below are chronological. The **2026-06-19 session** (center re-selects Chat when
> its tab is docked; the sidebar-toggle clearance refined per dock context + the terminal extra-keys
> bar; **maximize/restore** a dock or the central zone; **terminal explicit-start** — separate concern,
> own doc) is the newest. The **2026-06-18 session** (native wa-tab, edge-aware borders, overlay
> route-derivation + focus lifecycle, swap-on-navigate, mobile arrows, clearance single-source,
> tool-tab registry, resize splitters) is the bulk of current behaviour. Read the newest first where it
> supersedes earlier notes (notably: the overlay is route-derived; the clearance is now per dock context).

## Status: what works

- Default (nothing docked) view is **unchanged** — the existing `wa-tab-group` behaves as before.
- Tabs are **native `<wa-tab>`** (no more `<wa-button>` wrappers); unified tab chrome across
  session/project/dock/overlay/terminal navs (see 2026-06-18 section).
- Per-tab **placement arrow** (▾) on each tool tab → dropdown (Center + 6 docks). Chat has none (pinned).
  Hidden in the mobile tab strip (`!layoutTabsMode`). Now also on **overlay** tabs.
- Docking a tab renders it as a real dock region **with its live panel content** (Teleport — Terminal keeps its PTY across moves; no re-mount).
- Docked tab **leaves the center strip**; non-docked tabs stay in it.
- **Gutters** (minimize → edge rail, one icon per tab), **overlays** (95% peek), **swap**, sibling **merges**, **bottom→gutter**, mobile **tabs** mode — all driven by the pure resolver.
- **Edge-aware borders** on docks/gutters/overlays — a single `--divider-size` line on inner edges
  only, never on the layout boundary (see 2026-06-18 section).
- **Route/focus model** — the route is the single pointer to the focused tab; no URL loops,
  click-to-focus on docks/center. **The overlay is fully route-derived** and **swap/minimized docks
  reveal themselves on navigation** (the "active tab is always visible" invariant — see sections below).
- **Sidebar-toggle clearance** — single source (`--sidebar-toggle-clearance-x/-y`), consumed by the
  composer + gutters (see 2026-06-18 section).
- **Container-relative responsiveness** (prior session — **live-verified 2026-06-19**): the
  Files/Git/Artifacts `mobile-layout` and the chat/composer margins react to the panel's own width
  (dock / center zone), not the viewport.
- **Compact mode decoupled from the tabs** (prior session — **live-verified 2026-06-19**):
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
- `frontend/src/stores/data.js` — `localState.sessionLayout` (the live working copy; now **persisted** to `Session.layout`, debounced — see the persistence plan) + getter `getSessionLayout` + actions `ensureSessionLayout, setTabDock, minimizeDock, restoreDock, setLayoutActiveSide, setLayoutActiveResize, setLayoutGroupActiveTab, setLayoutMaximized, loadLayoutIntoSession, persistSessionLayoutDebounced, clearSessionLayout`.
- `frontend/src/views/SessionView.vue` — the integration (host + Teleport + gating + center filtering + route/focus model). Compact-tabs machinery removed (this session).

Modified for responsiveness + compact decoupling (prior session):
- `frontend/src/composables/useContainerBreakpoint.js` — observes the component's own root when no
  selector is given.
- `FilesPanel.vue` / `GitPanel.vue` / `TerminalPanel.vue` — `routeOwner` prop (sync-from-route gate);
  Files/Git also drop the `.main-content` selector (observe self).
- `SessionItem.vue` / `MessageInput.vue` / `MessageSnippetsBar.vue` — chat/composer `@media`→`@container`.
- `SessionLayout.vue` — adds `has-bottom-region` / `has-bottom-gutter` root classes.
- `SessionHeader.vue` / `ProjectDetailPanel.vue` / `ProjectDetailHeader.vue` — compact-tab machinery
  removed (tabs stay inline).

Modified/touched on 2026-06-18 (see that section for the why):
- `useSessionLayout.js` — overlay now route-derived (`openOverlayEdge` computed); removed
  `overlayActiveTab`/`openOverlay`/`closeOverlay`; added `gutterEdgeForTabAction` + `overlayEdgeForTab`;
  swap-on-navigate in the route watch.
- `SessionView.vue` / `SessionLayout.vue` / `LayoutOverlay.vue` — overlay `overlay-activate`/
  `-dismiss` lifecycle, `layoutTabsMode` arrow gating; native `<wa-tab>` tabs + unified chrome.
- `DockRegion.vue` / `DockGutter.vue` / `LayoutOverlay.vue` — edge-aware borders (+ `data-rid`).
- `App.vue` — base `--sidebar-toggle-clearance-x/-y` on `body.sidebar-closed` (single source).
- `MessageInput.vue` / `CollapsedBar.vue` — consume the clearance var (no fallback); native tabs in
  `ProjectDetailPanel.vue`; smaller arrow in `TabPlacementMenu.vue`; border-thickness tweaks in
  `FileTreePanel.vue` / `GitPanelHeader.vue` / `TerminalPanel.vue` / `WorktreeDialog.vue`.

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

## Route / focus model (the dock/center model — extended on 2026-06-18 for overlay + swap)

Agreed rule: **the route is the single pointer to the focused tab.** Generalized so it works
with multiple visible docked panes. (The overlay and swap parts of this rule were completed on
2026-06-18 — see that section; the overlay is now fully route-derived.)

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

## Container-relative responsiveness (prior session — live-verified 2026-06-19)

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

## Compact mode — tabs decoupled from it (prior session — live-verified 2026-06-19)

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

## Session 2026-06-18 — native tabs, borders, overlay lifecycle, swap, clearance

All live-verified in the browser unless noted. These supersede earlier notes where they overlap.

### Native `wa-tab` + unified tab chrome
The center (`SessionView`) and project-detail (`ProjectDetailPanel`) tab labels were a `<wa-button>`
wrapped inside each `<wa-tab>`; now they're native `<wa-tab>` content — the active state comes from
wa-tab's own indicator, not button `appearance`/`variant`. Unified the tab chrome across the
session/project/dock/overlay/terminal navs: native indicator (WA default `currentColor`, **no**
per-component `--indicator-color`), `--track-width: var(--divider-size)`, base padding `2xs xs` +
`gap 2xs`, `--divider-size` borders, `.reduced-height` (global util in `App.vue`) on the minimize/
close buttons. The placement arrow shrank to `0.5em`.

### Edge-aware borders (DockRegion / DockGutter / LayoutOverlay)
A region/gutter/overlay draws a divider only on **inner** edges (facing the center or a sibling),
never on an edge touching the layout boundary; **single ownership** so each shared edge is exactly one
`--divider-size` line. Driven purely by CSS from `region.kind` + a `:data-rid="region.id"` attribute
on `DockRegion` — the resolver encodes "both siblings shown" in the id: `left-bottom`/`right-bottom`
exist **only** in a split column, so they own the inter-sibling **top** divider; `bottom-right` owns
the vertical divider toward `bottom-left`. Side columns border the center-facing edge; bottom regions
the top; gutters/overlays only their center-facing edge. Thickness is `var(--divider-size)` everywhere.

### Overlay = derived from the route (single source of truth) + focus lifecycle
**Big architectural change — supersedes the old imperative overlay state.** `openOverlayEdge` is now a
`computed(() => overlayEdgeForTab(routeActiveTabId.value))`: the peek overlay is open **iff** the
active (route) tab's dock is in overlay mode, on that edge, showing that tab. Removed
`overlayActiveTab`, `openOverlay`/`closeOverlay`, the stale-overlay watcher, and the (briefly-added)
auto-open watcher. So bookmarks / direct navigation / **browser back-forward** open and close it for
free — no second source of truth, no desync (the earlier bug: forward left the overlay open).
- Gutter/overlay interactions emit `overlay-activate` / `overlay-dismiss` (which are just navigations
  in `SessionView`). Opening **remembers** the pre-open active tab; an explicit **dismiss** (backdrop,
  close button, or re-click toggle) returns to it — falling back to the center (`main`), and **never**
  remembering an overlay-only tab as the return (would re-open immediately).
- Overlay tabs gained the per-tab `TabPlacementMenu`; overlay `z-index` 9 → **11** (still below the
  gutters at 12, which stay clickable above it). A DockRegion-style body click-to-focus claim was
  briefly added to the overlay then **removed** — under route-derivation the overlay always shows the
  active tab, so it was a permanent no-op.

### Swap-on-navigate (completes the "active tab is always visible" invariant)
The composable's `watch(routeActiveTabId)` already revealed **minimized** docks (`restore`); it now
also calls `swapSide(edge)` when the active tab sits in a **`swap`** rail (its side lost mutual
exclusion, capacity 1). So navigation / back-forward / keyboard tab-switch keep the active tab visible
— same invariant the overlay derivation upholds. New helper `gutterEdgeForTabAction(tabId, action)`
(generalizes `overlayEdgeForTab`). The **collapsed-AND-swapped** combo is handled in one watch pass:
`restoreDock` does a reactive `splice` that synchronously re-derives `render`, so the following swap
check reads fresh state (the un-collapsed dock now a `swap` item) → `swapSide` fires. Verified live
(navigating to a collapsed tab on the swapped-out side flipped `activeSide` **and** cleared `collapsed`).

### Mobile fallback hides the placement arrows
`layoutTabsMode = layout.measured.value && layout.render.value.mode === 'tabs'` (SessionView). The 5
center `TabPlacementMenu` arrows are `v-if="!layoutTabsMode"` — in the mobile tab strip the docking
system is skipped, so a tab can't be placed into a dock. Gated on `measured` so the arrows don't flash
out before the first measurement on a normal-width screen.

### Sidebar-toggle clearance — single source (no more duplicated magic numbers)
The closed-sidebar reopen toggle's clearance was duplicated literals + copy-pasted `:is/:not` context
selectors across `MessageInput`, `CollapsedBar`, `SessionLayout`. Now: `--sidebar-toggle-clearance-x`
/ `-y` are defined **once** on `body.sidebar-closed` (`App.vue`); `SessionLayout` only **refines** `-x`
per dock context (thin left rail → `1.5rem`; bottom dock/gutter or left column → `0`; nothing docked
falls through to the base `3.5rem`). The composer toolbar (`var(...)`), collapsed bar
(`calc(var(...) + 0.5rem)`) and left gutter (`-y`) consume with **no fallback**, so the value lives in
exactly one place. The bottom-gutter `.start` inset (`3`/`1.5rem`) is kept literal (single copy,
slightly different offset from the toggle). Decided: project/workspace pages will **not** get a layout
(no central zone), so the clearance stays session-only — not moved to `ProjectView`. **Refined
2026-06-19** (see that session): the values changed, a bottom *gutter* no longer zeroes the composer,
and the terminal extra-keys bar now derives from the clearance too.

### Real icons — tab icons + custom dock-placement icons
Tabs no longer rely on guessed/missing glyphs. A single `TAB_ICONS` map in `SessionView` feeds both the
resolver input (`layoutTabs`) and the center tab strip markup, so the center tabs (Chat/Files/Git/
Terminal/Artifacts/Orchestration) now show icons; subagent tabs use `robot`; Artifacts uses `shapes`
(matching the existing artifacts UI in `SidebarViewSwitch`). `ProjectDetailPanel`'s tabs got icons too
(Stats `chart-simple`, Files/Git/Terminal). Dock-placement icons: the old single shared FA glyph
(`table-cells-large`, identical for every dock) is replaced by **seven custom SVGs** in
`public/icons/docks/` (PyCharm-style: layout frame outline + a filled bar where the panel docks, `0 0 24 24`,
`currentColor`), consumed via `<wa-icon src="/icons/docks/…">` (local endpoint — the only kind `src`
supports). The placement menu (`TabPlacementMenu`) gained a disabled **"Tab placement"** header + divider,
and the `center` option was relabeled **"Main area"**. Default choices to revisit if wanted: subagent
`robot`, Stats `chart-simple`.

### Optional tabs = absent when empty (model change + resolver, prototyped in the playground)
**Reversed the design's "empty-optional → gutter" rule** (user call, 2026-06-18). An optional tab
with no content is now treated as **absent**: it produces no tab, no dock, no gutter, no overlay —
exactly as if it were never in `tabs`. Its dock **assignment** is remembered, so it appears at the
right dock the moment content arrives (a present sibling in the same dock just shows alone until
then). The optional set: **Files + Terminal are the only always-present tabs**; Git, Artifacts,
Orchestration — and future tabs — are optional (Chat + subagents are center-only, not dockable).

Done in the **resolver** — both the interactive playground (`~/.twicc/artifacts/<this-session>/
layout-playground/`) and the code `frontend/src/utils/layoutResolver.js`, kept in **parity** (only
the header comment + the `resolve`↔`resolveLayout` name differ). A one-line
`isPresent(t) = !t.optional || t.hasContent` filter at the top of the resolve function drops
empty-optional tabs before bucketing; `demandOf` simplified; the `optional`/`hasContent`
empty→gutter/overlay branch removed. The playground's now-obsolete "empty → gutter" test was
**deleted**; the suite is **19/19** and four targeted headless checks pass (absent everywhere /
with-content shows as a column / mixed dock shows present-only / center strip excludes empty-optional).
The design doc got a dated addendum at its "Optional tabs" rule.

**What's left for the real feature (not done — rides on persistence):** (1) an **optional-tab
registry** in `SessionView` marking which tool tabs are conditional, to DRY the scattered
`hasGitRepo`/`hasArtifacts`/`hasSpawnRoot` gates (`layoutTabs` ~589, `toolPanelTabs` ~751, route
guards ~798); (2) **persistence** of the placement intention for a not-yet-present tab (a default
layout pre-assigning Artifacts→right-top only pays off once intentions persist). The code-resolver
change is a **no-op today** (SessionView already omits absent tabs), so nothing changes in the app
until the registry/persistence land — it's the spec, validated, ready for that wiring.

### Tool-tab registry (single source) + cold-load redirect fix
The conditional-tab knowledge was scattered across `SessionView` — `if (hasGitRepo)` / `hasArtifacts`
/ `hasSpawnRoot` repeated in `layoutTabs`, `orderedTabs`, `LAYOUT_TOOL_IDS`, the keyboard guards, the
template `v-if`s, and three near-identical redirect watchers. Now a single declarative `TOOL_TABS`
registry (`{ id, label, icon, present, redirectReady? }`) is the source: only **Files + Terminal** are
always present; **Git/Artifacts/Orchestration** (and future tabs) are conditional via `present()`.
Everything derives from it — `layoutTabs`, `orderedTabs`, `LAYOUT_TOOL_IDS`, `TAB_ICONS` (derived), the
keyboard guards (`isToolTabPresent`), and the template `v-if`s. Adding a conditional tab is now one
registry line. (Chat + subagents stay center-only, not in the registry.)

Collapsing the three redirect watchers into one surfaced + fixed a **pre-existing bug**: a **cold load
/ direct navigation** to an absent tool tab's URL (e.g. `/git` on a non-git session) did **not**
redirect to chat — only a warm in-app nav did. Root cause: the readiness signals (session row, project
row) were read inside the watcher callback (`redirectReady`) but were **not** watch dependencies, so on
a cold load the `immediate` run skipped (data not ready) and never re-fired (the presence flags stay
stably false while only the readiness data loads). Fix: a computed `absentActiveToolTab` ("the active
tab is a tool tab that is *definitively* absent — not present AND ready"); being a computed it tracks
**every** reactive source it reads (presence + session + project), so it flips the moment the gating
data loads and the watcher redirects. Verified live in Chrome: cold load of `/git`, `/artifacts`,
`/orchestration` on a session lacking each now redirects to chat; a git-bearing session stays on `/git`.

### Resize — drag splitters (dock + sibling)
The resolver already emitted `splitters` (geometry + math params: `origin` / `extent` / `from` /
`configKey`); this wires the `kind:'dock'` ones into the UI — drag a side column's width or the bottom
region's height against the center. `SessionLayout` renders an invisible 9px hit-strip over each
boundary (`.layout-splitter`; a brand line on hover/drag — the divider itself stays the region border),
and a pointer drag writes the dragged fraction to the store, fed to the resolver as a `config` override
(`resolveLayout` re-clamps to its px mins each render — `centerResizeMinW` etc.). Window-level
pointermove/up so the drag survives the re-render that repositions the handle; the dragged side sets
`activeResize` (it wins the squeeze). Math ported verbatim from the playground minus its preview
`scale`: `frac = (from==='end' ? origin - pointer : pointer - origin) / extent`. Store: an ephemeral
`resizeFractions` map (`configKey -> number`) on the per-session layout intention +
`setLayoutResizeFraction`; the composable passes it as `config` and exposes `setActiveResize` /
`setResizeFraction`. Custom hit-strips (not `wa-split-panel`) sidestep the `wa-reposition` trap.
Validated live in Chrome (synthetic drags): left-dock (`axis-v`/`from-start`) and bottom-dock
(`axis-h`/`from-end`) move the boundary **1:1** with the cursor (exact px), correct direction, clean
cursor/selection teardown; right-dock (`axis-v`/`from-end`) covered by composition (its two branches
each validated). **Sibling splitters (`kind:'sib'`) followed (step 2):** the same generic handler renders + drags them
— `{edge}-split` (axis-h, between a column's two siblings) and `bottom-split` (axis-v, between the two
bottom siblings); they leave `activeResize` untouched. Validated live (side-split, axis-h/from-start,
moved 1:1 — 100 px exact). Fractions now **persist** (they ride the persisted intention's
`resizeFractions`).

**Touch affordance:** on coarse-pointer devices (`@media (pointer: coarse)`) each handle shows a
scaled `grip-lines-vertical` grip (rotated 90° on axis-h so its lines run along the divider), mirroring
the sidebar splitter's `.divider-handle`. The grip overflows the thin strip and a pointerdown on it
bubbles to the strip → starts the drag, so the enlarged grip is a much larger tap target (the point on
touch). Centering is pure CSS: the strip is `display: grid; place-content: center`, and the `wa-icon`
carries `auto-width` so its box sizes to the narrow glyph (without it the box is centered but the glyph
paints left — see bug 6). No magic numbers: the strip thickness is a `--resize-grab` token, the strip is
centered on its divider with `translate`, and the hover line is `var(--divider-size)` (theme-aware).

**Stacking (z-index):** a docked panel's own z-index (sticky selects, the pane-callout overlay, 10–20)
used to leak into `.session-layout` and paint over the splitters (z 5). Each `.dock-region` now owns a
stacking context (`isolation: isolate`) so it stays local and the splitters sit above panel content
(still below gutters 12 / overlay 11). Because that traps the FilePane full-window preview
(`position: fixed; z-index: 1000`), the region **provides its own `expandPreviewHost`**: while a preview
inside is expanded it drops the isolation (`isolation: auto`, so the overlay escapes above the splitters)
and chains to the host up the tree (ProjectView lifts `.main-content` for the sidebar). See bug 7.

## Session 2026-06-19 — center re-select fix + clearance refinement

All live-verified in the browser.

### Center re-selects Chat when its active tab is docked (bug #8)
See Bugs log #8 (commit `51dab168`). Docking the very tab the center was *showing* left
`centerActiveTab` pointing at a tab no longer in the center → blank center. Fixed by re-validating the
`lastCenterTab` fallback against `isCenterTab` and dropping back to Chat (`main`, always center-only)
when the remembered tab has left the center. Reactive, URL untouched (the tab is now active in its dock).

### Sidebar-toggle clearance — refined per dock context + terminal extra-keys bar
Refines the 2026-06-18 single-source clearance (the "single source" architecture stands; values and one
rule changed, and a new consumer was added). Four files: `App.vue`, `SessionLayout.vue`,
`TerminalExtraKeysBar.vue`, `CollapsedBar.vue` (`MessageInput.vue` unchanged — already reads `-x`).

- **New left-edge source `--sidebar-toggle-clearance-left-x`** (`App.vue`, refined in `SessionLayout`):
  the clearance the bottom-left toggle needs from the **left edge alone** — nothing → `2.5rem`, a thin
  left gutter → `1rem`, a full left column → `0`.
- **Composer `-x` = left-x, zeroed only by a bottom dock _region_** (it lifts the composer above the
  toggle). A bottom **gutter** no longer zeroes it (too thin) — the composer **and** the collapsed bar
  keep the clearance whether a bottom gutter is present or not. Net: bottom region / left column → `0`;
  left gutter → `1rem` (collapsed `1.5rem`); else → `2.5rem` (collapsed `3rem`).
- **Terminal `.extra-keys-bar` now derives from the clearance** (was a hardcoded `4rem`): `left-x + 1rem`,
  but **only when it actually sits at the bottom-left** — the terminal is the **sole or bottom-left**
  bottom dock (never bottom-right), or it's in the **center** and the center reaches the bottom-left (no
  left column, no bottom dock lifting it); otherwise its base padding. Driven by
  `--sidebar-toggle-clearance-extra-keys`, **set on the nearest layout element**
  (`.center-slot` / `.dock-region[data-rid="bottom"|"bottom-left"]`) so it **inherits across the panel
  teleport** into the bar — no `:deep` into the relocated terminal. The consumer's fallback
  (`calc(left-x + 1rem)`) covers terminals **outside** the dockable layout (the project view). Values at
  the bottom-left: `3.5rem` (nothing left) / `2rem` (left gutter); everywhere else: base.

### Maximize / restore (a dock or the central zone)
A region — a single dock (e.g. `left-top`) **or** the central zone — can be maximized to fill the whole
layout area: only it renders (with its tabs), **no other docks, no gutters, no splitters**. The only exit
is **restore**. One unified mechanism (user's call: deliberately *not* PyCharm's "keep the gutters" model
— far simpler, nothing to arbitrate on a gutter click while maximized).

- **Resolver** (`maximized: string[]` — the region's dockIds, or `['center']`): a highest-priority
  short-circuit to a single full-bleed region of `kind: 'maximized'`, with empty gutters/splitters/overlays.
  **Prototyped + tested in the playground first** (4 scenarios), then ported to the code resolver — parity
  re-verified (23/23 suite on *both* + a 240/240 strict output diff incl. all maximize variants).
- **State**: `maximized` on the ephemeral layout intention (`data.js` + `setLayoutMaximized`), **excluded
  from future persistence** (transient view state). The composable exposes `maximize`/`restoreMaximized`
  + `maximizedRegion`/`isCenterMaximized`, and **auto-restores** if the maximized region loses all its tabs.
- **UI**: a maximize button (`expand` icon — the same pair as FilePane's fullscreen toggle, `compress` to
  restore) in each dock's tab bar, **after the minimize**; the central tab bar gets one on the right, shown
  only when there are docks to hide (`dockingRendered`). Maximizing **routes + focuses** the region's active
  tab (the URL points into what's shown); restore returns to the **exact** prior layout. A maximized **dock**
  teleports its panel into the full-bleed region (state preserved — PTY, file selection); a maximized
  **center** just fills the `.center-slot` and hides all docks. Placement arrows hidden while maximized (no
  re-docking — it's a separate mode). The terminal's extra-keys clearance also covers the maximized region
  (`data-rid="maximized"`).
- **Live-verified in Chrome** (separate tab): dock + center maximize/restore, icons render, no console
  errors, URL/focus and content preservation all correct.

Files: `layoutResolver.js`, `data.js`, `useSessionLayout.js`, `DockRegion.vue`, `SessionLayout.vue`,
`SessionView.vue` (+ the playground resolver/tests/harness as the prototype).

### Terminal explicit-start (separate concern — full doc:`2026-06-19-terminal-lifecycle-layout-analysis.md`)
A docked-by-default terminal used to auto-create its PTY/tmux just by being shown (region-active on
render), spawning a tmux session for every session merely viewed. Fixed **on this branch** (commit
`6ad90552`): the **Main** terminal sub-tab (index 0) no longer auto-creates — it **attaches** if a tmux
session already exists for it, else shows a **"Start" callout** (reuse of the disconnect overlay); **new
sub-tabs (index > 0)** auto-connect as before (login + snippet flows untouched). Existence is read from the
`list_terminals` discovery (`terminalTabs.indices`, main WS, no PTY). `TerminalInstance` gained a
`startMode` prop (`auto`/`manual`/`pending`, 4s safety net); `TerminalPanel.startModeFor(index)` decides.
Same in tmux + non-tmux (non-tmux → always Start). **Live-verified in Chrome both modes** (incl. real tmux:
first visit Start → tmux created → reload auto-attaches). The analysis doc holds the full decisions (Q1–Q5)
and the still-**open** items: the general **focus-model lifecycle** (Option C — per-tab visible/hidden/
focused signals; e.g. Git stops polling when not shown) and a tmux **reaper/GC** (Option D), both deferred.

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
5. **Cold-load redirect to an absent tool tab didn't fire** — direct navigation / bookmark to `/git`
   (etc.) on a session lacking that tab stayed put instead of redirecting to chat. The readiness
   guard (`redirectReady`) was read in the watcher callback but wasn't a watch dependency, so the
   `immediate` run skipped before data loaded and never re-fired. Fixed with a computed
   (`absentActiveToolTab`) tracking presence + readiness reactively (see 2026-06-18 section).
   Pre-existing (the old per-tab watchers had it too); warm in-app nav masked it.
6. **Touch resize grip painted off the divider** — the grip box (`wa-icon`) was centered, but `wa-icon`
   paints the narrow `grip-lines-vertical` glyph left-aligned inside a square box (glyph ~6px in a ~20px
   box), so the visible lines sat ~21px off. `getBoundingClientRect` on the box reported "centered" and
   hid it — only a screenshot exposed it (lesson: paint/stacking offsets need a screenshot, not a rect
   check). Fixed with `auto-width` (box shrinks to the glyph) + the strip's grid `place-content: center`.
7. **Docked-panel chrome over splitters → full-window preview trapped** — panel z-index (10–20) leaked
   over the splitters; `isolation: isolate` on the region fixed that but trapped the FilePane
   `position:fixed z-1000` full-window preview below the splitters. Originally "fixed" by the region
   providing `expandPreviewHost` that drops its isolation while expanded — but **that provide/inject was
   dead** (teleport: the panels' logical parent is SessionView, not the dock region), so the class never
   applied. The real fix is CSS `:has()` — see bug #9. The base `isolation: isolate` stays (it's what
   keeps panel z-index off the splitters); only the drop trigger changed.
8. **Center blanked when docking its own active tab** — placing the tab the center was *showing* into a
   dock left the route on it (now active in the dock) but it was no longer a center tab; `lastCenterTab`
   still pointed at it, so `centerActiveTab` named a tab with no `<wa-tab>` in the center strip → empty
   center. Fixed by re-validating the `lastCenterTab` fallback against `isCenterTab` and dropping back to
   Chat (`main`, always center-only) when the remembered tab has left the center. Reactive (the computed
   reads `dockingRendered`/`dockOf`), URL untouched. (`SessionView.centerActiveTab`, commit `51dab168`.)
9. **Fullscreen artifact preview painted *under* the gutters** — a FilePane preview expanded to full
   window (`position:fixed; z-index:1000`) sat below a dock's gutters. Root cause (confirmed live by DOM
   hit-test in Chrome): the preview lives in a `.dock-region` whose `isolation: isolate` traps its
   z-index at the region's z-auto level, while the gutters (z-index:12) resolve one stacking context
   higher (`.main-content`) and paint over it. Crucially, bug #7's "drop the isolation via
   `expandPreviewHost` provide/inject" mechanism was in fact **dead**: the tool panels teleport from
   SessionView's hidden host, so the injected host never reached the dock region and the
   `preview-expanded` class never applied (verified by calling the injected host directly — no effect).
   Replaced the whole chain with one self-contained CSS rule,
   `.dock-region:has(.file-pane-preview--fullscreen) { isolation: auto }`: the region drops its
   isolation exactly while it holds a fullscreen preview, so the overlay escapes above the
   splitters/gutters. No JS, no provide/inject. (commit `6f189f0c`.)

## Decisions / deviations vs the plans

- Teleport host is **bounded to the 5 tool panels** (chat + subagents stay in the center group) —
  decided for low risk; not spelled out in the plan.
- **Click-to-focus** + **minimize-returns-focus** + the **ownsRoute(Terminal-only) loop fix** are
  new this session (the plan only sketched "route = single pointer").
- Auto-focus-on-dock: **kept** (user validated 2026-06-18 — docking a tab focuses it, as desired).
- Subagent tabs **center-only is settled** (won't change).
- Resize splitters: **wired** (2026-06-18) — dock + sibling splitters drag live (custom hit-strips,
  fractions persisted). See the 2026-06-18 resize section.
- **Optional-empty model reversed (2026-06-18):** an optional tab with no content is now **absent**
  (no tab/dock/gutter/overlay), not collapsed-to-gutter — its placement is just remembered for when
  content arrives. Prototyped in the playground and **ported to the code resolver** (`isPresent`
  filter; the `optional`/`hasContent` empty→gutter branch removed). See the 2026-06-18 subsection.
- Icons are **done** (2026-06-18): real tab icons + seven custom dock-placement SVGs (see that
  section). Only the subagent (`robot`) and Stats (`chart-simple`) defaults are open to a re-pick.

## Known issues / open (not yet fixed)

- **Persistence — COMPLETE (steps 1–4 + scope rows + alphabetical listing).** `Session.layout`
  (migration 0109) persists + syncs the per-session intention; a `layouts.json` catalog of named layouts
  (synced, mirrors workspaces.json) with a save/select menu (`LayoutMenu` ▾) **and** a rename/delete
  manager (`LayoutManagerDialog`, reassignment-on-delete); the 3-tier default (global
  `settings.defaultLayoutId` → project `Project.default_layout_id` (0110) → session) resolved + frozen at
  creation, mirroring agent settings; per-scope default rows in the menu (worktree → project → global,
  deduped against the named list); and alphabetical ordering wherever layouts are listed. Full design in
  `docs/plans/2026-06-19-layout-persistence-impl-plan.md`. **Deferred (v2):** per-device localStorage
  override, per-project named layouts, schema `version`, CLI catalog commands. `maximized` stays
  transient (never persisted).
- **Layout thresholds/values are placeholders** — tune later: the resolver thresholds, the 800px
  container breakpoint (`useContainerBreakpoint`), the 40rem chat/composer `@container` thresholds,
  and the sidebar-toggle clearance values (centralized in `App.vue` + refined per dock context in
  `SessionLayout` — see the 2026-06-19 session for the current values). (The resolver IS correctly
  reused; an earlier "stuck in widescreen" report was just a too-wide window, not a bug.)

## What remains (categorized todo — none trivial, lots left)

- **Step 1 — done + fully live-verified (2026-06-19).** The prior session's responsiveness + compact
  decoupling are confirmed live; auto-focus-on-dock is **decided = kept**; the optional-empty model is
  **decided = absent** (done in the resolver — what's left there is the registry + persistence, below);
  overlay route-derivation, swap-on-navigate, borders, the clearance (now per dock context) and the
  icons are done + live-verified. What's genuinely left below is persistence + new interactions.
- **Compact tab bar + custom tab styling: DONE** (user-confirmed 2026-06-19). The inline compact tab
  bar and the tab visual styling are finished — do **not** re-list these as remaining.
- **Focus model polish:** tab lifecycle (run work on "became visible/focused", not on tab activation).
  The file-clears-on-blur, focus-race, overlay-focus and route-derivation work is **done** (see the
  route/focus + 2026-06-18 sections) — this is just the remaining "lifecycle hooks" idea.
- **Resize UI: done** — both dock splitters (a column's width / the bottom's height vs the center) and
  sibling splitters (between a column's two siblings / the two bottom siblings) drag live (see the
  2026-06-18 section). Custom hit-strips, not `wa-split-panel` (so the `wa-reposition` trap is
  sidestepped). The fractions' **persistence is now done** (resizeFractions ride the persisted
  intention).
- **Persistence: COMPLETE (steps 1–4 + scope rows + alphabetical listing)** —
  `docs/plans/2026-06-19-layout-persistence-impl-plan.md` is the spec. The "remembering an absent tab's
  dock" payoff falls out for free (the catalog/intention key tabs by id; the resolver filters
  `isPresent`). **Deferred only:** per-device localStorage override, per-project named layouts, schema
  `version`, CLI catalog commands (v2). Doc chore still pending (plan §7): add `layouts.json` to the
  data-dir inventory in `CLAUDE.md` / `AGENTS.md` and note `Session.layout` / `Project.default_layout_id`
  in the models section.
- **Interactions/UX:** keyboard nav; reset (project/default/tabbed); drag-and-drop placement;
  animations. (Maximize/restore, named layouts/presets, save/select/manage are **done**.)
- **Polish/divers:** structural-vs-resize-min naming; keep docs/AGENTS.md/CLAUDE.md in sync if rules
  change. (Custom tab styling is **done** — see above. The old "bottom region + empty-optional bottom
  gutter" coexistence edge case is now **moot** — empty-optional docks no longer exist.)

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
