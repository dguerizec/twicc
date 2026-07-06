# TwiCC MCP Server — Manual / Agent Test Plan

**Goal:** validate the `/mcp` MCP server end-to-end on the `mcp` branch — the parts the
pytest suite (903 tests, all green) cannot cover because they need a *live backend* and
*real spawned agent sessions*.

**Who runs this:** an agent (or a human) working **inside the `mcp` worktree** with shell
access. This document is fully self-contained: it assumes no prior context. Follow it top
to bottom, record PASS/FAIL for each step in the results table at the end.

**Design recap (what you are validating):**
- `/mcp` is a Streamable-HTTP MCP endpoint served by TwiCC's own backend, mounted as raw
  ASGI in front of Django. It exposes the skill-covered CLI surface as MCP tools
  (`mcp__twicc__<command>` on Claude; names use `_` for `/` and `-`).
- Every request needs a Bearer token: a per-session token (`twicc_mcp_<sid>.<sig>`) or a
  PAT (`twicc_pat_…`). No token → 401.
- Tools run **in-process**: reads via the RPC invoker, writes through the dual-mode
  drop-request transport straight into the service handlers — **no drop files**.
- Every tool is available in **every permission mode** and **auto-approved** (no prompt) on
  both providers — it is a control plane, orthogonal to the project's permissions (D9).

---

## 0. Preconditions & setup

> ⚠️ Run **every** command from the worktree root, and set `TWICC_DATA_DIR=$PWD` for any
> ad-hoc Python (otherwise it resolves the *prod* `~/.twicc/` data dir — see CLAUDE.md).

### 0.1 — Confirm you are in the worktree on the `mcp` branch

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/mcp
git rev-parse --abbrev-ref HEAD      # expect: mcp
pwd                                   # expect: /home/twidi/dev/twicc-poc/.worktrees/mcp
```

### 0.2 — Ensure the worktree backend is running (with the new `mcp` dep)

The `mcp>=1.27` dependency was added to `pyproject.toml`; devctl's editable rebuild picks it
up on `start`. Check if the backend is up, and start it if not:

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/mcp
uv run ./devctl.py status
# If backend is not running:
uv run ./devctl.py start all
# Then confirm readiness via the backend log (initial sync can delay the port check):
uv run ./devctl.py logs back --lines=40
```

Expected in `backend.log`: a line `MCP server ready at /mcp`. If instead you see
`MCP server disabled (TWICC_NO_MCP)`, unset that env and restart.

> If you are an agent and are **not** authorized to start servers, stop here and ask the
> human to run `devctl.py start all` in the worktree, then resume.

### 0.3 — Resolve the port, the CLI, and export helpers

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/mcp
# Backend port: devctl writes it into the worktree .env (worktrees default to 3501).
export PORT=$(grep -E '^TWICC_PORT=' .env | cut -d= -f2)
echo "PORT=$PORT"                     # expect a number, e.g. 3501
export MCP_URL="http://127.0.0.1:$PORT/mcp"

# Resolve the twicc CLI (do NOT quote $TWICC when using it — it may be multi-word).
export TWICC="uv run --active twicc"
$TWICC whoami >/dev/null 2>&1; echo "twicc resolves (exit $?)"

# Resolve THIS worktree's project id (needed to spawn sessions bound to it in Part D).
export PROJECT_ID=$($TWICC projects | TWICC_DATA_DIR=$PWD uv run --active python -c \
  "import sys,json,os; d=json.load(sys.stdin); print(next(p['id'] for p in d if p.get('directory')==os.getcwd()))")
echo "PROJECT_ID=$PROJECT_ID"
```

### 0.4 — Helper: mint a session token for an arbitrary session id

The backend signs tokens with `<data_dir>/mcp-secret`. Minting with `TWICC_DATA_DIR=$PWD`
reads the *same* file, so the token is valid against the running backend.

```bash
mint_token() {  # usage: mint_token <session_id>
  TWICC_DATA_DIR=$PWD uv run --active python -c \
    "import sys; from twicc.mcp.identity import mint_session_token; print(mint_session_token(sys.argv[1]))" "$1"
}
```

---

## Part A — Endpoint auth & protocol

### A1 — Unauthenticated request is 401

```bash
curl -s -o /dev/null -w '%{http_code}\n' -X POST "$MCP_URL" \
  -H 'content-type: application/json' \
  -H 'accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"t","version":"0"}}}'
```
**Expect:** `401`.

### A2 — Bad/tampered token is 401

```bash
curl -s -o /dev/null -w '%{http_code}\n' -X POST "$MCP_URL" \
  -H 'authorization: Bearer twicc_mcp_whatever.deadbeef' \
  -H 'content-type: application/json' -H 'accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"t","version":"0"}}}'
```
**Expect:** `401`.

### A3 — A PAT (API token) passes auth

```bash
# Create a PAT (prints the one-time secret as plain text). Save it.
$TWICC token create mcp-test-plan
# Copy the printed twicc_pat_... value into PAT below:
export PAT='twicc_pat_REPLACE_ME'
curl -s -o /dev/null -w '%{http_code}\n' -X POST "$MCP_URL" \
  -H "authorization: Bearer $PAT" \
  -H 'content-type: application/json' -H 'accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"t","version":"0"}}}'
```
**Expect:** `200`. (Clean up later: `$TWICC token revoke <id>` — list with `$TWICC token list`.)

### A4 — Full protocol roundtrip with a session token

Write and run this helper script (uses a persistent HTTP connection, as a real client does):

```bash
cat > /tmp/mcp_roundtrip.py <<'PY'
import os, sys, httpx, json
url = os.environ["MCP_URL"]
tok = sys.argv[1]
H = {"authorization": f"Bearer {tok}", "content-type": "application/json",
     "accept": "application/json, text/event-stream"}
def rpc(c, method, params=None, _id=1):
    msg = {"jsonrpc": "2.0", "method": method}
    if params is not None: msg["params"] = params
    if _id is not None: msg["id"] = _id
    r = c.post(url, json=msg, headers=H)
    return r
with httpx.Client(timeout=30) as c:
    r = rpc(c, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                              "clientInfo": {"name": "plan", "version": "0"}})
    print("initialize:", r.status_code, r.json()["result"]["serverInfo"]["name"])
    rpc(c, "notifications/initialized", _id=None)
    r = rpc(c, "tools/list", {}, 2)
    names = [t["name"] for t in r.json()["result"]["tools"]]
    print("tools/list:", r.status_code, "count =", len(names))
    print("has whoami:", "whoami" in names, "| has create_session:", "create_session" in names)
    print("settings excluded:", not any(n.startswith("settings") for n in names))
    r = rpc(c, "tools/call", {"name": "workspaces", "arguments": {}}, 3)
    sc = r.json()["result"]["structuredContent"]
    print("tools/call workspaces exit_code:", sc["exit_code"])
PY
MCP_URL="$MCP_URL" TWICC_DATA_DIR=$PWD uv run --active python /tmp/mcp_roundtrip.py "$(mint_token some-plan-session)"
```
**Expect:** `initialize: 200 twicc`; `count = 57`; `has whoami: True | has create_session: True`;
`settings excluded: True`; `tools/call workspaces exit_code: 0`.

### A5 — Tool-set sanity (offline, cross-check)

```bash
TWICC_DATA_DIR=$PWD uv run --active python -c "
from twicc.mcp.tools import iter_mcp_tools
ts = iter_mcp_tools(); names = {t.name for t in ts}
print('total:', len(ts))                       # expect 57
print('settings excluded:', not any(n.startswith('settings') for n in names))
for banned in ('password','token','run','claude','codex'):
    assert banned not in names, banned
print('local-only excluded: True; whoami present:', 'whoami' in names)
"
```
**Expect:** `total: 57`, all `True`.

### A6 — Kill switch `TWICC_NO_MCP` (requires a restart; do LAST or skip)

Only if you can restart the worktree backend with an extra env var. Set `TWICC_NO_MCP=1`
in the worktree `.env`, restart, then repeat A1 — **expect `503`** (`MCP server not available`).
**Revert the `.env` change and restart afterwards.** If you cannot safely restart, mark SKIPPED.

---

## Part B — In-process execution / no drop files

### B1 — An MCP mutation creates no drop file

```bash
# Snapshot the drop dir, run a mutation tool, confirm nothing was written.
ls -1 drop-requests/ 2>/dev/null | wc -l           # baseline count
cat > /tmp/mcp_mutation.py <<'PY'
import os, sys, httpx
url = os.environ["MCP_URL"]; tok = sys.argv[1]
H = {"authorization": f"Bearer {tok}", "content-type": "application/json",
     "accept": "application/json, text/event-stream"}
with httpx.Client(timeout=30) as c:
    c.post(url, json={"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"p","version":"0"}}}, headers=H)
    c.post(url, json={"jsonrpc":"2.0","method":"notifications/initialized"}, headers=H)
    r = c.post(url, json={"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"create_workspace","arguments":{"name":"mcp-smoke-ws"}}}, headers=H)
    print(r.json()["result"]["structuredContent"])
PY
MCP_URL="$MCP_URL" TWICC_DATA_DIR=$PWD uv run --active python /tmp/mcp_mutation.py "$(mint_token some-plan-session)"
ls -1 drop-requests/ 2>/dev/null | wc -l           # must equal baseline
```
**Expect:** the tool result shows `'status': 'created'` and a `workspace_id`; the drop-dir
count is **unchanged**. Verify the workspace exists, then delete it:
```bash
$TWICC workspaces | grep -i mcp-smoke-ws
$TWICC delete-workspace mcp-smoke-ws          # local-mode cleanup (uses a drop file — that is Part E)
```

---

## Part C — Identity binding

### C1 — A session token resolves to that session in `whoami`

Pick any real session id (from `$TWICC sessions`), mint a token for it, and call `whoami`:
```bash
SID=$($TWICC sessions | TWICC_DATA_DIR=$PWD uv run --active python -c "import sys,json; print(json.load(sys.stdin)[0]['id'])")
echo "SID=$SID"
cat > /tmp/mcp_whoami.py <<'PY'
import os, sys, httpx
url=os.environ["MCP_URL"]; tok=sys.argv[1]
H={"authorization":f"Bearer {tok}","content-type":"application/json","accept":"application/json, text/event-stream"}
with httpx.Client(timeout=30) as c:
    c.post(url, json={"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"p","version":"0"}}}, headers=H)
    c.post(url, json={"jsonrpc":"2.0","method":"notifications/initialized"}, headers=H)
    r=c.post(url, json={"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"whoami","arguments":{}}}, headers=H)
    sc=r.json()["result"]["structuredContent"]
    print("exit:", sc["exit_code"], "| session:", (sc.get("result") or {}).get("session",{}).get("id"))
PY
MCP_URL="$MCP_URL" TWICC_DATA_DIR=$PWD uv run --active python /tmp/mcp_whoami.py "$(mint_token "$SID")"
```
**Expect:** `exit: 0 | session: <SID>` (the whoami tool resolved the identity carried by the token).

---

## Part D — Provider wiring (real spawned sessions)

> These require spawning real agent sessions via `twicc create-session` (the PROMPT is a
> **positional argument**, not a flag). Every spawned prompt below is self-contained. After
> each, read the child's output with `$TWICC session <child_id> messages` (JSON array — look
> at the last entries) and confirm the tool ran **without any pending approval** (if the
> result JSON is present, it was auto-approved). Confirm `drop-requests/` stays empty
> throughout.

### D1 — Claude Code (SDK) session, restrictive mode

```bash
# Spawn a Claude session in DEFAULT mode (NOT bypass) so the permission callback fires.
$TWICC create-session --provider claude_code --permission-mode default --project "$PROJECT_ID" \
  "Call the mcp__twicc__whoami tool. Reply with ONLY the JSON it returns."
```
Note the returned `session_id` (the create-session JSON output), wait a few seconds, then:
```bash
$TWICC session <child_id> messages          # inspect the last assistant message
ls -1 drop-requests/ | wc -l                # expect 0
```
**Expect:** the child reports its own `session_id`, **no approval prompt** was raised (the
tool result is present), drop-dir empty. Then repeat with a mutation prompt:
`"Use mcp__twicc__update_session_title to set this session's title to 'mcp-d1' (session self)."`
and confirm the title changes (`$TWICC session <child_id>` shows the new title) with no prompt.

### D2 — Claude Code hybrid (tmux) session

Repeat D1 but with a **hybrid (tmux) Claude session** — this mode is driven by the
`terminalUseTmux` setting / the way your setup launches tmux-CLI sessions, not a
`create-session` flag. Launch one in `default` mode and prompt it to call
`mcp__twicc__whoami`. Same expectations. **This is the key check for the hybrid auto-approve
fix:** the MCP tool must run with **no approval card** in `default` mode.
Also verify token survival across a restart: mid-session, restart the backend
(`devctl.py restart back`), then send the session a new prompt to call `mcp__twicc__whoami`
again — it must still succeed (the token is deterministic, baked into the session's
`mcp-config` file).

### D3 — Codex session, non-yolo mode

```bash
$TWICC create-session --provider codex --permission-mode auto --project "$PROJECT_ID" \
  "Call the twicc whoami MCP tool. Reply with ONLY the session id it returns."
```
**Expect:** the child returns the **canonical** session id (proves the draft→canonical alias),
no approval prompt. Then a mutation:
`"Use the twicc update_session_title tool to set this session's title to 'mcp-d3' (session self)."`
— title changes with **no approval prompt** (validates `default_tools_approval_mode="approve"`),
drop-dir stays empty.

### D4 — Codex read-only / strict mode can still call a write tool (D9)

Spawn a Codex session in `read_only`/strict mode and prompt it to run
`update_session_title` on itself. **Expect:** it works — MCP is a control plane available in
every mode, even though the same session cannot run the `twicc` CLI at all.

---

## Part E — CLI regression (local mode still uses drop files)

From a plain terminal (a real `twicc` process talking to the backend — `backend_loop` unset):
```bash
$TWICC create-workspace mcp-cli-smoke      # should print status: created
$TWICC workspaces | grep -i mcp-cli-smoke
$TWICC delete-workspace mcp-cli-smoke      # status: deleted
```
**Expect:** both succeed. (Optional: while a slow command runs, observe a transient
`<uuid>.json` in `drop-requests/` — the local path is unchanged.)

---

## Part F — Codex context mode (`TWICC_MCP_CODEX_DEFER`)

- **Default (`True`, deferred):** in D3, the Codex thread started cleanly and the `whoami`
  tool resolved on demand → PASS by virtue of D3 passing.
- **Eager fallback:** temporarily set `TWICC_MCP_CODEX_DEFER = False` in
  `src/twicc/mcp/__init__.py`, restart the backend, re-run D3. **Expect:** still works (Codex
  loads all schemas eagerly). **Revert the constant and restart afterwards.**

---

## Part G — Existing surfaces intact (regression)

- SPA loads in the browser at `http://127.0.0.1:<frontend_port>/`.
- `/rpc/` still works: `curl -s -X POST "http://127.0.0.1:$PORT/rpc/status" -H "authorization: Bearer $PAT" | head -c 200`.
- Artifacts serving and the terminal WebSocket still work (open a session's Artifacts and
  Terminal tabs).

---

## Results

| ID | Check | Result | Notes |
|----|-------|--------|-------|
| A1 | 401 no token | | |
| A2 | 401 bad token | | |
| A3 | 200 with PAT | | |
| A4 | protocol roundtrip (57 tools, workspaces exit 0) | | |
| A5 | tool-set sanity (offline) | | |
| A6 | TWICC_NO_MCP → 503 | | (skip if no restart) |
| B1 | MCP mutation, no drop file | | |
| C1 | token → whoami identity | | |
| D1 | Claude SDK default: whoami+mutation, no prompt | | |
| D2 | Claude hybrid: no card + token survives restart | | |
| D3 | Codex auto: canonical id + mutation, no prompt | | |
| D4 | Codex read-only can call a write tool | | |
| E  | CLI local mode still works (drop files) | | |
| F  | Codex defer default + eager fallback | | |
| G  | SPA / /rpc/ / artifacts / terminal intact | | |

**Cleanup checklist:** revert any `.env` (`TWICC_NO_MCP`) and `__init__.py`
(`TWICC_MCP_CODEX_DEFER`) changes + restart; revoke the test PAT (`$TWICC token revoke <id>`);
delete any leftover test workspaces/sessions you created; `rm /tmp/mcp_*.py`.
