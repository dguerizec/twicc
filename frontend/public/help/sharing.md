---
title: "Sharing"
---

A **share link** is a public, read-only URL to one of your sessions or
artifacts. Anyone who has the link can open it in a browser — no TwiCC
account, no sign-in. They can only **look**: a link can never send a
message, run a tool, or change anything on your side.

## What you can share

- **An artifact** — a bookmarked file from a session's Artifacts tab,
  rendered on its own (an HTML page, a chart, a document…).
- **A session** — the conversation, rendered read-only. Tool calls and
  diffs show as you see them.

> **A session is shared as-is** — nothing in it is redacted or cleaned up.
> Before sharing one, make sure it holds no secrets or private data, and
> only give the link to people you trust. An artifact is a single file you
> picked, so there is much less to review.

## Before you can share

Sharing needs a **dedicated share host** — a hostname distinct from the one
you use TwiCC on, pointing at the same instance. Set it in
**Settings → Sharing**. Until it is configured, the share buttons stay
disabled.

Keep in mind that **every visit is served by the machine running TwiCC** —
your own computer, over your tunnel. There is no hosted copy, so avoid
handing links out at scale: all that traffic lands directly on your machine.

## Your links

When you create a link you can give it a **label**, protect it with a
**password**, and set an **expiry**. You can create **several links** to the
same session or artifact, each with its own password and expiry — hand
different links to different people and follow each one's view logs
separately. A link per person is handy: you can revoke one person's access
without touching the others.

Manage them from the share button (or **Settings → Sharing → Shared
links**), where each link also shows how many times it was opened:

- **Revoke** disables a link temporarily — viewers get a 404 — (but keeps
  its view logs), so you can re-enable it later.
- **Delete** removes it permanently, including its view logs.

## Agents and share links

By default, only you can create or manage share links — agents cannot.
Two switches in **Settings → Sharing** change that, one per kind (session
links, artifact links). Both are **off** until you enable them.

Enabling **Session shares** lets agents create session links for their own
session or any session in their spawn subtree. Enabling **Artifact shares**
lets agents create artifact links for bookmarks owned by their own session
or any session in their spawn subtree.

For the enabled kind, agents can also:

- **manage** (update, delete, re-publish) links created by themselves or by
  agents in their spawn subtree;
- **revoke** any existing link of that kind — including links you created
  yourself (un-publishing is always considered safe);
- **read the URL** of every existing link of that kind, including yours.

Agents can never clear a link's password, never share with the `debug`
display mode, and their new session links are frozen snapshots unless they
explicitly ask for a live link.

## Artifacts and network access

A shared artifact runs for the viewer with **no consent prompts**. If it
makes network calls, only the hosts **you have allowed** are reachable;
any other host is blocked and the viewer sees a short "blocked by the
owner" note. You allow or deny hosts from the artifact's **bookmark**, and
hosts that viewers tried to reach are listed there so you can approve them
after the fact.

If you change an artifact's files after sharing, use **Push update** on the
link to refresh what viewers see.
