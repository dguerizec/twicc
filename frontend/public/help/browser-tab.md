---
title: "The Browser tab"
---

The Browser tab embeds a page of your choice — typically the dev server of
the project you're working on — right next to the conversation. The page
runs exactly as it would in a normal browser tab: direct network, live
reload, service workers. TwiCC doesn't proxy or sandbox it.

### Opening a page

Type any URL in the address bar. The **Home** button jumps to the saved
default URL; use the **bookmark** menu to save the current URL as the
default for the project (or one of its workspaces) — the tab then opens on
it right away. The URL you were on is also remembered per session.

### Limits of embedding

Some pages can't be embedded:

- Sites that refuse to be framed (most public sites do) show a warning —
  use **Open in a new browser tab** instead.
- When TwiCC itself is served over `https`, plain `http://` pages are
  blocked by the browser (mixed content).

Your local dev servers are the sweet spot; that's what the tab is for.

### The companion script

An embedded page is a black box for TwiCC: links followed inside it are
invisible, so Back / Forward only track URLs entered in the toolbar. TwiCC
provides a **companion script** to be added to your dev page that removes
that limit and unlocks the richer features:

- real navigation tracking — Back / Forward / Reload drive the page's own
  history, and in-page (SPA) navigation updates the address bar;
- **element selection** — instead of describing "the third button in the
  header" and hoping the agent finds it, turn the mode on with the **mouse
  cursor** button in the toolbar, click the element right on the page, then
  hit the **comment** button: a widget opens where you can add an optional
  note (and include a screenshot of the element) before pasting the whole
  thing into the chat input — a precise, code-friendly description of the
  element (tag, ids, classes — things the agent can find in your source)
  along with your comment. Perfect for "make this bigger" or "this label
  is wrong";
- **page errors** — uncaught exceptions and `console.error` calls are
  collected, counted in the toolbar, and one click away from your message.

When the embedded page has no companion, a banner in the tab offers the
exact snippet to copy. Paste it into your page's `<html>` **head, as high
as possible** — the earlier it loads, the earlier navigation and errors are
captured (it uses `defer`, so it never blocks your page). It is invisible
to the page itself and does nothing when the page runs outside TwiCC, so
it's safe to keep in your dev template. If you access TwiCC from another
machine, configure the [external URL](help/external-url) so the snippet
points at an address your browser can reach.

The **plug** button shows the companion status: colored when connected,
dimmed otherwise. Clicking it opens this help page.

### Responsive mode

The **phone** button gives the page an exact device-size viewport: pick a
preset (or type a width × height), swap the two, or drag the handles around
the frame. The page reflows exactly as it would on a window of that size.
No companion needed.

### Everything else

- **Full screen** expands the whole tab, toolbar included.
- **Open in a new browser tab** hands the current URL to your real browser.
- Errors, selection and comments always land in the message you're
  composing — nothing is sent to the agent without you.
