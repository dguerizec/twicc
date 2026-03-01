# Tmux Shell Navigator — Design

**Date:** 2026-03-01
**Status:** Approved

## Goal

Allow users to manage multiple named shells within a tmux session, with a simple navigator UI accessible by re-clicking the terminal tab. First shell ("main") is created automatically on first visit.

## Constraints

- No modifications to existing DB tables
- No split/pane support — one shell visible at a time
- Single WebSocket connection (approach B — multiplexing via tmux)
- Shell names stored natively in tmux (window names)

## WebSocket Protocol Extension

### Client → Server (JSON)

```json
{ "type": "list_windows" }
{ "type": "create_window", "name": "build" }
{ "type": "select_window", "name": "build" }
```

### Server → Client

Control messages are sent as JSON text frames with a `type` field. PTY data continues as plain text frames (unchanged).

```json
{ "type": "windows", "windows": [{"name": "main", "active": true}, {"name": "build", "active": false}] }
{ "type": "window_changed", "name": "build" }
```

Detection: attempt `JSON.parse` on incoming text frame — if it succeeds and has a `type` field, it's a control message; otherwise it's PTY output forwarded to `terminal.write()`.

## Backend — `terminal.py`

New control message handling in `receive_loop`:

- **`list_windows`**: runs `tmux -L twicc list-windows -t <session> -F "#{window_name}:#{window_active}"`, parses output, sends `windows` response.
- **`create_window`**: runs `tmux -L twicc new-window -t <session> -n <name>`, then sends updated `windows` list + `window_changed`.
- **`select_window`**: runs `tmux -L twicc select-window -t <session>:<name>`, sends `window_changed`.

All tmux commands use `subprocess.run` (instant, no async needed). Session name is the existing `twicc-<session_id>`.

**First connection**: the default tmux window (index 0) is renamed to "main" via `tmux rename-window`.

## Frontend — Components

### TerminalPanel.vue

- New `showNavigator` ref (boolean, default `false`)
- When `true`: shows `TmuxNavigator` overlay, hides xterm.js container (`display: none`)
- When `false`: shows xterm.js, hides navigator

### SessionView.vue — Tab toggle

When user clicks the terminal tab while it's already active, toggle `showNavigator` on `TerminalPanel` (instead of the current no-op).

### useTerminal.js — New functions

- `listWindows()` — sends `list_windows`, returns Promise resolved on `windows` response
- `createWindow(name)` — sends `create_window`
- `selectWindow(name)` — sends `select_window`; on `window_changed` response, sets `showNavigator = false`
- `ws.onmessage` updated: try JSON parse for control messages, fall through to `terminal.write()` for PTY data

### TmuxNavigator.vue (new component)

Simple navigator screen:
- Title "Shells"
- List of `wa-button` (one per window), full width, `●` marker on active window
- Click on a shell → `selectWindow(name)` → returns to terminal
- Bottom: `wa-input` (name field) + `wa-button` "Create" → `createWindow(name)` → stays on navigator, list refreshes
- Calls `listWindows()` on mount
- Dark background matching terminal theme

## First-visit Behavior

When the terminal tab is activated for the first time:
1. WebSocket connects (existing flow)
2. tmux session is created with default window (existing `new-session -A`)
3. Backend renames the default window to "main"
4. Terminal is displayed directly — no navigator shown
5. User sees the "main" shell immediately

The navigator is only shown on subsequent clicks on the already-active terminal tab.
