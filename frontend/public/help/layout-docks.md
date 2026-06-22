---
title: "Arranging your session layout"
platform: [desktop]
---

You just sent a tab to a **dock** — one of the regions around the session.
This is how you shape your own layout: keep the chat front and center while
pinning **Files**, **Git**, **Terminal**, **Artifacts** — any tab — wherever
suits you.

- Use the **chevron (▾)** on any tab to move it to a dock (left, right,
  bottom) or back to the center.
- Drag the divider between regions to resize a dock. Minimize a dock to fold
  it away, and restore it when you need it.
- Your arrangement is remembered **per session**, so it's still there when
  you come back.

### Save it and reuse it

The **⋮ menu** at the right end of the tab bar is where layouts live:

- **Save layout** stores your current arrangement as a named layout — a new
  one, or overwriting an existing one.
- The same menu lists your saved layouts (and **Single pane**), so you can
  switch any session to one in a click, plus **Manage layouts…** to rename
  or delete them.

### Make one the default for new sessions

In the save dialog, tick **Set as default for new sessions** for any scope —
they're independent:

- **Global** — every new session, everywhere.
- **This project** — only new sessions in this project.
- **This worktree** — only new sessions in this worktree (when you're in one).

A new session picks the **most specific** default that applies (worktree →
project → global). You can change these later, too: the global default in
**Settings → Layouts**, and a project's (or worktree's) default from its
edit dialog.
