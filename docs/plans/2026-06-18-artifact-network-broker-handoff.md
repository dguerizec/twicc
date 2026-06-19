# Artifact Network Broker — Implementation Hand-off

**Read this with the design doc** [`2026-06-18-artifact-network-broker-design.md`](2026-06-18-artifact-network-broker-design.md) (same dir). The design doc holds the *what/why* (and is **up to date**). This hand-off holds the *implementation status, the non-obvious operational knowledge, and the next steps*.

**Worktree:** `/home/twidi/dev/twicc-poc/.worktrees/artifacts-fetch`, branch `artifacts-fetch` (off `main`). All work lives here. Servers run: backend `:3502`, frontend `:5175`.

---

## 1. Status at a glance

| Phase | What | State |
|---|---|---|
| Design | doc + decisions O1–O5 + refinements | ✅ committed `462acd90` (+ later edits folded into phase-4 commit) |
| 1 | server proxy + IP guard (`classify_ip`, `resolve_target`, pinned `proxy_fetch`, `/api/artifact-proxy/`) | ✅ committed `1dc326a5` |
| 2 | `ArtifactBookmark.allowed_hosts` + migration 0109 + serializer + REST mutation endpoint/service | ✅ committed `2ab97b3f` |
| 3a/b | broker HTML serving: shim injection + strict CSP, wired into `artifact_serve` + `file_raw`/`standalone_file_raw` | ✅ committed `44dc128a` |
| 3c | shim bundle (penpal + @mswjs/interceptors → IIFE) built + served at `/_twicc/artifact-broker-shim.js` | ✅ committed `0b8f65e3` |
| 4 | **the host** (broker client): `host.js` + `ArtifactBrokerPrompt.vue` + FilePane integration — **E2E-verified via Claude-in-Chrome** | ✅ committed (HEAD) |
| 4-policy | header **pass-through** + same-origin **promptable host-direct** (the model finalization, see §4 below) | ✅ committed (HEAD, same commit as 4) |
| 4-rest | proxy server-side allowlist re-check (defense-in-depth, §6.4) | ⬜ **deferred — not done** |
| 5 | dedicated-page shell (`/artifacts/<id>/` → shell that iframes the artifact) | ⬜ not started |
| 6 | docs (CLAUDE.md/AGENTS.md broker posture, SKILLS-AND-CLI, artifacts skill) | ⬜ not started |

---

## 2. THE IMMEDIATE NEXT STEP (resume here)

**Phases 1–4 are done, committed, and E2E-verified.** The broker works end-to-end: shim → host → (host-direct | server proxy). Remaining work, in order:

1. **Proxy server-side allowlist re-check (§7)** — the one real gap: the proxy enforces the metadata block + pin, but does **not** re-validate the cross-origin target against the bookmark's `allowed_hosts` (the host gates client-side only). Wire it as defense-in-depth + tests.
2. **Phase 5 — dedicated-page shell** (`/artifacts/<id>/`). Until it exists, a *dedicated-page* artifact has no host (window.parent is itself) so its `fetch` fails cleanly via CSP. In-SPA preview is fully working.
3. **Phase 6 — docs** (CLAUDE.md/AGENTS.md broker posture; artifacts skill).

Nothing is half-built or untested at this point. Pick up at (1).

---

## 3. CRITICAL operational knowledge (cost real time to learn)

### Python changes need a BACKEND RESTART — the frontend does not
The backend (uvicorn) does **NOT** auto-reload on `.py` edits: a running server keeps the **old** code. Frontend source (`host.js`, `.vue`) **HMRs live** on `:5175`. So after editing any backend `.py` (e.g. `proxy.py`) you **must restart the backend** or the change is invisible. *This bit during E2E:* the header-forwarding test reported `Authorization echoed: false` because the server was still running the old allowlist code — a restart fixed it. (Symptom of stale proxy code: only `accept`/`content-type` get forwarded — the old allowlist set.)

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
The hatchling build hook (`hatch_build.py`) **SKIPS `npm ci` + `npm run build` if `src/twicc/static/frontend/index.html` exists** — so a devctl restart does **NOT** rebuild the frontend. To rebuild the **shim** bundle (the only frontend piece not HMR'd — it's served from the built static file):
```bash
cd frontend && npm run build   # = vite build && vite build --config vite.config.shim.js
```
The shim output `src/twicc/static/artifact-broker/shim.js` is **gitignored**. Deps `penpal@7.0.6` + `@mswjs/interceptors@0.41.9` are in `package.json`/lock; `npm ci` if missing.

### Migrations
`0109_artifactbookmark_allowed_hosts` exists and is applied. devctl auto-applies migrations at backend startup — **never `migrate` by hand.**

---

## 4. Key design decisions (one-liners — full rationale in the design doc)

- **The one rule (§3.5, §6.2, §14 O3):** only the cloud metadata address (`169.254.169.254` + `fd00:ec2::254`) is **ever** hard-blocked. *Every* other target — `localhost`, LAN, public, even TwiCC's own API — is reachable with **informed per-host:port user consent**. The server resolves the hostname, **pins** the IP, follows no redirects, and the prompt shows the **true** resolved target so a name can't masquerade as an internal IP. No range-blocking, no flag, no deployment detection — single-user self-hosted, the operator clicking the prompt *is* the authority.
- **Header policy = PASS-THROUGH (§6.5):** the broker forwards the artifact's headers **verbatim** (incl. `Authorization` + custom). It imposes **nothing** — deciding what an artifact may send would be an arbitrary dev limit. Only *mechanical* headers are dropped: req `host` (we set the real vhost) + `content-length` (httpx recomputes) + hop-by-hop; resp `content-length` + `content-encoding` (httpx already decoded the body) + hop-by-hop. The TwiCC cookie never reaches the proxy anyway (a `fetch` doesn't expose it in `Request.headers`; JS can't set `Cookie`).
- **Same-origin (§6.6) — no special case, only execution differs:** the artifact's **own files** (its dir) auto-serve **host-direct, no prompt**. **Any other same-origin URL** (TwiCC's own `/api`, `/rpc`) is **promptable like everything else** and runs **host-direct** — i.e. the browser does the fetch, so it **attaches your TwiCC session** (authenticated; writes via cookie, reads via the API's token, the artifact's `Authorization` header forwarded). The prompt is **uniform**: it shows `localhost:5175 → 127.0.0.1 (loopback)`, no "TwiCC" label (we don't guess the app per port). **Cross-origin** goes through the pinning **server proxy** instead.
- **Allowlist (§6.4, §10):** `ArtifactBookmark.allowed_hosts` = JSON **dict** keyed by normalized `scheme://host:port`, value `{ "kind": "public"|"loopback"|"lan" }`. **Port-by-port.** Value is an object from the start (extensible w/o migration). Mutation is **REST-only / never agent-facing**.
- **Rebinding re-prompt (§6.2):** "allow forever" stores the approved `kind`; on every fetch the host re-resolves; a kind change (`public`→`loopback`/`lan`) re-prompts. **In v1.**
- **Shim vs CSP (§7, §8.4):** the injected shim (`@mswjs/interceptors` + `penpal`) is **DX, not the boundary**. The boundary is the header CSP `connect-src 'none'`. The shim installs its interceptor **only if a host answers** — no host ⇒ fetch falls to the CSP (clean failure).
- **Doc-vs-asset detection (§8.3):** `artifact_serve` → `asset == ""`; `file_raw`/`standalone_file_raw` → `Sec-Fetch-Dest` (`iframe`/`document` ⇒ wrap, else raw).

---

## 5. File map of what's implemented

**Backend (Python):**
- `src/twicc/artifacts/proxy.py` — `classify_ip`, `resolve_target` (resolve+pin), `normalize_host_key`, `filter_request_headers`/`filter_response_headers` (**pass-through, mechanical-only drops** — `_HOP_BY_HOP`/`_REQUEST_DROP`/`_RESPONSE_DROP`), `proxy_fetch` (pinned), the `artifact_proxy` view (preflight/fetch modes). Tests: `tests/test_artifact_proxy.py` (39).
- `src/twicc/artifacts/broker_html.py` — `inject_broker_shim`, `ARTIFACT_CSP`, `artifact_html_response`, `is_artifact_document_request`, `BROKER_SHIM_URL`. Tests: `tests/test_artifact_broker_html.py` (13).
- `src/twicc/views.py` — `_serve_artifact_file` wired into `artifact_serve`/`file_raw`/`standalone_file_raw`; `artifact_proxy`; `artifact_bookmark_allowed_hosts`; `artifact_broker_shim` (serves the built bundle).
- `src/twicc/core/models.py` — `ArtifactBookmark.allowed_hosts` (JSONField, migration `0109`); `serializers.py` exposes it; `services/artifact_bookmark_mutation.py` — `add/remove_artifact_allowed_host` (lock + broadcast, REST-only).
- `src/twicc/urls.py` — `api/artifact-proxy/`, `api/artifact-bookmarks/<id>/allowed-hosts/`, `_twicc/artifact-broker-shim.js`. Tests: `tests/test_artifact_bookmarks.py`.

**Frontend:**
- `frontend/src/artifact-broker/shim.js` (3c) — runs in the iframe; intercepts fetch/XHR → `host.proxyFetch` over penpal; wraps broker errors as `TypeError('broker: …')`.
- `frontend/src/artifact-broker/host.js` — `createBrokerHost` (`proxyFetch`: own-dir → `hostDirectFetch` no prompt; else preflight + consent; **same-origin → `hostDirectFetch` authenticated**, cross-origin → server proxy) + `mountBrokerHost(iframe, opts)` (penpal parent).
- `frontend/src/components/artifacts/ArtifactBrokerPrompt.vue` — consent dialog (wa-dialog; once/forever/deny; warns when target isn't public).
- `frontend/src/components/files/FilePane.vue` — mounts the host on the preview iframe (`previewIframeRef`, `brokerPrompt`, `watch`/`onBeforeUnmount` setup/teardown, `persistBrokerAllow`).
- Build config (3c): `frontend/vite.config.shim.js`, `vite.config.js` (`/_twicc` dev proxy), `package.json`/lock.

---

## 6. What was E2E-verified (Claude-in-Chrome, in-SPA preview)

A test artifact (`broker-test.html` in this session's artifacts dir — **kept on purpose**, do not delete) drove every path:

- ✅ Cross-origin `example.com` → honest public prompt → Allow once → 200 rendered.
- ✅ Same-origin **own asset** (`./asset.txt`) → no prompt, served.
- ✅ **Metadata** (`169.254.169.254`) → blocked at preflight, no prompt.
- ✅ **Same-origin outside dir** (`/rpc/`, `/api/projects/`) → uniform **loopback prompt** → host-direct → authenticated read (RPC catalog). *(This is the post-reversal behavior — previously hard-blocked.)*
- ✅ **Header forwarding** (`httpbin.org/headers` with `Authorization` + `X-Twicc-Test`) → both **echoed back** (verified both direct-proxy and through the shim).
- ✅ Deny → fetch rejects; Allow always → persists to `allowed_hosts` (DB-verified, correct normalized key + object value), no re-prompt in-session AND on fresh mount.
- ✅ Shim reconnects cleanly after a preview reload and after a backend restart. No console errors.

**Known minor limitation (not a bug):** `setupBroker`'s watch depends on the iframe/src, not on `artifactBookmark`. So bookmarking *while* previewing requires a reload before "Allow always" is offered / the persisted allowlist is picked up — because the shim handshakes once, so the host can only safely re-mount on an iframe reload. Theoretical async-bookmark-load race; not observed. Possible follow-up: reload the iframe when the bookmark id appears.

---

## 7. Known deferred / TODO

- **Proxy server-side allowlist re-check (§6.4) — THE next task.** The proxy does metadata-block + pin + fetch but does **not** re-validate the target against the bookmark's `allowed_hosts`. The host gates client-side. Add a server-side check in `artifact_proxy` fetch mode (load bookmark, normalize key, require membership) + tests. (Defense-in-depth; the iframe can't reach the proxy anyway thanks to CSP, but the proxy shouldn't be a confused deputy.)
- **Phase 5 — dedicated-page shell:** `/artifacts/<id>/` must serve a trusted **shell** that iframes the real artifact and mounts the same `host.js` (reuse `mountBrokerHost`), instead of injecting the shim into the artifact *as the top-level page* (where there is no host).
- **Phase 6 — docs:** CLAUDE.md + AGENTS.md (broker posture: widgets use plain `fetch`, it's brokered, header pass-through, only metadata blocked), the artifacts skill.
- **Hardening (design §13):** isolating artifacts on a dedicated origin (so `window.parent` is cross-origin/unreadable). Out of v1 scope.

---

## 8. How to resume

1. Read this hand-off + the design doc.
2. `git -C <worktree> log --oneline -8` and `git status --short` to confirm state.
3. Confirm servers: `curl -s http://localhost:3502/_twicc/artifact-broker-shim.js | head -c 40` (should be JS) — if down, restart per §3 (mind the zombie; remember a backend restart is needed for any `.py` change).
4. Start on §7 item 1 (proxy server-side allowlist re-check), then phase 5, then phase 6.
