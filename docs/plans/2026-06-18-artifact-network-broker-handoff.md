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
| **5** | **dedicated-page shell, UNIFIED with the in-SPA preview** (`/artifacts/<id>/`) | ⬜ **NEXT — user asked to do it now (see §2)** |
| 4-rest | proxy server-side allowlist re-check (defense-in-depth, §6.4) | ⬜ deferred |
| 6 | docs (CLAUDE.md/AGENTS.md broker posture, artifacts skill) | ⬜ not started |

`git log --oneline`: a docs-only commit (this hand-off + the §9 shared-wiring note) sits on top of `f42c96da` (consent) · `35163802` (origin-isolation doc) · `0fe6ed64` (phase 4 + policy) · `0b8f65e3` (3c) · `44dc128a` (3a/b) · `2ab97b3f` (2) · `1dc326a5` (1). The last *feature* commit is `f42c96da`. Run `git log` for the true HEAD; working tree clean.

---

## 2. THE IMMEDIATE NEXT STEP (resume here) — Phase 5, the UNIFIED dedicated-page shell

**User directive (2026-06-19):** make an artifact behave **identically** whether it runs in the in-SPA preview *or* in its isolated page (`/artifacts/<id>/`). The trusted **shell** (iframe-the-artifact + mount the broker host + show the prompt) "should have been done the same on both sides" — i.e. **one shared shell**, not two parallel implementations. Do it **now**, before things diverge further. *(I had only started reading the code when the user interrupted to compact — no phase-5 code written yet. Resume by presenting the plan + confirming the one open decision below, then implement.)*

### Why it's needed (today's asymmetry)
- **In-SPA preview (works):** `FilePane.vue` renders the artifact in an `<iframe>` (backend-served, shim+CSP injected) and mounts the broker **host** on it (`mountBrokerHost`) + renders `<ArtifactBrokerPrompt>`. The host lives in the parent (the SPA) → broker works.
- **Dedicated page (broker dead):** `artifact_serve` (`/artifacts/<id>/`, `asset==""`) serves the artifact **as the top-level document** (shim+CSP injected). There is **no host** — `window.parent` is itself → the shim's `connect()` finds no host → it never installs its interceptor → the artifact's `fetch` falls straight to the CSP `connect-src 'none'` → **blocked**. So a dedicated-page artifact cannot use the network at all.

### The fix
**Backend** (`views.py` `artifact_serve` ~3304, `_serve_artifact_file` ~1766, `urls.py` artifact routes 112–119):
- `/artifacts/<id>/` (`asset==""`) → serve a **trusted shell page** (TwiCC HTML), **not** the artifact.
- Add an **inner-doc route** (sentinel, e.g. `/artifacts/<id>/_doc`) that serves the artifact's **root file as a document** (the current `asset==""` behavior: `_serve_artifact_file(root, as_document=True)`).
- The shell's `<iframe src>` points at the inner-doc URL. The artifact's relative assets (`./x.css`) resolve to `/artifacts/<id>/x.css` → the existing **sub-asset** route (`asset!=""` → raw). Pick a sentinel that can't collide with a real asset name.
- Auth already handled: `PasswordAuthMiddleware` gates `/artifacts/`; the page holds the session cookie (so the shell can POST "Forever" to `/api/artifact-bookmarks/<id>/allowed-hosts/`). CSP already has `frame-src 'self'` → the shell may iframe the same-origin inner doc.

**Frontend (the unification):** extract `FilePane.vue`'s inline broker wiring — `mountBrokerHost` + `showBrokerPrompt`/`onBrokerDecision`/`persistBrokerAllow` + `setupBroker`/`teardownBroker` watch + the `<ArtifactBrokerPrompt>` — into **one shared composable** `useArtifactBroker(iframeRef, opts)`. Then:
- `FilePane.vue` refactors to use the composable (no behaviour change).
- The **dedicated shell** is a small page that mounts the **same** composable + `<ArtifactBrokerPrompt>` around its inner iframe. So both sides are literally the same broker shell — the user's requirement.

### The one open decision (confirm before building)
**How to build/serve the shell.** It is a separate Django-served page (not the SPA — `/artifacts/` is outside the SPA catch-all). Options:
- **(A, faithful to "same on both sides")** a small **Vite-built Vue bundle** that mounts a tiny app: the inner iframe + `useArtifactBroker` + `<ArtifactBrokerPrompt>`. Reuses the exact Vue composable + dialog. Cost: the bundle pulls in Vue + the Web Awesome dialog/button (register them). Mirror the shim's build setup (`vite.config.shim.js` → add a `vite.config.shell.js`, served from a static file like the shim).
- **(B, lighter)** a Django template + vanilla JS shell that reuses `host.js` (framework-agnostic, already shared) but **re-implements** the prompt in plain HTML. Smaller, but two prompt implementations → drifts from the in-SPA one, which is exactly what the user wants to avoid.

**Recommend (A)** to honour "pareil des deux côtés"; confirm the bundle-weight trade-off with the user first.

### Scope notes
- The dedicated page is **bookmarked-only** (it has an `<id>`). **Non-bookmarked** artifacts stay **in-SPA-preview only** (unchanged). The design already specifies all this: §5 (two run contexts), §6.1 O1 ("implemented together", inner-doc path is cosmetic), §9 ("two mounts, host.js shared").
- This was always meant to ship *with* phase 4 (O1) but slipped; doing it now keeps the two contexts from diverging.

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
The hatchling build hook (`hatch_build.py`) **SKIPS `npm ci` + `npm run build` if `src/twicc/static/frontend/index.html` exists** — so a devctl restart does **NOT** rebuild the frontend. To rebuild the **shim** bundle (the only frontend piece not HMR'd — it's served from the built static file; the future **shell** bundle, phase 5 option A, will be the same):
```bash
cd frontend && npm run build   # = vite build && vite build --config vite.config.shim.js
```
The shim output `src/twicc/static/artifact-broker/shim.js` is **gitignored**. Deps `penpal@7.0.6` + `@mswjs/interceptors@0.41.9` are in `package.json`/lock; `npm ci` if missing.

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
- `src/twicc/artifacts/broker_html.py` — `inject_broker_shim`, `ARTIFACT_CSP`, `artifact_html_response`, `is_artifact_document_request`, `BROKER_SHIM_URL`. Tests: `tests/test_artifact_broker_html.py` (13).
- `src/twicc/views.py` — `_serve_artifact_file` (~1766) wired into `artifact_serve` (~3304)/`file_raw`/`standalone_file_raw`; `artifact_proxy`; `artifact_bookmark_allowed_hosts`; `artifact_broker_shim`. *(Phase 5 touches `artifact_serve` + adds an inner-doc route.)*
- `src/twicc/core/models.py` — `ArtifactBookmark.allowed_hosts` (JSONField, migration `0109`); `serializers.py` exposes it; `services/artifact_bookmark_mutation.py` — `add/remove_artifact_allowed_host` (lock + broadcast, REST-only); `confined_artifact_path`.
- `src/twicc/urls.py` — `api/artifact-proxy/`, `api/artifact-bookmarks/<id>/allowed-hosts/`, `_twicc/artifact-broker-shim.js`, `artifacts/<id>/…` (artifact_serve), `artifacts/auth`. Tests: `tests/test_artifact_bookmarks.py`.

**Frontend:**
- `frontend/src/artifact-broker/shim.js` (3c) — runs in the iframe; intercepts fetch/XHR → `host.proxyFetch` over penpal; wraps broker errors as `TypeError('broker: …')`.
- `frontend/src/artifact-broker/host.js` — `createBrokerHost` (`proxyFetch`: own-dir → `hostDirectFetch` no prompt; else preflight + **consent gate** `gate()` with per-host coalescing + `isAllowed`; same-origin → `hostDirectFetch` authenticated, cross-origin → server proxy) + `mountBrokerHost(iframe, opts)` (penpal parent). Framework-agnostic — both mounts call it.
- `frontend/src/components/artifacts/ArtifactBrokerPrompt.vue` — consent dialog (wa-dialog; **Deny / Forever / This session** + "until you reload this tab" caption; warns when target isn't public). Emits `'deny'|'forever'|'session'`.
- `frontend/src/components/files/FilePane.vue` — mounts the host on the preview iframe (`previewIframeRef`, `brokerPrompt`, `showBrokerPrompt`/`onBrokerDecision`/`persistBrokerAllow`, `setupBroker`/`teardownBroker` watch + `onBeforeUnmount`). **Phase 5 extracts this wiring into a shared `useArtifactBroker` composable.**
- Build config (3c): `frontend/vite.config.shim.js`, `vite.config.js` (`/_twicc` dev proxy), `package.json`/lock.

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

**Known minor limitation (not a bug):** `setupBroker`'s watch depends on the iframe/src, not on `artifactBookmark`. So bookmarking *while* previewing requires a reload before "Forever" is offered / the persisted allowlist is picked up (the shim handshakes once → host can only safely re-mount on an iframe reload). Theoretical async-bookmark-load race; not observed.

---

## 7. Known deferred / TODO (besides phase 5 in §2)

- **Proxy server-side allowlist re-check (§6.4).** The proxy does metadata-block + pin + fetch but does **not** re-validate the target against the bookmark's `allowed_hosts` (the host gates client-side). Add a server-side check in `artifact_proxy` fetch mode (load bookmark, normalize key, require membership / a valid once-grant — mind that "This session" grants are **not** persisted, so the proxy can't see them; the `grant:'once'` field carries the host's decision) + tests. Defense-in-depth (the iframe can't reach the proxy anyway thanks to CSP, but the proxy shouldn't be a confused deputy).
- **Phase 6 — docs:** CLAUDE.md + AGENTS.md (broker posture: widgets use plain `fetch`, it's brokered, header pass-through, only metadata blocked), the artifacts skill.
- **Hardening — `window.parent` bypass (design §13): accepted residual risk in v1.** `allow-same-origin` lets a malicious artifact reach `window.parent`. The cheap fix (opaque origin + explicit-origin CSP) was **E2E-prototyped 2026-06-19 and works** (hole closed; assets + broker + authenticated API survive) **but kills `localStorage`/`cookie`/IndexedDB**, so it was **reverted** — keep `allow-same-origin`. The real fix = a **separate real origin** for artifacts (keeps storage + closes the hole) but is a genuine project, out of v1. Full write-up in design §13.
- **Timed consent grants** (5 min / 1 h via the value object's `expires_at`) — deferred extension of §9; v1 ships session + forever.

---

## 8. How to resume

1. Read this hand-off + the design doc.
2. `git -C <worktree> log --oneline -8` and `git status --short` to confirm state (clean; last feature commit `f42c96da`, a docs-only commit on top).
3. Confirm servers: `curl -s http://localhost:3502/_twicc/artifact-broker-shim.js | head -c 40` (should be JS) — if down, restart per §3 (mind the zombie; a backend restart is needed for any `.py` change).
4. **Do §2 — phase 5 (unified dedicated-page shell).** Present the plan + confirm the open decision (shell build A vs B), then implement: backend shell + inner-doc route, extract `useArtifactBroker`, build the shell, refactor FilePane, E2E-verify **both** contexts (open `/artifacts/<id>/` in a tab → the broker prompt must work there too).
5. Then: proxy server-side re-check → phase 6 docs.
