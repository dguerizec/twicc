# Public Origin Settings Design

## Scope

TwiCC has three synced settings that identify public origins:

- `publicBaseUrl`, shown as **External address**;
- `shareBaseUrl`, shown as **Share host**;
- `peerBaseUrl`, shown as **Your address** in the Peer settings.

This design gives all three settings one input, normalization, validation, and
legacy-migration contract. It does not add the planned peer-only host routing.

The setting names do not change. Existing code and persisted settings continue
to use the `*BaseUrl` names.

As part of this change, every user-facing **External URL** label becomes
**External address**. This includes the application, CLI help, help pages, and
tips. Internal identifiers such as `publicBaseUrl`, help keys, component names,
and function names do not change.

## Product meaning

Each non-empty value identifies an HTTP origin. It contains a scheme, a
hostname, and an optional port. It does not contain credentials, a non-root
path, a query, or a fragment.

An empty value keeps its current setting-specific meaning:

- `publicBaseUrl`: omit public links;
- `shareBaseUrl`: disable sharing;
- `peerBaseUrl`: disable peer messaging.

## Current behavior

The settings currently have three different contracts:

| Setting | UI input | Scheme-less value today |
|---|---|---|
| `publicBaseUrl` | Any string | Stored unchanged; generated links are relative and usually broken |
| `shareBaseUrl` | Bare hostname or URL | Stored bare; share URL builders add `https://` |
| `peerBaseUrl` | Absolute HTTP(S) URL | Rejected by the UI; a CLI-written value later fails in `httpx` |

The generic settings CLI accepts any string for all three settings. Backend
settings writes do not validate them.

## Common input contract

The common normalizer accepts an empty string. For a non-empty input, it:

1. trims the existing normative ASCII whitespace set: TAB, LF, VT, FF, CR, and
   SPACE;
2. preserves an explicit `http://` or `https://` scheme;
3. rejects every other explicit scheme;
4. infers a scheme when none is present;
5. requires a valid hostname and port;
6. rejects username or password credentials;
7. accepts only an empty path or `/`;
8. rejects a query or fragment;
9. serializes one canonical origin without a trailing slash.

Canonical output uses a lower-case scheme and hostname. It removes an explicit
default port (`:80` for HTTP and `:443` for HTTPS). It preserves a valid
non-default port and brackets IPv6 hostnames.

Examples:

| Input | Stored value |
|---|---|
| `https://TwiCC.Example.com/` | `https://twicc.example.com` |
| `http://localhost:3501` | `http://localhost:3501` |
| `share.example.com` | `https://share.example.com` |
| `localhost:3501` | `http://localhost:3501` |
| `devbox:3501` | `http://devbox:3501` |

The normalizer rejects `ftp://example.com`, credentials, invalid ports,
non-root paths, queries, fragments, protocol-relative inputs, and values
without a hostname.

## Scheme inference

Scheme inference follows the existing Browser address normalizer:

- use HTTP for `localhost`, IPv4 literals, `[::1]`, `*.local`, `*.test`,
  `*.localhost`, and a dotless single-label hostname with an explicit port;
- use HTTPS for every other scheme-less hostname.

An explicit scheme is authoritative. TwiCC never upgrades an explicit HTTP
origin to HTTPS.

This preserves the current working behavior of a bare public share hostname.
It also repairs scheme-less External addresses and CLI-written Peer addresses.

## Shared implementation

The backend is authoritative. A pure Python module owns parsing,
normalization, validation, scheme inference, and origin metadata extraction.

A dependency-free JavaScript mirror provides immediate form validation. Both
implementations consume one JSON fixture. The fixture covers valid,
normalizable, local, public, IPv4, IPv6, port, and invalid inputs.

The three Settings fields use the same JavaScript helper. Setting-specific UI
rules remain separate. For example, the Share address must still differ from
the working app hostname, and an HTTP Peer address can still show its existing
warning.

## Backend write contract

`settings_mutation.py` normalizes every changed origin-setting value before it
writes the merged settings. The normalized value is included in the existing
settings corrections broadcast, so every connected client converges on the
canonical value.

An invalid changed value rejects the settings update atomically with a field
error. The WebSocket and CLI surfaces expose the same error.

Settings clients can send a full snapshot rather than a narrow patch. An
unchanged invalid legacy value must not block an unrelated settings update.
Only a newly supplied or changed invalid value is rejected.

## Legacy migration

The first settings read applies a one-time, idempotent migration to all three
keys. Empty values stay empty.

The migration canonicalizes values that it can repair deterministically:

- add the inferred scheme to a bare hostname;
- normalize case and default ports;
- remove surrounding whitespace and a root trailing slash;
- reduce a value with a path, query, or fragment to its HTTP origin.

Examples:

| Stored before migration | Stored after migration |
|---|---|
| `share.example.com` | `https://share.example.com` |
| `twicc.example.com` | `https://twicc.example.com` |
| `localhost:3501` | `http://localhost:3501` |
| `https://example.com/base?x=1#part` | `https://example.com` |

The Share URL produced from a bare hostname does not change. External links
become absolute. A scheme-less Peer address becomes usable.

The migration does not guess when repair is unsafe. It retains an existing
value with an unsupported scheme, credentials, an invalid port, or no
hostname. It logs only the affected setting key, never the value.

The Settings UI keeps such a retained value visible and shows its validation
error. Runtime consumers treat it as unset until the user corrects it. This
preserves the user's input without using an unsafe or malformed origin.

## Runtime consumers

All runtime consumers use the authoritative parser instead of checking only
whether the stored string is non-empty.

- External notifications omit links for an invalid `publicBaseUrl`.
- The Browser companion snippet falls back to the current origin for an
  invalid `publicBaseUrl`.
- Share URL builders and the share host gate treat an invalid `shareBaseUrl`
  as unset.
- Share creation guards treat an invalid `shareBaseUrl` as unset.
- Peer messaging and the future peer host gate treat an invalid
  `peerBaseUrl` as unset.
- The peer display-name fallback uses the parsed peer hostname.

The existing Share URL builder can retain scheme-less fallback support during
the transition. Normal settings reads and writes nevertheless produce a
canonical origin.

## Error behavior

The UI uses one stable message for structural errors:

> Enter a hostname or an HTTP(S) origin without a path, query, or fragment.

It can use a more specific message for an unsupported scheme or credentials
when the common helper provides that error code.

No invalid update partially changes settings. No runtime consumer uses an
invalid retained legacy value.

## Tests

The implementation requires:

- shared Python and JavaScript fixture tests for every normalization case;
- backend settings-mutation tests for corrections and atomic rejection;
- migration tests for bare values, explicit schemes, suffix removal,
  idempotence, and retained unsafe values;
- frontend Settings tests for all three fields;
- consumer tests for notifications, companion snippets, sharing, and peer
  feature gating with invalid stored values;
- regression tests that an explicit HTTP origin remains HTTP;
- regression tests that a bare Share address still produces the same HTTPS
  share link.

## Deferred work

This design does not restrict a dedicated Peer origin to `/peer/`. That host
routing change follows after the three settings share this validated origin
contract.
