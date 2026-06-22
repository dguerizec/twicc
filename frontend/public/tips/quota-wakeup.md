---
title: "Get an extra 5-hour quota window"
providers_any: [claude_code, codex]
---

A provider's 5-hour quota window opens on your **first request** and resets 5
hours later — so the earlier that first request lands, the more full windows
your day holds.

Say you work 9am–7pm. Start at 9 and you get **two** windows: 9–2 and 2–7. Have
the first one open at **6am** instead and the same hours now fit **three**:
6–11, 11–4, 4–9 — an extra window, for the same work.

Set a daily **Quota wake-up** time per provider in **Settings → Usage**. TwiCC
sends a tiny throwaway request at that hour to open the window early — only
while it's running, and skipped if a window is already active.
