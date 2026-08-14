"""Authoritative normalization for the three synced public-origin settings.

The frontend performs only the safe subset defined by the public-origin
design. Backend and frontend fixture sections have explicit separate scopes.
"""

from __future__ import annotations

import ipaddress
import re
from typing import NamedTuple
from urllib.parse import SplitResult, urlsplit

import idna


PUBLIC_ORIGIN_SETTING_KEYS = ("publicBaseUrl", "shareBaseUrl", "peerBaseUrl")
LEGACY_PUBLIC_ORIGIN_SETTING_KEYS = ("publicBaseUrl", "shareBaseUrl")

_TRIM_CHARS = "\t\n\x0b\x0c\r "
_HTTP_SCHEME_RE = re.compile(r"^https?://", re.IGNORECASE)
_EXPLICIT_SCHEME_RE = re.compile(r"^[a-z][a-z0-9+.-]*:(?!\d)", re.IGNORECASE)
_LOCAL_HOST_RE = re.compile(
    r"^(localhost|127(?:\.\d{1,3}){3}|0\.0\.0\.0|\[::1?\]|"
    r"(?:\d{1,3}\.){3}\d{1,3}|[^./:\s]+\.(?:local|test|localhost)|"
    r"[^./:\s]+(?=:\d))(?::\d+)?(?:[/?#]|$)",
    re.IGNORECASE,
)


class PublicOriginResult(NamedTuple):
    value: str | None
    error: str | None
    scheme: str | None = None
    hostname: str | None = None
    port: int | None = None
    authority: str | None = None


class CanonicalHostname(NamedTuple):
    hostname: str | None
    is_ipv6: bool = False


_DNS_HOSTNAME_MAX_LENGTH = 253
_DNS_LABEL_RE = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)$")


def _valid_alabel(label: str) -> bool:
    """True when ``label`` (lower-case, ``xn--``-prefixed) is a valid IDNA2008 A-label."""
    try:
        return idna.alabel(idna.ulabel(label)).decode("ascii") == label
    except (idna.IDNAError, UnicodeError):
        return False


def canonicalize_hostname(token: str, *, bracketed: bool) -> CanonicalHostname:
    """Canonicalize one raw hostname token per the strict ASCII contract (design §5.1).

    ``bracketed`` says whether the source spelled the token inside ``[…]``:
    brackets require a valid IPv6 literal, canonicalized to the lower-case
    compressed RFC 5952 form. No percent decoding, no Unicode-to-IDNA
    conversion — an invalid raw token stays invalid.
    """
    if not token or "%" in token or not all(0x21 <= ord(char) <= 0x7e for char in token):
        return CanonicalHostname(None)
    lowered = token.lower()
    if bracketed:
        try:
            return CanonicalHostname(str(ipaddress.IPv6Address(lowered)), True)
        except ValueError:
            return CanonicalHostname(None)
    if lowered == "localhost":
        return CanonicalHostname("localhost")
    if re.fullmatch(r"[0-9.]+", lowered):
        try:
            ipaddress.IPv4Address(lowered)
        except ValueError:
            return CanonicalHostname(None)
        return CanonicalHostname(lowered)
    if len(lowered) > _DNS_HOSTNAME_MAX_LENGTH:
        return CanonicalHostname(None)
    for label in lowered.split("."):
        if not _DNS_LABEL_RE.fullmatch(label):
            return CanonicalHostname(None)
        if label.startswith("xn--") and not _valid_alabel(label):
            return CanonicalHostname(None)
    return CanonicalHostname(lowered)


def _candidate(raw: str) -> tuple[str | None, str | None]:
    if raw.startswith("//"):
        return None, "scheme"
    if _HTTP_SCHEME_RE.match(raw):
        return raw, None
    if _EXPLICIT_SCHEME_RE.match(raw):
        return None, "scheme"
    scheme = "http" if _LOCAL_HOST_RE.match(raw) else "https"
    return f"{scheme}://{raw}", None


class _RawAuthority(NamedTuple):
    hostname: str
    port: str | None
    bracketed: bool


def _raw_authority(candidate: str) -> tuple[_RawAuthority | None, str | None]:
    """Extract raw tokens before ``urlsplit`` can remove control characters."""
    authority = re.match(r"^https?://([^/?#]*)", candidate, re.IGNORECASE).group(1)
    host_port = authority.rsplit("@", 1)[-1]
    if host_port.startswith("["):
        match = re.fullmatch(r"\[([^\]]*)\](?::(.*))?", host_port)
        if match is None:
            return None, "host"
        hostname, port = match.group(1), match.group(2)
        bracketed = True
    else:
        if host_port.count(":") > 1:
            return None, "host"
        hostname, separator, port = host_port.partition(":")
        port = port if separator else None
        bracketed = False
    if not hostname:
        return None, "host"
    if any(char.isspace() or ord(char) < 0x20 or ord(char) == 0x7f for char in hostname):
        return None, "host"
    if port == "" or (port is not None and re.fullmatch(r"[0-9]+", port) is None):
        return None, "port"
    return _RawAuthority(hostname, port, bracketed), None


def _parse(
    value: str | None,
) -> tuple[str, SplitResult | None, CanonicalHostname | None, str | None]:
    raw = (value or "").strip(_TRIM_CHARS)
    if not raw:
        return raw, None, None, None
    candidate, error = _candidate(raw)
    if error:
        return raw, None, None, error
    authority, error = _raw_authority(candidate)
    if error:
        return raw, None, None, error
    canonical = canonicalize_hostname(authority.hostname, bracketed=authority.bracketed)
    try:
        parsed = urlsplit(candidate)
        hostname = parsed.hostname
    except ValueError as exc:
        error = "port" if "port" in str(exc).lower() else "host"
        return raw, None, None, error
    if parsed.scheme.lower() not in ("http", "https"):
        return raw, None, None, "scheme"
    if not hostname:
        return raw, None, None, "host"
    try:
        parsed.port
    except ValueError:
        return raw, None, None, "port"
    if parsed.username is not None or parsed.password is not None:
        return raw, None, None, "credentials"
    return raw, parsed, canonical, None


def _origin(parsed: SplitResult, canonical: CanonicalHostname) -> PublicOriginResult:
    scheme = parsed.scheme.lower()
    serialized_host = f"[{canonical.hostname}]" if canonical.is_ipv6 else canonical.hostname
    port = parsed.port
    if port == (443 if scheme == "https" else 80):
        port = None
    suffix = f":{port}" if port is not None else ""
    authority = f"{serialized_host}{suffix}"
    return PublicOriginResult(
        f"{scheme}://{authority}",
        None,
        scheme,
        canonical.hostname,
        port,
        authority,
    )


def normalize_public_origin(value: str | None) -> PublicOriginResult:
    """Return one canonical HTTP origin, or a stable validation error."""
    if value is not None and not isinstance(value, str):
        return PublicOriginResult(None, "type")
    raw, parsed, canonical, error = _parse(value)
    if not raw:
        return PublicOriginResult("", None)
    if error:
        return PublicOriginResult(None, error)
    if parsed.path not in ("", "/"):
        return PublicOriginResult(None, "path")
    query_index = raw.find("?")
    fragment_index = raw.find("#")
    if query_index >= 0 and (fragment_index < 0 or query_index < fragment_index):
        return PublicOriginResult(None, "query")
    if fragment_index >= 0:
        return PublicOriginResult(None, "fragment")
    if canonical.hostname is None:
        return PublicOriginResult(None, "host")
    return _origin(parsed, canonical)


def repair_legacy_public_origin(value: str | None) -> PublicOriginResult:
    """Repair safe legacy suffixes while retaining unsafe values unchanged."""
    if value is not None and not isinstance(value, str):
        return PublicOriginResult(None, "type")
    raw, parsed, canonical, error = _parse(value)
    if not raw:
        return PublicOriginResult("", None)
    if error:
        return PublicOriginResult(None, error)
    if canonical.hostname is None:
        return PublicOriginResult(None, "host")
    return _origin(parsed, canonical)


def usable_public_origin(value: str | None) -> str:
    """Return the canonical origin, or an empty string for invalid storage."""
    return normalize_public_origin(value).value or ""


ORIGIN_CONFLICT_SHARE_EXTERNAL = "origin_conflict_share_external_hostname"
ORIGIN_CONFLICT_SHARE_PEER = "origin_conflict_share_peer_hostname"
ORIGIN_CONFLICT_AMBIGUOUS = "origin_conflict_ambiguous_authority"


class OriginFieldError(NamedTuple):
    field: str
    code: str


def classify_peer_external(peer: PublicOriginResult, external: PublicOriginResult) -> str | None:
    """Peer/External routing class (design §6.2-§6.4) for parsed settings.

    ``None`` when peer is not a valid non-empty origin. An empty or invalid
    external makes every valid peer address dedicated at this (write-path)
    level; the runtime policy layer handles invalid-external quarantine.
    """
    if not peer.value:
        return None
    if not external.value:
        return "dedicated"
    if peer.value == external.value:
        return "shared"
    if peer.authority == external.authority:
        return "ambiguous"
    return "dedicated"


def validate_origin_settings(
    public_value, share_value, peer_value, *, changed_fields,
) -> tuple[OriginFieldError, ...]:
    """Validate changed origins and their relationships (design §7).

    Unchanged invalid values do not block a patch and do not become conflict
    operands. A structural error does not hide conflicts among other valid
    changed operands. Relationship errors name every participating field.
    """
    values = {"publicBaseUrl": public_value, "shareBaseUrl": share_value, "peerBaseUrl": peer_value}
    errors: list[OriginFieldError] = []
    results: dict[str, PublicOriginResult] = {}
    for field, value in values.items():
        # ``normalize_public_origin`` maps ``None`` to the valid empty result
        # because settings READS need that. Validation is a write-path contract:
        # a JSON ``null`` is a type error, not a request to clear the address.
        result = PublicOriginResult(None, "type") if value is None else normalize_public_origin(value)
        results[field] = result
        if field in changed_fields and result.error:
            errors.append(OriginFieldError(field, f"invalid_origin_{result.error}"))
    public, share, peer = results["publicBaseUrl"], results["shareBaseUrl"], results["peerBaseUrl"]
    if (
        changed_fields & {"shareBaseUrl", "publicBaseUrl"}
        and share.value and public.value and share.hostname == public.hostname
    ):
        errors.append(OriginFieldError("shareBaseUrl", ORIGIN_CONFLICT_SHARE_EXTERNAL))
        errors.append(OriginFieldError("publicBaseUrl", ORIGIN_CONFLICT_SHARE_EXTERNAL))
    if (
        changed_fields & {"shareBaseUrl", "peerBaseUrl"}
        and share.value and peer.value and share.hostname == peer.hostname
    ):
        errors.append(OriginFieldError("shareBaseUrl", ORIGIN_CONFLICT_SHARE_PEER))
        errors.append(OriginFieldError("peerBaseUrl", ORIGIN_CONFLICT_SHARE_PEER))
    if (
        changed_fields & {"peerBaseUrl", "publicBaseUrl"}
        and classify_peer_external(peer, public) == "ambiguous"
    ):
        errors.append(OriginFieldError("peerBaseUrl", ORIGIN_CONFLICT_AMBIGUOUS))
        errors.append(OriginFieldError("publicBaseUrl", ORIGIN_CONFLICT_AMBIGUOUS))
    return tuple(errors)
