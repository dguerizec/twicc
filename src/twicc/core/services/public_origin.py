"""Normalize the three synced public-origin settings.

Mirrored by ``frontend/src/utils/publicOrigin.js``. Both implementations are
covered by ``tests/fixtures/public_origin_cases.json``.
"""

from __future__ import annotations

import ipaddress
import re
from typing import NamedTuple
from urllib.parse import SplitResult, urlsplit


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


def _candidate(raw: str) -> tuple[str | None, str | None]:
    if raw.startswith("//"):
        return None, "scheme"
    if _HTTP_SCHEME_RE.match(raw):
        return raw, None
    if _EXPLICIT_SCHEME_RE.match(raw):
        return None, "scheme"
    scheme = "http" if _LOCAL_HOST_RE.match(raw) else "https"
    return f"{scheme}://{raw}", None


def _parse(value: str | None) -> tuple[str, SplitResult | None, str | None]:
    raw = (value or "").strip(_TRIM_CHARS)
    if not raw:
        return raw, None, None
    candidate, error = _candidate(raw)
    if error:
        return raw, None, error
    try:
        parsed = urlsplit(candidate)
        hostname = parsed.hostname
    except ValueError as exc:
        error = "port" if "port" in str(exc).lower() else "host"
        return raw, None, error
    if parsed.scheme.lower() not in ("http", "https"):
        return raw, None, "scheme"
    if not hostname or any(char.isspace() for char in hostname):
        return raw, None, "host"
    try:
        parsed.port
    except ValueError:
        return raw, None, "port"
    if parsed.username is not None or parsed.password is not None:
        return raw, None, "credentials"
    return raw, parsed, None


def _origin(parsed: SplitResult) -> PublicOriginResult:
    scheme = parsed.scheme.lower()
    hostname = parsed.hostname.lower()
    if re.fullmatch(r"[0-9.]+", hostname):
        try:
            hostname = str(ipaddress.IPv4Address(hostname))
        except ipaddress.AddressValueError:
            return PublicOriginResult(None, "host")
    try:
        ascii_hostname = hostname.encode("idna").decode("ascii")
    except UnicodeError:
        return PublicOriginResult(None, "host")
    serialized_host = f"[{ascii_hostname}]" if ":" in ascii_hostname else ascii_hostname
    port = parsed.port
    if port == (443 if scheme == "https" else 80):
        port = None
    suffix = f":{port}" if port is not None else ""
    return PublicOriginResult(
        f"{scheme}://{serialized_host}{suffix}",
        None,
        scheme,
        ascii_hostname,
        port,
    )


def normalize_public_origin(value: str | None) -> PublicOriginResult:
    """Return one canonical HTTP origin, or a stable validation error."""
    if value is not None and not isinstance(value, str):
        return PublicOriginResult(None, "type")
    raw, parsed, error = _parse(value)
    if not raw:
        return PublicOriginResult("", None)
    if error:
        return PublicOriginResult(None, error)
    if parsed.path not in ("", "/"):
        return PublicOriginResult(None, "path")
    if parsed.query:
        return PublicOriginResult(None, "query")
    if parsed.fragment:
        return PublicOriginResult(None, "fragment")
    return _origin(parsed)


def repair_legacy_public_origin(value: str | None) -> PublicOriginResult:
    """Repair safe legacy suffixes while retaining unsafe values unchanged."""
    if value is not None and not isinstance(value, str):
        return PublicOriginResult(None, "type")
    raw, parsed, error = _parse(value)
    if not raw:
        return PublicOriginResult("", None)
    if error:
        return PublicOriginResult(None, error)
    return _origin(parsed)


def usable_public_origin(value: str | None) -> str:
    """Return the canonical origin, or an empty string for invalid storage."""
    return normalize_public_origin(value).value or ""
