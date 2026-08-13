---
title: "Reaching TwiCC from anywhere"
---

The **External address** is where you reach *this* TwiCC instance from your
devices. TwiCC also uses it to build links back to your sessions. An external
notification can then open the right conversation instead of a dead `localhost`
address.

### What it's for

- Open a session from your **phone or another computer** while TwiCC keeps
  running on your home machine.
- It's also stitched into **external notifications** (Apprise): a pushed alert
  can then link straight to the session it's about.

Leave it empty and TwiCC simply omits those links — nothing else breaks.

### Why you need a tunnel

TwiCC runs as a local process, so `http://localhost:…` only means something on
the machine it runs on. To reach it from elsewhere you expose it through a
**tunnel**: a service that gives your local instance a stable public address and
forwards traffic back to it. That public address is exactly what you put in the
External address. A few popular options:

- **Cloudflare Tunnel** (`cloudflared`) — a named tunnel with a stable hostname.
- **Tailscale** — a private mesh VPN between your own devices, with optional
  public exposure (Funnel).
- **ngrok** — quick public URLs for a local port.

### Protect your tunnel

A tunnel can make your instance reachable from the open internet, so treat it
seriously. Every provider ships ways to lock it down — use them:

- **Cloudflare Tunnel** → put **Cloudflare Access** in front (email one-time
  PIN, Google/GitHub SSO), add IP rules or WAF.
- **Tailscale** → keep it on your **private tailnet** so only your own devices
  reach it (avoid public Funnel unless you truly need it); tighten with device
  ACLs.
- **ngrok** → enable its built-in **Basic Auth**, **OAuth** (Google, GitHub…),
  or IP allowlists.

### TwiCC's own password comes first

Most important: **TwiCC refuses every non-local connection unless you've set a
password.** So an External address only actually works once you've run
`twicc password set` — that password is your primary protection.

(The tunnel provider's protection sits *on top* of that — a second layer, in
addition to the password — but it stays important: it keeps unwanted traffic
away from TwiCC entirely, before it ever reaches the login screen.)
