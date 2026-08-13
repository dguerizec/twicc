# Peer Origin Routing Design

**Status:** approved design, not implemented
**Date:** 2026-08-13

## 1. Scope

This design restricts the Peer HTTP surface to the configured Peer address.
It also centralizes routing for the External, Share, and Peer addresses.

The three settings keep their existing internal names:

- `publicBaseUrl` is the **External address**;
- `shareBaseUrl` is the Share address, shown as **Share host**;
- `peerBaseUrl` is the Peer address, shown as **Your address**.

This design replaces the ingress decision in
`docs/plans/2026-07-24-peer-messaging-design.md` section 4.3. A Peer address can
now share the External address or use a dedicated routing authority.

The common origin syntax remains defined by
`docs/superpowers/specs/2026-08-13-public-origin-settings-design.md`.

### 1.1 Hard compatibility boundary

**This boundary is a core design invariant.** The Peer System exists only on
the owner's development machines. It has never been released, distributed, or
deployed to another user.

Every instance and database that contains Peer data is owner-controlled
development or test state. The owner can recreate that state when necessary.

The first released Peer version defines the only supported Peer contract.
Earlier development builds create no compatibility obligation.

No backward or forward compatibility is required between development builds.
This rule covers:

- frontend state, components, payloads, and cached data;
- backend APIs, services, validation, and behavior;
- Peer settings and their development-only stored values;
- the Peer wire protocol, endpoints, payloads, authentication, and responses;
- the database schema and every development-only Peer row;
- tests, fixtures, and generated Peer history.

The implementation must not add compatibility machinery for an unreleased
Peer behavior. In particular, it must not add:

- protocol or feature-version negotiation;
- capability detection for older Peer implementations;
- fallback endpoints or fallback request formats;
- dual-read or dual-write behavior;
- legacy parsers, schema adapters, or old-value readers;
- repair, backfill, merge, or conversion of development-only Peer history;
- rolling-deployment compatibility between old and new Peer code.

Unreleased Peer migrations can be replaced or rewritten. A Peer migration can
fail explicitly when it encounters unexpected development-only Peer state. It
does not need a repair path because the owner can recreate that database.

Released TwiCC databases contain no Peer data. A normal release migration must
still preserve data from released non-Peer features. This invariant does not
authorize damage to unrelated TwiCC data.

Tests target the final Peer contract and clean migration from a released TwiCC
schema. They do not test upgrade paths between Peer development commits.

A review must not require Peer compatibility machinery unless this release
boundary changes before the first Peer release.

## 2. Goals

- Expose `/peer/` only through the configured Peer address.
- Expose nothing except `/peer/` through a dedicated Peer address.
- Keep the existing dedicated Share hostname boundary.
- Keep the complete application on the External address.
- Permit local and LAN access without exposing `/peer/` there.
- Apply setting changes without a server restart.
- Validate relationships between all three stored addresses.

## 3. Out of scope

This design does not define relationship changes after `peerBaseUrl` changes.
It does not define revocation, credential invalidation, or reconnection.

`docs/plans/2026-08-13-peer-revocation-reconnection-design.md` is a temporary
work-in-progress draft. Readers, reviewers, planners, and implementers must
ignore it for this work.

That draft is not a requirement, dependency, or source of truth for this
design. It will be reviewed only after this routing work is complete.

This design allows the setting change without a relationship warning. It
deliberately leaves every resulting relationship effect unspecified.

The existing plain HTTP warning remains. It warns about unencrypted Peer
tokens, not about an address change.

This design does not add a Peer settings migration. The Peer System is new and
has not shipped. No deployed `peerBaseUrl` requires compatibility or repair.

## 4. Terms

### 4.1 Normalized origin

A normalized origin contains a scheme, hostname, and optional non-default
port. The existing common normalizer produces this value.

Examples:

| Input | Normalized origin |
|---|---|
| `https://Example.com:443` | `https://example.com` |
| `http://Example.com:80` | `http://example.com` |
| `https://Example.com:8443` | `https://example.com:8443` |

### 4.2 Routing authority

A routing authority contains the normalized hostname and remaining port. It
does not contain the scheme.

The scheme is not reliable behind a proxy or tunnel. The ASGI scheme can
describe the proxy connection instead of the original client connection.

| Origin | Routing authority |
|---|---|
| `https://example.com` | `example.com` |
| `http://example.com` | `example.com` |
| `https://example.com:8443` | `example.com:8443` |

Default ports disappear during origin normalization. A non-default explicit
port remains part of the routing authority.

### 4.3 Share hostname

Share routing compares only the hostname. It ignores every port.

Browser cookies are not port-scoped. Therefore, a port cannot isolate Share
from the authenticated application.

## 5. Address relationships

### 5.1 Share

A non-empty Share address must use a different hostname from each non-empty
External or Peer address. A different scheme or port does not satisfy this
requirement.

The Share hostname remains a dedicated public surface. It never serves the
working application, `/peer/`, `/mcp`, or general static files.

### 5.2 Peer shared with External

Peer shares External only when both normalized origins are exactly equal.

This authority serves the complete application. It also serves `/peer/`.

### 5.3 Dedicated Peer

Peer is dedicated when its routing authority differs from External. An empty
External address also makes every valid Peer address dedicated.

A dedicated Peer authority serves only HTTP paths below `/peer/`. It serves no
WebSocket path.

### 5.4 Ambiguous Peer and External addresses

Two origins can differ while producing the same routing authority. A
scheme-only difference is the relevant example.

TwiCC rejects this configuration. It cannot recover the external scheme after
a proxy removes that information.

Examples:

| External address | Peer address | Result |
|---|---|---|
| `https://x.example` | `https://x.example` | Shared |
| `https://x.example` | `http://x.example` | Rejected |
| `https://x.example` | `https://x.example:8443` | Dedicated |
| `https://x.example:7443` | `https://x.example:8443` | Dedicated |

## 6. Common settings validation

The backend validates the merged final settings under the existing settings
lock. It validates relationships when at least one origin value changes.

This trigger preserves the existing unrelated-update contract. An unchanged
manual or legacy problem does not block another settings change.

The backend applies these rules:

1. Empty values are valid.
2. Every changed value must satisfy the common origin syntax.
3. Share and External cannot use the same non-empty hostname.
4. Share and Peer cannot use the same non-empty hostname.
5. Equal Peer and External normalized origins mean shared routing.
6. Different Peer and External routing authorities mean dedicated routing.
7. Different origins with one routing authority are rejected.

The update is atomic. A rejected update changes no setting and increments no
settings version.

Errors identify the changed fields that participate in each conflict. A
multi-field patch can return more than one field error.

The JavaScript mirror provides immediate errors in Settings. The backend stays
authoritative for the UI, WebSocket writes, and CLI writes.

The existing frontend check also compares Share with
`window.location.hostname`. This check protects an active working hostname when
the External address is empty or different.

Changing the Peer address requires no confirmation or relationship warning.
This work performs no resulting Peer relationship transition.

## 7. Request authority parsing

The filter reads the HTTP `Host` header from the ASGI scope. It does not use
the ASGI scheme or forwarded-protocol headers.

The parser:

- requires exactly one valid `Host` header;
- normalizes hostname case and IDNA;
- parses bracketed IPv6 correctly;
- preserves an explicit request port;
- returns no authority for malformed input.

The request parser does not remove a port. It cannot know the original scheme.

Configured origins already remove their scheme-specific default ports. The
outbound Peer client therefore advertises and calls the same canonical origin.

`Host: example.com:443` does not match the authority `example.com`. It matches
only the configured authority `example.com:443`.

This contract assumes that the proxy or tunnel preserves `Host`. Standard
proxy operation does this. An explicit `Host` rewrite is operator configuration.

TwiCC cannot recover information removed by that configuration.

## 8. ASGI architecture

A common `PublicOriginGate` replaces the current `ShareHostGate`. It wraps the
application above BlackNoise.

The gate therefore runs before:

- general static file handling;
- Django and its SPA fallback;
- the raw `/mcp` ASGI endpoint;
- application WebSockets.

The gate reads the three settings for each request. The settings reader uses
the existing in-process cache.

One pure classifier converts the live settings into routing policy. A separate
request classifier compares the request authority and path with that policy.

The ASGI layer only executes the classifier result. This separation keeps
normalization, policy, and protocol responses independently testable.

Lifespan and other non-request ASGI scopes pass through unchanged.

## 9. Routing table

The Peer surface means every HTTP path starting with `/peer/`. Peer has no
public WebSocket surface.

| Request origin | `/peer/` HTTP | Share-exclusive HTTP | Other HTTP | WebSockets |
|---|---|---|---|---|
| Share hostname | `404` | Existing Share policy | Existing Share-only policy | Share only |
| Dedicated Peer authority | Allowed | `404` | `404` | Close with `4404` |
| Shared External and Peer authority | Allowed | `404` | Full application | App only; Share closes `4404` |
| Every other authority | `404` | `404` | Full application | App only; Share closes `4404` |

"Every other authority" includes localhost, LAN names, and secondary tunnel
addresses. Those origins can still serve the application, but never `/peer/`.

An empty Peer address hides `/peer/` on every authority.

The current Share rules stay unchanged:

- the Share root redirects temporarily to `/share/`;
- Share pages and required public assets remain available;
- non-Share HTTP paths return `404`;
- only Share WebSockets remain available;
- Share-exclusive paths remain hidden on every other origin.

On a dedicated Peer authority, `/` returns `404`. The same applies to static
files, `/mcp`, `/api/`, `/rpc/`, artifacts, and every other route.

## 10. Runtime invalid settings

Normal write paths prevent every defined conflict. Manual file changes can
still create invalid stored state.

The live policy builder fails closed for the affected public surface:

- an empty or invalid Share address disables Share routes;
- an empty or invalid Peer address disables Peer routes;
- a Share and External hostname conflict disables Share routes;
- a Share and Peer hostname conflict disables both public surfaces;
- an ambiguous Peer and External authority disables Peer routes;
- an invalid non-empty External address disables Peer classification.

The gate never repairs or writes settings. It never changes Peer rows.

An invalid value might not contain a parseable hostname. TwiCC cannot identify
or quarantine a tunnel hostname that the setting does not describe.

Hidden HTTP paths return a plain `404 Not found`. The response does not reveal
the configured addresses or the rejection reason.

Rejected WebSockets close with code `4404`.

## 11. Live changes

The gate reads settings on every request. A successful Apply changes routing
for the next request without a restart.

The routing change itself has no confirmation and no warning. Relationship
side effects remain unspecified and outside this work.

## 12. Testing

### 12.1 Origin and settings tests

- Test routing authorities for hostnames, IPv4, IPv6, IDNA, and ports.
- Test default-port removal from configured origins.
- Test exact shared origins and dedicated authorities.
- Test every External, Share, and Peer conflict.
- Test atomic rejection and field errors.
- Test multi-field patches.
- Test that unrelated settings updates remain possible.
- Use shared Python and JavaScript cases for mirrored behavior.

### 12.2 ASGI routing tests

- Cover every routing-table cell for HTTP.
- Cover every routing-table cell for WebSockets.
- Cover a Peer address shared with External.
- Cover dedicated Peer hostnames and non-default ports.
- Cover an empty or invalid Peer address.
- Cover missing, duplicate, malformed, IPv6, and port-bearing `Host` headers.
- Prove that dedicated Peer blocks static files, `/mcp`, and the SPA.
- Prove that alternate application origins hide `/peer/`.
- Prove that settings changes affect the next request.

### 12.3 Share regression tests

The existing Share gate suite moves to the common gate suite. It keeps every
current Share behavior under test.

Tests prove that Share still serves its pages, assets, root redirect, favicon,
and WebSocket. They also prove that Share blocks Peer and the working app.

### 12.4 Peer migration boundary

No test migrates or repairs `peerBaseUrl`. A regression test confirms that
settings reads leave that value unchanged.

Tests do not create an upgrade matrix for earlier Peer development schemas,
frontend payloads, backend APIs, or wire protocols. An unexpected
development-only Peer row can produce an explicit migration failure.

## 13. Acceptance criteria

- A dedicated Peer address exposes only `/peer/` HTTP endpoints.
- The configured Peer authority is the only authority that serves `/peer/`.
- Sharing an exact External origin keeps the complete application available.
- Share remains isolated by hostname.
- Invalid cross-address settings writes fail atomically.
- Routing changes apply without a restart.
- No Peer settings migration or compatibility path exists.
- No compatibility branch supports an earlier Peer development build.
- Peer address changes show no relationship warning from this feature.
