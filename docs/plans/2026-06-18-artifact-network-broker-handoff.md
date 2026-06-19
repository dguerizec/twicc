# Artifact Network Broker — Implementation Hand-off

**Read this with the design doc** [`2026-06-18-artifact-network-broker-design.md`](2026-06-18-artifact-network-broker-design.md) (same dir). The design doc holds the *what/why* (and is **up to date**). This hand-off holds the *implementation status, the non-obvious operational knowledge, and the next steps*.

**Worktree:** `/home/twidi/dev/twicc-poc/.worktrees/artifacts-fetch`, branch `artifacts-fetch` (off `main`). All work lives here. Servers run: backend `:3502`, frontend `:5175`.

---

## 1. Status at a glance

| Phase | What | State |
|---|---|---|
| Design | doc + decisions O1–O5 + refinements | ✅ committed `462acd90` (+ later edits folded into later commits) |
| 1 | server proxy + IP guard (`classify_ip`, `resolve_target`, pinned `proxy_fetch`, `/api/artifact-proxy/`) | ✅ committed `1dc326a5` |
| 2 | `ArtifactBookmark.allowed_hosts` + migration 0109 + serializer + REST mutation endpoint/service | ✅ committed `2ab97b3f` |
| 3a/b | broker HTML serving: shim injection + strict CSP, wired into `artifact_serve` + `file_raw`/`standalone_file_raw` | ✅ committed `44dc128a` |
| 3c | shim bundle (penpal + @mswjs/interceptors → IIFE) built + served at `/_twicc/artifact-broker-shim.js` | ✅ committed `0b8f65e3` |
| 4 | **the host** (`host.js` + `ArtifactBrokerPrompt.vue` + FilePane) **+ final policy** (header pass-through + same-origin promptable host-direct) — E2E-verified | ✅ committed `0fe6ed64` |
| 4-consent | **"This session" grants** (in-memory, until reload) replacing per-request "once" + **burst coalescing** (N concurrent requests to one host → 1 prompt) — E2E-verified | ✅ committed `f42c96da` |
| doc | `window.parent` origin-isolation finding (tested + reverted, §13) | ✅ committed `35163802` |
| **5** | **dedicated-page shell, UNIFIED with the in-SPA preview** (`/artifacts/<id>/`) | ✅ committed `f8967aaf` (E2E-verified 2026-06-19) |
| 4-rest | proxy server-side allowlist re-check (defense-in-depth, §6.4) | ❌ **decided against 2026-06-19** (design §6.4 "Decision update"; rationale in §7) |
| 6 | docs (system_prompt Artifacts preamble + CLAUDE.md/AGENTS.md broker posture) | ✅ **DONE 2026-06-19 — UNCOMMITTED** (not the CLI `twicc-artifacts` skill — unrelated) |

`git log --oneline`: the last *committed* state is a docs commit `414cf8b4` on top of `f42c96da` (consent) · `35163802` (origin-isolation doc) · `0fe6ed64` (phase 4 + policy) · `0b8f65e3` (3c) · `44dc128a` (3a/b) · `2ab97b3f` (2) · `1dc326a5` (1). **Phase 5 is implemented + verified but NOT yet committed** — the working tree carries the phase-5 diff (see §2 for the file list). Run `git status`/`git log` for the true state.

---

## 2. Phase 5 — UNIFIED dedicated-page shell (DONE 2026-06-19, UNCOMMITTED) + what's next

**User directive (2026-06-19):** make an artifact behave **identically** in the in-SPA preview *or* its isolated page (`/artifacts/<id>/`) — **one shared shell**, not two parallel implementations. Built with **option A** (a small Vite Vue bundle reusing the exact composable + dialog), per the user's call, with a hard constraint: **minimal bundle, pull in none of the main SPA**. **Implemented + E2E-verified; NOT yet committed** (user commits on request).

### What was the asymmetry (now fixed)
- **In-SPA preview:** `FilePane.vue` iframes the artifact (backend-served, shim+CSP) and mounts the broker **host** on it in the SPA parent → broker works.
- **Dedicated page (was broken):** `artifact_serve` served the artifact **as the top-level document** → no parent host → the shim's `connect()` found nobody → no interceptor → the artifact's `fetch` hit CSP `connect-src 'none'` → blocked. A dedicated-page artifact had no network at all.

### What was built
**Backend:**
- `broker_html.py` — `ARTIFACT_INNER_DOC_PATH = "__twicc_doc__"`, `ARTIFACT_SHELL_JS_URL`/`_CSS_URL`, `artifact_shell_response(*, bookmark_id, allowed_hosts)` (+ `_shell_html`): builds the **trusted shell** page (no artifact CSP, no shim — TwiCC's own code; injects `<script id="twicc-shell-data">` with `{innerDocUrl, bookmarkId, allowedHosts}`, `<` escaped against breakout).
- `views.py` `artifact_serve` (~3304): `asset==""` → **shell** for an HTML root (else served directly, unchanged for non-HTML); `asset==ARTIFACT_INNER_DOC_PATH` → the artifact doc **wrapped** (shim+CSP); other asset → raw. New `artifact_shell_asset(request, asset)` serves `static/artifact-shell/` (like the shim).
- `urls.py`: `path("_twicc/artifact-shell/<str:asset>", views.artifact_shell_asset)`. The inner-doc sentinel rides the existing `<path:asset>` route — no new route.

**Frontend (the unification):**
- `frontend/src/composables/useArtifactBroker.js` — the shared wiring (prompt state machine + host mount/teardown + watch). Caller passes `(iframeRef, getConfig, watchSources)`; `getConfig` returns `{documentUrl, bookmarkId, allowedHosts, persistAllow}` or null. **No store/router import** (keeps the shell light).
- `FilePane.vue` refactored onto it — **identical watch sources + config → zero behaviour change** (its `persistBrokerAllow` still uses `apiFetch`).
- `frontend/src/artifact-shell/{main.js,ArtifactShellApp.vue}` — the shell app: iframes the inner doc (same sandbox as the preview), mounts the **same** composable + **same** `ArtifactBrokerPrompt.vue`; `persistAllow` is a plain same-origin `fetch` (page holds the cookie). `main.js` imports only Vue + 4 WA components (dialog/button/callout/icon) + WA css/default theme.
- `frontend/vite.config.shell.js` — standalone lib build → `static/artifact-shell/{shell.js,shell.css}`, `base:'/_twicc/artifact-shell/'`, `define: process.env.NODE_ENV='production'` (lib mode doesn't substitute it → Vue throws `process is not defined` without this). `package.json` build script chains it; output gitignored.

**Bundle:** `shell.js` ~67.5 kB gzip + `shell.css` ~19.9 kB gzip — Vue + the dialog/button/callout/icon + penpal, nothing of the SPA (82 modules).

### ⚠️ Test-env gotcha that cost real time (NOT a bug)
On a **hidden/background tab**, the browser **freezes Web Animations** → the wa-dialog's hide animation never reaches `finished` → `wa-after-hide`/`dialog.close()` never fire → **the consent dialog stays open and its `<dialog>` overlay blocks the page**. Looks exactly like a close bug; it isn't. Symptom signature: `document.visibilityState==='hidden'` + the panel's `getAnimations()` stuck at `"running"` + `open` reverting to true. **Bring the tab to the foreground before testing any WA animated open/close.** No real-world impact (a user must see the modal tab to click a decision → it's visible → animation runs → closes). Verified: identical component closes instantly on a **visible** tab (both FilePane and the shell).

### What's next (resume here)
Phase 5 is done. Remaining, in order:
1. **Commit phase 5** when the user asks (the diff spans the files in §5).
2. **Proxy server-side allowlist re-check** (§6.4/§7) — defense-in-depth, independent.
3. **Phase 6 docs** (CLAUDE.md/AGENTS.md broker posture + artifacts skill).

---

## 3. CRITICAL operational knowledge (cost real time to learn)

### Python changes need a BACKEND RESTART — the frontend does not
The backend (uvicorn) does **NOT** auto-reload on `.py` edits: a running server keeps the **old** code. Frontend source (`host.js`, `.vue`) **HMRs live** on `:5175`. So after editing any backend `.py` (e.g. `proxy.py`, `views.py`) you **must restart the backend** or the change is invisible. *This bit during E2E:* the header-forwarding test reported `Authorization echoed: false` because the server was still running the old allowlist code — a restart fixed it. (Symptom of stale proxy code: only `accept`/`content-type` get forwarded — the old allowlist set.)

### Running the Python tests in THIS worktree
`uv run pytest` is **broken here** (separate non-editable env). Use the worktree venv directly:
```bash
cd /home/twidi/dev/twicc-poc/.worktrees/artifacts-fetch
TWICC_DATA_DIR=$PWD .venv/bin/python -m pytest -q
```
`pytest`/`pytest-django` were installed with `uv pip install --python .venv/bin/python pytest pytest-django`. A devctl rebuild may drop them — reinstall if missing. Ruff: `uv run --no-sync ruff check <files>` (line-length 120). Full suite currently: **638 passing**.

### Restarting the worktree backend (devctl) — the ZOMBIE trap
`devctl stop` reports the backend stopped but **does NOT kill the real `run.py`** — it leaves a straggler that makes the next `start` lose the data-dir race. `devctl restart back` hits the same trap. Safe sequence (back-only keeps the frontend HMR alive):
```bash
uv run ./devctl.py stop back
pgrep -f "artifacts-fetch/.venv/bin/python3 ./run.py" | xargs -r kill   # verify gone; kill -9 if needed
uv run ./devctl.py start back
```
`.env` has **no** `TWICC_PASSWORD_HASH`, so `/api/*` and `/rpc/` are **un-authenticated** in this worktree (handy for curl). Starting/restarting servers is user-reserved — the user has been authorizing it for this worktree.

### Building the frontend / the shim (NON-OBVIOUS)
The hatchling build hook (`hatch_build.py`) **SKIPS `npm ci` + `npm run build` if `src/twicc/static/frontend/index.html` exists** — so a devctl restart does **NOT** rebuild the frontend. The **shim** and the **shell** bundles are the only frontend pieces NOT HMR'd — they're served from built static files, so editing `frontend/src/artifact-broker/*` or `frontend/src/artifact-shell/*` (or their vite configs) needs a rebuild:
```bash
cd frontend && npm run build   # = vite build && vite build --config vite.config.shim.js && vite build --config vite.config.shell.js
# (or just the one you touched, e.g. `npx vite build --config vite.config.shell.js`)
```
Outputs `static/artifact-broker/shim.js` + `static/artifact-shell/{shell.js,shell.css}` are **gitignored**. They're served with never-cache headers, so a browser reload picks up a rebuild **without** a backend restart. Deps `penpal@7.0.6` + `@mswjs/interceptors@0.41.9` (+ `vue`, `@awesome.me/webawesome` for the shell) are in `package.json`/lock; `npm ci` if missing.

### Migrations
`0109_artifactbookmark_allowed_hosts` exists and is applied. devctl auto-applies migrations at backend startup — **never `migrate` by hand.**

---

## 4. Key design decisions (one-liners — full rationale in the design doc)

- **The one rule (§3.5, §6.2, §14 O3):** only the cloud metadata address (`169.254.169.254` + `fd00:ec2::254`) is **ever** hard-blocked. *Every* other target — `localhost`, LAN, public, even TwiCC's own API — is reachable with **informed per-host:port user consent**. The server resolves the hostname, **pins** the IP, follows no redirects, and the prompt shows the **true** resolved target so a name can't masquerade as an internal IP. No range-blocking, no flag, no deployment detection — single-user self-hosted, the operator clicking the prompt *is* the authority.
- **Header policy = PASS-THROUGH (§6.5):** the broker forwards the artifact's headers **verbatim** (incl. `Authorization` + custom). It imposes **nothing**. Only *mechanical* headers are dropped: req `host` + `content-length` + hop-by-hop; resp `content-length` + `content-encoding` + hop-by-hop. The TwiCC cookie never reaches the proxy anyway (a `fetch` doesn't expose it; JS can't set `Cookie`).
- **Same-origin (§6.6) — no special case, only execution differs:** the artifact's **own files** auto-serve **host-direct, no prompt**. **Any other same-origin URL** (TwiCC's own `/api`, `/rpc`) is **promptable like everything else** and runs **host-direct** (browser does the fetch → attaches your TwiCC session → authenticated; the `Authorization` header is forwarded). The prompt is **uniform** (`localhost:5175 → 127.0.0.1 (loopback)`, no app label). **Cross-origin** goes through the pinning **server proxy**.
- **Consent grants (§9) — "This session" + "Forever":** a grant covers `scheme://host:port` for a `kind`. **This session** = remembered **in-memory on the host instance**, no persistence, cleared on artifact reload (the *default* — per-request "once" was useless). **Forever** = additionally persisted onto the bookmark (bookmarked-only). A **burst of concurrent requests to one host is coalesced into a single decision** (per-host pending-gate over the serialized prompt chain); cleared once settled so a later request re-asks. Dialog: `Deny / Forever / This session` + caption "kept until you reload this tab". Timed grants (5 min/1 h via the value object's `expires_at`) are a deferred extension.
- **Allowlist (§6.4, §10):** `ArtifactBookmark.allowed_hosts` = JSON **dict** keyed by normalized `scheme://host:port`, value `{ "kind" }` (object → extensible w/o migration: `expires_at`, …). **Port-by-port.** Mutation **REST-only / never agent-facing**.
- **Rebinding re-prompt (§6.2):** "Forever" stores the approved `kind`; every fetch re-resolves; a kind change (`public`→`loopback`/`lan`) re-prompts. In v1.
- **Shim vs CSP (§7, §8.4):** the injected shim (`@mswjs/interceptors` + `penpal`) is **DX, not the boundary**. The boundary is the header CSP `connect-src 'none'`. The shim installs its interceptor **only if a host answers** — no host ⇒ fetch falls to the CSP (this is exactly why the dedicated page needs phase 5).
- **Doc-vs-asset detection (§8.3):** `artifact_serve` → `asset == ""`; `file_raw`/`standalone_file_raw` → `Sec-Fetch-Dest` (`iframe`/`document` ⇒ wrap, else raw).

---

## 5. File map of what's implemented

**Backend (Python):**
- `src/twicc/artifacts/proxy.py` — `classify_ip`, `resolve_target` (resolve+pin), `normalize_host_key`, `filter_request_headers`/`filter_response_headers` (**pass-through, mechanical-only drops**), `proxy_fetch` (pinned), the `artifact_proxy` view (preflight/fetch modes). Tests: `tests/test_artifact_proxy.py` (39).
- `src/twicc/artifacts/broker_html.py` — `inject_broker_shim`, `ARTIFACT_CSP`, `artifact_html_response`, `is_artifact_document_request`, `BROKER_SHIM_URL`; **phase 5:** `ARTIFACT_INNER_DOC_PATH`, `ARTIFACT_SHELL_JS_URL`/`_CSS_URL`, `artifact_shell_response`/`_shell_html`. Tests: `tests/test_artifact_broker_html.py` (17).
- `src/twicc/views.py` — `_serve_artifact_file` (~1766) wired into `artifact_serve` (~3304, **phase 5: shell / inner-doc / raw branching**)/`file_raw`/`standalone_file_raw`; `artifact_proxy`; `artifact_bookmark_allowed_hosts`; `artifact_broker_shim`; **phase 5: `artifact_shell_asset`** (serves the built shell bundle).
- `src/twicc/core/models.py` — `ArtifactBookmark.allowed_hosts` (JSONField, migration `0109`); `serializers.py` exposes it; `services/artifact_bookmark_mutation.py` — `add/remove_artifact_allowed_host` (lock + broadcast, REST-only); `confined_artifact_path`.
- `src/twicc/urls.py` — `api/artifact-proxy/`, `api/artifact-bookmarks/<id>/allowed-hosts/`, `_twicc/artifact-broker-shim.js`, **`_twicc/artifact-shell/<str:asset>` (phase 5)**, `artifacts/<id>/…` (artifact_serve), `artifacts/auth`. Tests: `tests/test_artifact_bookmarks.py` (incl. shell / inner-doc / non-HTML-direct).

**Frontend:**
- `frontend/src/artifact-broker/shim.js` (3c) — runs in the iframe; intercepts fetch/XHR → `host.proxyFetch` over penpal; wraps broker errors as `TypeError('broker: …')`.
- `frontend/src/artifact-broker/host.js` — `createBrokerHost` (`proxyFetch`: own-dir → `hostDirectFetch` no prompt; else preflight + **consent gate** `gate()` with per-host coalescing + `isAllowed`; same-origin → `hostDirectFetch` authenticated, cross-origin → server proxy) + `mountBrokerHost(iframe, opts)` (penpal parent). Framework-agnostic — both mounts call it.
- `frontend/src/components/artifacts/ArtifactBrokerPrompt.vue` — consent dialog (wa-dialog; **Deny / Forever / This session** + "until you reload this tab" caption; warns when target isn't public). Emits `'deny'|'forever'|'session'`.
- `frontend/src/composables/useArtifactBroker.js` (**phase 5**) — the shared broker wiring (prompt state machine + host mount/teardown + watch); used by both `FilePane.vue` and the shell. No store/router import.
- `frontend/src/components/files/FilePane.vue` — now uses `useArtifactBroker(previewIframeRef, getConfig, [previewIframeRef, htmlPreviewSrc, isHtmlPreviewActive])`; keeps its own `persistBrokerAllow` (via `apiFetch`). Behaviour unchanged.
- `frontend/src/artifact-shell/{main.js,ArtifactShellApp.vue}` (**phase 5**) — the dedicated-page shell app: iframes the inner doc (same sandbox), mounts the same composable + `ArtifactBrokerPrompt.vue`; `persistAllow` = plain same-origin `fetch`.
- Build config: `frontend/vite.config.shim.js` (3c), **`frontend/vite.config.shell.js` (phase 5)**, `vite.config.js` (`/_twicc` dev proxy), `package.json` (build chains shim + shell)/lock. Both shim + shell outputs gitignored.

---

## 6. What was E2E-verified (Claude-in-Chrome, in-SPA preview)

A test artifact (`broker-test.html` in this session's artifacts dir — **kept on purpose**, do not delete; `asset.txt` sibling too) drove every path. Phases 1–4 + consent all green:

- ✅ Cross-origin `example.com` → honest public prompt → Allow → 200 rendered.
- ✅ Same-origin **own asset** (`./asset.txt`) → no prompt, served.
- ✅ **Metadata** (`169.254.169.254`) → blocked at preflight, no prompt.
- ✅ **Same-origin outside dir** (`/rpc/`, `/api/projects/`) → uniform **loopback prompt** → host-direct → authenticated read (RPC catalog).
- ✅ **Header forwarding** (`httpbin.org/headers` with `Authorization` + `X-Twicc-Test`) → both **echoed back** (direct-proxy AND through the shim).
- ✅ **CSP is the real boundary** (button 8/9): a script that restores a native fetch (nested-iframe trick) or opens a `WebSocket` is **blocked by `connect-src`** (confirmed via the browser's `securitypolicyviolation` event, `directive="connect-src"`).
- ✅ **Consent (button 10):** 4 **concurrent** fetches to a fresh host → **ONE** prompt (coalescing); re-run after "This session" → **zero** prompts (in-memory grant). Deny rejects; Forever persists to `allowed_hosts` (DB-verified) + survives reload.
- ✅ Shim reconnects cleanly after a preview reload and after a backend restart. No console errors.

**Known minor limitation (not a bug):** `setupBroker`'s watch depends on the iframe/src, not on `artifactBookmark`, so the host is not re-created on a bookmark change. Mostly mitigated (2026-06-19): the host reads the bookmark id through a **getter** (`getBookmarkId`), so **whether "Forever" is offered** now reflects the live bookmark (bookmark/un-bookmark while previewing → the option appears/disappears with no reload). What still needs a reload: the **seeded persisted allowlist** (`allowedHosts` is still a mount-time snapshot) — so a host you "Forever"-approved in another tab isn't picked up until the artifact reloads. Theoretical async-bookmark-load race; not observed.

**Phase 5 — dedicated page `/artifacts/<id>/` E2E-verified (2026-06-19, Chrome MCP, bookmark id 6):**
- ✅ Shell mounts; artifact renders in the inner iframe; "Shim status: ready".
- ✅ Cross-origin `example.com` via the **server proxy**, with the **persisted "Forever" allowlist seeded into the shell** → no prompt, 200.
- ✅ Own asset host-direct, no prompt. Metadata `169.254.169.254` blocked.
- ✅ **Header forwarding** (`Authorization` + `X-Twicc-Test` echoed) through the proxy.
- ✅ Consent **prompt renders in the shell** (host/ip/kind + warning callout for loopback; Deny/Forever/This session).
- ✅ **Coalescing** (4 concurrent → 1 prompt) + **"This session"** grant (re-fire → 0 prompts).
- ✅ Dialog **closes cleanly** on a visible tab (see the §2 hidden-tab gotcha).
- ✅ **FilePane non-regression**: the in-SPA preview still prompts + closes correctly.

---

## 7. Known deferred / TODO (phase 5 is DONE — see §2)

- ~~**Proxy server-side allowlist re-check (§6.4).**~~ **RESOLVED 2026-06-19 — decided against, won't build.** Evaluated the threat model: the iframe (the only untrusted party) can't reach the proxy (CSP `connect-src 'none'`); the endpoint is auth-gated + `SameSite=Lax` so cross-site forgery can't ride the cookie → only first-party trusted code (the host, which already prompts) calls it; metadata is already re-blocked on the fetch path; and an `allowed_hosts` re-check would break the in-memory "This session"/"once" grants (not persisted) and contradict the consent-is-client-authority model. Residual (first-party XSS → SSRF) accepted. Full rationale written into design §6.4 ("Decision update 2026-06-19"). The `grant:"once"` payload field is now vestigial (proxy ignores it) — left in place, harmless.
- ~~**Phase 6 — docs.**~~ **DONE 2026-06-19 (uncommitted).** Added: (a) `src/twicc/agent/system_prompt.py` Artifacts preamble — a "the page may use the network" note for agents *building* artifacts (plain `fetch`, brokered, per-host consent, headers forwarded, only metadata unreachable); (b) `CLAUDE.md` + `AGENTS.md` — an **Artifact Network Broker** section (invariants/guardrails + design pointer) and `allowed_hosts` on the `ArtifactBookmark` note. **Not** the `twicc-artifacts` skill — it's CLI bookmarking, unrelated to the broker (so no plugin version bump).
- **Hardening — `window.parent` bypass (design §13): accepted residual risk in v1.** `allow-same-origin` lets a malicious artifact reach `window.parent`. The cheap fix (opaque origin + explicit-origin CSP) was **E2E-prototyped 2026-06-19 and works** (hole closed; assets + broker + authenticated API survive) **but kills `localStorage`/`cookie`/IndexedDB**, so it was **reverted** — keep `allow-same-origin`. The real fix = a **separate real origin** for artifacts (keeps storage + closes the hole) but is a genuine project, out of v1. Full write-up in design §13.
- **Timed consent grants** (5 min / 1 h via the value object's `expires_at`) — deferred extension of §9; v1 ships session + forever.

---

## 8. How to resume

1. Read this hand-off + the design doc.
2. `git -C <worktree> log --oneline -8` and `git status --short`: last *commit* is the docs `414cf8b4`; **the phase-5 diff is UNCOMMITTED in the working tree** (backend + `useArtifactBroker` + `artifact-shell/` + vite config + this doc). Run the tests to confirm green: `TWICC_DATA_DIR=$PWD .venv/bin/python -m pytest -q` (644 passing).
3. Confirm servers: `curl -s http://localhost:3502/_twicc/artifact-shell/shell.js | head -c 40` (should be JS) — if down/404, restart per §3 (a `.py` change needs a backend restart; a frontend-bundle change needs `npm run build` only).
4. **Commit phase 5 when the user asks.** Then: proxy server-side re-check (§6.4/§7) → phase 6 docs.
5. To re-run the dedicated-page E2E: open `/artifacts/<id>/` (e.g. `/artifacts/6/`) **in a foreground tab** (the §2 hidden-tab animation gotcha) and exercise the broker-test buttons.
