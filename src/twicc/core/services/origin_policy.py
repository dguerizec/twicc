"""Pure origin-routing policy for the common public-origin gate.

Three layers, all pure and independently testable (design §9):

- request-authority parsing (§8): the raw ``Host`` header value → a canonical
  :class:`RequestAuthority`, under the strict ASCII hostname contract (§5.1);
- recognition (§11): best-effort extraction of the authority inside an INVALID
  stored setting, so a broken setting can still quarantine its surface;
- policy building + request classification (§10-§11): the three live settings
  → an :class:`OriginPolicy`; one request's authority/path/protocol → a
  routing surface.

The ASGI executor lives in ``twicc.origin_gate``.
"""

from __future__ import annotations

import re
from typing import NamedTuple

from twicc.core.services.public_origin import _TRIM_CHARS, canonicalize_hostname

# Served on the share host. /_twicc/artifact-shell/ and the broker shim are
# shared with the working app's own artifact preview, so they are allowed on
# the share host but must NOT be hidden on the working origin — only /share/
# and /_twicc/share/ are share-exclusive.
SHARE_ONLY_PREFIXES = (
    "/share/",
    "/_twicc/share/",
    "/_twicc/artifact-shell/",
    "/_twicc/artifact-broker-shim.js",
)
# Share-exclusive: hidden (404) on any non-share routing authority.
SHARE_EXCLUSIVE_PREFIXES = ("/share/", "/_twicc/share/")

_SCHEME_PREFIX_RE = re.compile(r"^[a-z][a-z0-9+.-]*://", re.IGNORECASE)
_BRACKETED_AUTHORITY_RE = re.compile(r"^\[([^\]]*)\](?::([0-9]+))?$")
_PLAIN_AUTHORITY_RE = re.compile(r"^([^:\[\]]+)(?::([0-9]+))?$")


class RequestAuthority(NamedTuple):
    hostname: str
    authority: str


def parse_request_authority(value: str) -> RequestAuthority | None:
    """Parse one raw authority token per §8. ``None`` for any invalid input.

    Strict on purpose: no trimming, no percent decoding, no IDNA conversion,
    IPv6 requires brackets, a trailing colon is malformed, and the explicit
    port is preserved (the request side cannot know the original scheme, so it
    never strips a default port).
    """
    match = _BRACKETED_AUTHORITY_RE.fullmatch(value)
    bracketed = match is not None
    if match is None:
        match = _PLAIN_AUTHORITY_RE.fullmatch(value)
    if match is None:
        return None
    host_token, port_token = match.group(1), match.group(2)
    canonical = canonicalize_hostname(host_token, bracketed=bracketed)
    if canonical.hostname is None:
        return None
    port = None
    if port_token is not None:
        # Bound conversion by the significant decimal spelling. Leading zeroes
        # stay valid, but int() receives at most five digits.
        significant = port_token.lstrip("0") or "0"
        if len(significant) > 5 or (len(significant) == 5 and significant > "65535"):
            return None
        port = int(significant)
    serialized = f"[{canonical.hostname}]" if canonical.is_ipv6 else canonical.hostname
    authority = f"{serialized}:{port}" if port is not None else serialized
    return RequestAuthority(canonical.hostname, authority)


def request_authority_from_scope(scope) -> RequestAuthority | None:
    """The scope's routing authority, or ``None`` unless EXACTLY one ``Host``
    header is present and valid (§8)."""
    values = [value for name, value in scope.get("headers") or () if name == b"host"]
    if len(values) != 1:
        return None
    return parse_request_authority(values[0].decode("latin1"))


def recognize_authority(value) -> RequestAuthority | None:
    """§11 recognition: extract the authority a broken setting still names.

    Mechanical only — strip the whitespace the settings parser strips, drop one
    explicit ``scheme://`` prefix (any scheme: an unsupported scheme can leave
    the authority recognizable), cut at the first ``/``, ``?`` or ``#``, refuse
    userinfo, then apply the SAME strict token parsing as a ``Host`` header.
    Recognition never decodes percent escapes, never converts Unicode, and
    never guesses missing host syntax.
    """
    if not isinstance(value, str):
        return None
    raw = value.strip(_TRIM_CHARS)
    raw = _SCHEME_PREFIX_RE.sub("", raw, count=1)
    raw = re.split(r"[/?#]", raw, maxsplit=1)[0]
    if not raw or "@" in raw:
        return None
    return parse_request_authority(raw)
