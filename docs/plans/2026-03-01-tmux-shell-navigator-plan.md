# Tmux Shell Navigator — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a shell navigator UI to the tmux terminal tab, allowing users to list, create, and switch between named tmux windows via a simple overlay toggled by re-clicking the terminal tab.

**Architecture:** Single WebSocket connection with control messages added to the existing protocol. Backend executes tmux commands (`list-windows`, `new-window`, `select-window`, `rename-window`) via `subprocess.run`. Frontend adds a `TmuxNavigator.vue` overlay to `TerminalPanel.vue`, with control functions in `useTerminal.js`. No DB changes.

**Tech Stack:** Python (ASGI WebSocket handler), Vue 3 (Composition API), Web Awesome 3 components, tmux CLI

---

### Task 1: Backend — tmux window control helpers

**Files:**
- Modify: `src/twicc/terminal.py` (add helpers after `kill_tmux_session` at line ~283)

**Step 1: Add three tmux helper functions**

After `kill_tmux_session()` (line 283), add:

```python
def tmux_list_windows(session_id: str) -> list[dict[str, object]]:
    """List all windows in the tmux session for the given twicc session ID.

    Returns a list of dicts: [{"name": "main", "active": True}, ...]
    Returns an empty list if the session doesn't exist or tmux is not installed.
    """
    tmux_path = get_tmux_path()
    if tmux_path is None:
        return []

    name = tmux_session_name(session_id)
    try:
        result = subprocess.run(
            [tmux_path, "-L", TMUX_SOCKET_NAME, "list-windows",
             "-t", name, "-F", "#{window_name}\t#{window_active}"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            return []
        windows = []
        for line in result.stdout.strip().splitlines():
            parts = line.split("\t")
            if len(parts) == 2:
                windows.append({"name": parts[0], "active": parts[1] == "1"})
        return windows
    except (subprocess.TimeoutExpired, OSError):
        return []


def tmux_create_window(session_id: str, window_name: str) -> bool:
    """Create a new window in the tmux session with the given name.

    Returns True on success, False on failure.
    """
    tmux_path = get_tmux_path()
    if tmux_path is None:
        return False

    session_name = tmux_session_name(session_id)
    try:
        result = subprocess.run(
            [tmux_path, "-L", TMUX_SOCKET_NAME, "new-window",
             "-t", session_name, "-n", window_name],
            capture_output=True, timeout=5,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


def tmux_select_window(session_id: str, window_name: str) -> bool:
    """Switch the active window in the tmux session.

    Returns True on success, False on failure.
    """
    tmux_path = get_tmux_path()
    if tmux_path is None:
        return False

    session_name = tmux_session_name(session_id)
    try:
        result = subprocess.run(
            [tmux_path, "-L", TMUX_SOCKET_NAME, "select-window",
             "-t", f"{session_name}:{window_name}"],
            capture_output=True, timeout=5,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


def tmux_rename_window(session_id: str, target: str, new_name: str) -> bool:
    """Rename a window in the tmux session.

    target can be a window index (e.g., "0") or name.
    Returns True on success, False on failure.
    """
    tmux_path = get_tmux_path()
    if tmux_path is None:
        return False

    session_name = tmux_session_name(session_id)
    try:
        result = subprocess.run(
            [tmux_path, "-L", TMUX_SOCKET_NAME, "rename-window",
             "-t", f"{session_name}:{target}", new_name],
            capture_output=True, timeout=5,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False
```

**Step 2: Commit**

```bash
git add src/twicc/terminal.py
git commit -m "feat(terminal): add tmux window control helper functions"
```

---

### Task 2: Backend — handle control messages in receive_loop

**Files:**
- Modify: `src/twicc/terminal.py` — `terminal_application` function (line ~287)

**Step 1: Rename default window to "main" after tmux spawn**

After the PTY spawn block (line ~342, after `await send({"type": "websocket.accept"})`), add the rename when tmux is used:

```python
    # Rename the default window to "main" for a fresh tmux session
    if use_tmux:
        windows = tmux_list_windows(session_id)
        # Only rename if the first window still has tmux's default name
        if windows and windows[0]["name"] != "main":
            tmux_rename_window(session_id, "0", "main")
```

Place this right after the `await send({"type": "websocket.accept"})` line (345) and before the output_queue/pty_dead setup.

**Step 2: Add control message handling in receive_loop**

In the `receive_loop` function, after the `elif msg_type == "resize":` block (line ~428), add:

```python
                elif msg_type == "list_windows" and use_tmux:
                    windows = tmux_list_windows(session_id)
                    await send({"type": "websocket.send",
                                "text": json.dumps({"type": "windows", "windows": windows})})

                elif msg_type == "create_window" and use_tmux:
                    window_name = msg.get("name", "").strip()
                    if window_name:
                        tmux_create_window(session_id, window_name)
                        windows = tmux_list_windows(session_id)
                        await send({"type": "websocket.send",
                                    "text": json.dumps({"type": "windows", "windows": windows})})

                elif msg_type == "select_window" and use_tmux:
                    window_name = msg.get("name", "")
                    if window_name and tmux_select_window(session_id, window_name):
                        await send({"type": "websocket.send",
                                    "text": json.dumps({"type": "window_changed", "name": window_name})})
```

Note: all three handlers are gated with `and use_tmux` — they're no-ops for raw shell connections.

**Step 3: Update module docstring**

Update the docstring at the top of `terminal.py` (lines 1-18) to document the new protocol messages:

```python
"""
Raw ASGI WebSocket handler for interactive terminal sessions.

...existing docstring...

Protocol:
  Client → Server (JSON text frames):
    { "type": "input", "data": "ls -la\n" }       — keyboard input
    { "type": "resize", "cols": 120, "rows": 30 }  — terminal resize
    { "type": "list_windows" }                      — list tmux windows
    { "type": "create_window", "name": "build" }    — create named window
    { "type": "select_window", "name": "build" }    — switch to window

  Server → Client:
    Plain text frames — raw PTY output (no JSON wrapping for performance).
    JSON text frames (when type field present) — control responses:
      { "type": "windows", "windows": [...] }       — window list
      { "type": "window_changed", "name": "..." }   — window switched
"""
```

**Step 4: Commit**

```bash
git add src/twicc/terminal.py
git commit -m "feat(terminal): handle tmux window control messages in WS handler"
```

---

### Task 3: Frontend — extend useTerminal.js with window control

**Files:**
- Modify: `frontend/src/composables/useTerminal.js`

**Step 1: Add reactive state and control functions**

After the existing variable declarations (line ~100, after `let intentionalClose = false`), add:

```javascript
    // ── tmux window management state ────────────────────────────────────
    const windows = ref([])
    const showNavigator = ref(false)

    /** @type {((windows: Array) => void) | null} — resolver for pending listWindows() call */
    let windowsResolver = null
```

**Step 2: Update ws.onmessage to detect control messages**

Replace the current `ws.onmessage` (line ~159):

```javascript
        ws.onmessage = (event) => {
            const data = event.data
            // Detect JSON control messages from the server
            if (data.charAt(0) === '{') {
                try {
                    const msg = JSON.parse(data)
                    if (msg.type === 'windows') {
                        windows.value = msg.windows
                        if (windowsResolver) {
                            windowsResolver(msg.windows)
                            windowsResolver = null
                        }
                        return
                    }
                    if (msg.type === 'window_changed') {
                        // Update active flag in local list
                        for (const w of windows.value) {
                            w.active = (w.name === msg.name)
                        }
                        showNavigator.value = false
                        return
                    }
                    if (msg.type === 'auth_failure') {
                        // Existing auth failure handling — falls through to terminal.write below
                    }
                } catch {
                    // Not JSON — fall through to terminal.write
                }
            }
            // Raw PTY output
            terminal?.write(data)
        }
```

**Step 3: Add window control functions**

After the `reconnect()` function (line ~404), add:

```javascript
    /**
     * Request the list of tmux windows from the backend.
     * Returns a Promise that resolves with the window list.
     */
    function listWindows() {
        return new Promise((resolve) => {
            windowsResolver = resolve
            wsSend({ type: 'list_windows' })
            // Timeout fallback — resolve with current state after 3s
            setTimeout(() => {
                if (windowsResolver === resolve) {
                    windowsResolver = null
                    resolve(windows.value)
                }
            }, 3000)
        })
    }

    /**
     * Create a new tmux window with the given name.
     * The backend responds with an updated window list.
     */
    function createWindow(name) {
        wsSend({ type: 'create_window', name })
    }

    /**
     * Switch to a tmux window by name.
     * The backend responds with window_changed, which hides the navigator.
     */
    function selectWindow(name) {
        wsSend({ type: 'select_window', name })
    }

    /**
     * Toggle the shell navigator visibility.
     */
    function toggleNavigator() {
        showNavigator.value = !showNavigator.value
    }
```

**Step 4: Update the return statement**

Update the return (line ~474) to expose the new state and functions:

```javascript
    return {
        containerRef, isConnected, started, start, reconnect,
        windows, showNavigator, listWindows, createWindow, selectWindow, toggleNavigator,
    }
```

**Step 5: Commit**

```bash
git add frontend/src/composables/useTerminal.js
git commit -m "feat(terminal): add tmux window control to useTerminal composable"
```

---

### Task 4: Frontend — create TmuxNavigator.vue component

**Files:**
- Create: `frontend/src/components/TmuxNavigator.vue`

**Step 1: Create the component**

```vue
<script setup>
import { ref, onMounted } from 'vue'

const props = defineProps({
    windows: {
        type: Array,
        default: () => [],
    },
})

const emit = defineEmits(['select', 'create'])

const newName = ref('')

function handleSelect(name) {
    emit('select', name)
}

function handleCreate() {
    const name = newName.value.trim()
    if (!name) return
    emit('create', name)
    newName.value = ''
}
</script>

<template>
    <div class="tmux-navigator">
        <div class="navigator-content">
            <h3 class="navigator-title">Shells</h3>

            <div class="shell-list">
                <wa-button
                    v-for="win in windows"
                    :key="win.name"
                    class="shell-button"
                    :variant="win.active ? 'brand' : 'neutral'"
                    :appearance="win.active ? 'outlined' : 'plain'"
                    size="medium"
                    @click="handleSelect(win.name)"
                >
                    <span v-if="win.active" class="active-indicator">●</span>
                    <span v-else class="active-indicator-placeholder"></span>
                    {{ win.name }}
                </wa-button>
            </div>

            <form class="create-form" @submit.prevent="handleCreate">
                <wa-input
                    :value="newName"
                    placeholder="Name"
                    size="small"
                    class="create-input"
                    @input="newName = $event.target.value"
                ></wa-input>
                <wa-button
                    type="submit"
                    variant="brand"
                    appearance="outlined"
                    size="small"
                    :disabled="!newName.trim()"
                >
                    Create
                </wa-button>
            </form>
        </div>
    </div>
</template>

<style scoped>
.tmux-navigator {
    height: 100%;
    display: flex;
    align-items: center;
    justify-content: center;
    background: var(--wa-color-surface-default);
}

.navigator-content {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-m);
    width: min(400px, calc(100% - 2rem));
}

.navigator-title {
    margin: 0;
    font-size: var(--wa-font-size-l);
    font-weight: 600;
    color: var(--wa-color-text-default);
}

.shell-list {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-2xs);
}

.shell-button {
    width: 100%;
}

.shell-button::part(base) {
    justify-content: flex-start;
    width: 100%;
}

.active-indicator {
    color: var(--wa-color-brand-600);
    margin-right: var(--wa-space-xs);
    font-size: 0.8em;
}

.active-indicator-placeholder {
    display: inline-block;
    width: 0.8em;
    margin-right: var(--wa-space-xs);
}

.create-form {
    display: flex;
    gap: var(--wa-space-xs);
    align-items: center;
    margin-top: var(--wa-space-s);
}

.create-input {
    flex: 1;
}
</style>
```

**Step 2: Commit**

```bash
git add frontend/src/components/TmuxNavigator.vue
git commit -m "feat(terminal): create TmuxNavigator shell picker component"
```

---

### Task 5: Frontend — integrate TmuxNavigator into TerminalPanel

**Files:**
- Modify: `frontend/src/components/TerminalPanel.vue`

**Step 1: Rewrite TerminalPanel to include the navigator**

```vue
<script setup>
import { watch } from 'vue'
import { useTerminal } from '../composables/useTerminal'
import TmuxNavigator from './TmuxNavigator.vue'

const props = defineProps({
    sessionId: {
        type: String,
        default: null,
    },
    active: {
        type: Boolean,
        default: false,
    },
})

const {
    containerRef, isConnected, started, start, reconnect,
    windows, showNavigator, listWindows, createWindow, selectWindow, toggleNavigator,
} = useTerminal(props.sessionId)

// Lazy init: start the terminal only when the tab becomes active for the first time
watch(
    () => props.active,
    (active) => {
        if (active && !started.value) {
            start()
        }
    },
    { immediate: true },
)

async function handleNavigatorSelect(name) {
    selectWindow(name)
}

async function handleNavigatorCreate(name) {
    createWindow(name)
    // Refresh the list after creation (the backend sends updated windows automatically)
}

// Fetch window list when navigator is shown
watch(showNavigator, (show) => {
    if (show) {
        listWindows()
    }
})

// Expose toggleNavigator for parent component
defineExpose({ toggleNavigator })
</script>

<template>
    <div class="terminal-panel">
        <!-- Terminal xterm.js container — hidden when navigator is shown -->
        <div ref="containerRef" class="terminal-container" :class="{ hidden: showNavigator }"></div>

        <!-- Tmux Navigator overlay -->
        <TmuxNavigator
            v-if="showNavigator"
            :windows="windows"
            @select="handleNavigatorSelect"
            @create="handleNavigatorCreate"
        />

        <!-- Disconnect overlay -->
        <div v-if="started && !isConnected && !showNavigator" class="disconnect-overlay">
            <wa-callout variant="warning" appearance="outlined">
                <wa-icon slot="icon" name="plug-circle-xmark"></wa-icon>
                <div class="disconnect-content">
                    <div>Terminal disconnected</div>
                    <wa-button
                        variant="warning"
                        appearance="outlined"
                        size="small"
                        @click="reconnect"
                    >
                        <wa-icon slot="start" name="arrow-rotate-right"></wa-icon>
                        Reconnect
                    </wa-button>
                </div>
            </wa-callout>
        </div>
    </div>
</template>

<style scoped>
.terminal-panel {
    height: 100%;
    display: flex;
    flex-direction: column;
    position: relative;
}

.terminal-container {
    flex: 1;
    min-height: 0;
    width: 100%;
    padding: var(--wa-space-2xs);
}

.terminal-container.hidden {
    display: none;
}

/* Ensure xterm fills its container */
.terminal-container :deep(.xterm) {
    height: 100%;
}

.terminal-container :deep(.xterm-viewport) {
    overflow-y: auto !important;
}

.disconnect-overlay {
    position: absolute;
    inset: 0;
    display: flex;
    align-items: center;
    justify-content: center;
    background: rgba(0, 0, 0, 0.4);
    z-index: 10;
}

.disconnect-content {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: var(--wa-space-m);
    text-align: center;
}
</style>
```

**Step 2: Commit**

```bash
git add frontend/src/components/TerminalPanel.vue
git commit -m "feat(terminal): integrate TmuxNavigator into TerminalPanel"
```

---

### Task 6: Frontend — add tab toggle in SessionView.vue

**Files:**
- Modify: `frontend/src/views/SessionView.vue`

**Step 1: Add a ref to the TerminalPanel**

Near the other panel refs (search for `filesPanelRef`), add:

```javascript
const terminalPanelRef = ref(null)
```

**Step 2: Update the `onTabShow` handler**

In `onTabShow` (line ~240), the current `switchToTab(panel)` call early-returns when the tab is already active (line 190: `if (panel === activeTabId.value) return`). We need to intercept the terminal case before that return.

Modify `onTabShow`:

```javascript
function onTabShow(event) {
    const panel = event.detail?.name
    if (!panel) return

    // Toggle navigator when re-clicking the terminal tab
    if (panel === 'terminal' && activeTabId.value === 'terminal') {
        terminalPanelRef.value?.toggleNavigator()
        return
    }

    switchToTab(panel)
}
```

**Step 3: Add the ref to the TerminalPanel template**

In the template (line ~624), add `ref="terminalPanelRef"`:

```html
            <wa-tab-panel name="terminal">
                <TerminalPanel
                    ref="terminalPanelRef"
                    :session-id="session?.id"
                    :active="isActive && activeTabId === 'terminal'"
                />
            </wa-tab-panel>
```

**Step 4: Commit**

```bash
git add frontend/src/views/SessionView.vue
git commit -m "feat(terminal): toggle shell navigator on terminal tab re-click"
```

---

### Task 7: Verify — build and manual test

**Step 1: Build the frontend**

Run: `cd /data/perso/CLAUDE/twicc/frontend && npm run build`
Expected: Build succeeds with no errors.

**Step 2: Manual test checklist**

Verify in the browser (dev servers must be running):

1. Open a session → click Terminal tab → shell connects, shows "Connected (tmux)." → this is the "main" shell
2. Click the Terminal tab again → TmuxNavigator appears with "main" listed and marked active (●)
3. Type a name in the input → click "Create" → new shell appears in the list, stays on navigator
4. Click the new shell button → navigator closes, terminal shows the new shell
5. Click Terminal tab again → navigator shows both shells, new one is active
6. Click "main" → navigator closes, returns to main shell

**Step 3: Final commit if any adjustments needed**

```bash
git add -u
git commit -m "fix(terminal): polish tmux shell navigator"
```
