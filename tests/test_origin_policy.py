"""Pure origin-routing policy: request-authority parsing, recognition, policy
building, and request classification (peer-origin-routing design §8-§11)."""

from unittest.mock import patch

import pytest

from twicc.core.services.origin_policy import (
    RequestAuthority,
    parse_request_authority,
    recognize_authority,
    request_authority_from_scope,
)


@pytest.mark.parametrize("value,hostname,authority", [
    ("example.com", "example.com", "example.com"),
    ("example.com:0", "example.com", "example.com:0"),
    ("EXAMPLE.com", "example.com", "example.com"),
    ("example.com:8443", "example.com", "example.com:8443"),
    ("example.com:443", "example.com", "example.com:443"),  # the explicit port component is retained (§8)
    ("example.com:08080", "example.com", "example.com:8080"),  # canonical decimal, no leading zeros
    ("localhost:3501", "localhost", "localhost:3501"),
    ("192.168.1.42:3501", "192.168.1.42", "192.168.1.42:3501"),
    ("[::1]:8443", "::1", "[::1]:8443"),
    ("[0:0:0:0:0:0:0:1]:8443", "::1", "[::1]:8443"),
    ("[2001:DB8::1]", "2001:db8::1", "[2001:db8::1]"),
    ("XN--FA-HIA.DE", "xn--fa-hia.de", "xn--fa-hia.de"),
])
def test_parse_request_authority_accepts(value, hostname, authority):
    assert parse_request_authority(value) == RequestAuthority(hostname, authority)


@pytest.mark.parametrize("value", [
    "",
    "example.com:",          # trailing colon is malformed in a Host header
    "example.com:bad",
    "example.com:70000",
    "a..example",
    "example.com.",
    "-a.example",
    "a-.example",
    "my_host.example",
    "exämple.com",
    "%65xample.com",
    "xn--.example",
    "XN--.example",
    "xn--a-ecp.example",
    "xn--e28h.example",
    "999.1.2.3",
    "1.2.3",
    "192.168.001.1",
    "::1",                   # IPv6 requires brackets in Host (§8)
    "[::1",
    "[1.2.3.4]",
    "[fe80::1%eth0]",
    "[fe80::1%25eth0]",
    "example.com:" + ("9" * 4301),
    "a b.example",
    "exa\tmple.com",
    "example.com:8\t0",
    "example.com\n",
    "user@example.com",
])
def test_parse_request_authority_rejects(value):
    assert parse_request_authority(value) is None


def test_parse_request_authority_bounds_port_before_integer_conversion():
    with patch("builtins.int", side_effect=AssertionError("unbounded port reached int")) as conversion:
        assert parse_request_authority("example.com:" + ("9" * 5000)) is None
    conversion.assert_not_called()


def test_parse_request_authority_strips_leading_zeroes_before_integer_conversion():
    real_int = int
    with patch("builtins.int", side_effect=lambda token: real_int(token)) as conversion:
        assert parse_request_authority("example.com:" + ("0" * 5000)) == RequestAuthority(
            "example.com", "example.com:0",
        )
    conversion.assert_called_once_with("0")


def _scope(host_values):
    return {"type": "http", "headers": [(b"host", v.encode("latin1")) for v in host_values]}


def test_request_authority_from_scope_requires_exactly_one_host():
    assert request_authority_from_scope(_scope(["example.com"])) == RequestAuthority("example.com", "example.com")
    assert request_authority_from_scope(_scope([])) is None
    assert request_authority_from_scope(_scope(["a.example", "b.example"])) is None
    assert request_authority_from_scope(_scope(["a.example", "a.example"])) is None
    assert request_authority_from_scope({"type": "http"}) is None


def test_recognize_authority_extracts_from_invalid_settings():
    # An unsupported scheme or a forbidden path can leave the authority
    # recognizable (§11); hostname and decimal port spelling are canonicalized.
    assert recognize_authority("ftp://share.example") == RequestAuthority("share.example", "share.example")
    assert recognize_authority("https://peer.example/forbidden") == RequestAuthority("peer.example", "peer.example")
    assert recognize_authority("https://peer.example:8443/x?q=1") == RequestAuthority(
        "peer.example", "peer.example:8443",
    )
    assert recognize_authority("ftp://[::1]:8443/x") == RequestAuthority("::1", "[::1]:8443")
    assert recognize_authority("  ftp://Share.Example  ") == RequestAuthority("share.example", "share.example")
    # No percent decoding, no IDNA conversion, no host-syntax guessing.
    assert recognize_authority("https://") is None
    assert recognize_authority("https://%65xample.com/x") is None
    assert recognize_authority("https://exämple.com/x") is None
    assert recognize_authority("https://exa\tmple.com/x") is None
    assert recognize_authority("https://example.com:8\t0/x") is None
    assert recognize_authority("https://user:pw@x.example/x") is None
    assert recognize_authority(42) is None
    assert recognize_authority("") is None
