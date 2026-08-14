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


from twicc.core.services.origin_policy import (  # noqa: E402  (grouped with the module under test)
    OriginPolicy,
    build_origin_policy,
    classify_request,
    get_origin_policy,
)


def test_policy_valid_trio():
    policy = build_origin_policy("https://app.example", "https://share.example", "https://peer.example:8443")
    assert policy == OriginPolicy(
        external_authority="app.example",
        share_hostname="share.example",
        dedicated_peer_authority="peer.example:8443",
        shared_peer_authority=None,
        quarantined_hostnames=frozenset(),
        quarantined_authorities=frozenset(),
    )


def test_policy_shared_peer():
    policy = build_origin_policy("https://x.example", "", "https://x.example")
    assert policy.shared_peer_authority == "x.example"
    assert policy.dedicated_peer_authority is None


def test_policy_empty_settings_disable_their_surfaces():
    policy = build_origin_policy("", "", "")
    assert policy.share_hostname is None
    assert policy.dedicated_peer_authority is None and policy.shared_peer_authority is None
    assert policy.quarantined_hostnames == frozenset() and policy.quarantined_authorities == frozenset()


def test_policy_empty_external_makes_peer_dedicated():
    policy = build_origin_policy("", "", "https://peer.example")
    assert policy.dedicated_peer_authority == "peer.example"
    assert policy.external_authority is None


def test_policy_invalid_share_quarantines_recognizable_hostname():
    policy = build_origin_policy("", "ftp://share.example", "")
    assert policy.share_hostname is None
    assert policy.quarantined_hostnames == frozenset({"share.example"})
    # Unrecognizable: surface disabled, nothing to quarantine.
    policy = build_origin_policy("", "https://", "")
    assert policy.share_hostname is None
    assert policy.quarantined_hostnames == frozenset()


def test_policy_invalid_peer_quarantines_recognizable_authority():
    policy = build_origin_policy("", "", "https://peer.example:8443/forbidden")
    assert policy.dedicated_peer_authority is None and policy.shared_peer_authority is None
    assert policy.quarantined_authorities == frozenset({"peer.example:8443"})


def test_policy_unrecognizable_invalid_peer_disables_without_quarantine():
    policy = build_origin_policy("https://app.example", "https://share.example", "https://")
    assert policy.external_authority == "app.example"
    assert policy.share_hostname == "share.example"
    assert policy.dedicated_peer_authority is None and policy.shared_peer_authority is None
    assert policy.quarantined_hostnames == frozenset()
    assert policy.quarantined_authorities == frozenset()


def test_policy_share_conflicts_use_recognized_operands():
    # Share/External conflict: Share disabled, its hostname quarantined; the
    # exact External authority survives via classifier precedence.
    policy = build_origin_policy("https://x.example", "https://x.example:9443", "")
    assert policy.share_hostname is None
    assert policy.quarantined_hostnames == frozenset({"x.example"})
    assert policy.external_authority == "x.example"
    # Share/Peer conflict, with a recognizable invalid peer operand.
    policy = build_origin_policy("", "https://x.example", "https://x.example/forbidden")
    assert policy.share_hostname is None
    assert policy.dedicated_peer_authority is None and policy.shared_peer_authority is None
    assert policy.quarantined_hostnames == frozenset({"x.example"})
    assert policy.quarantined_authorities == frozenset({"x.example"})
    # A recognizable invalid External operand also disables conflicting Share.
    policy = build_origin_policy("ftp://x.example", "https://x.example", "")
    assert policy.external_authority is None
    assert policy.share_hostname is None
    assert policy.quarantined_hostnames == frozenset({"x.example"})


def test_policy_ambiguous_authority_disables_peer():
    policy = build_origin_policy("https://x.example", "", "http://x.example")
    assert policy.dedicated_peer_authority is None and policy.shared_peer_authority is None
    # The peer authority equals the valid External authority, so precedence
    # discards it from the authority-quarantine set (§11).
    assert policy.quarantined_authorities == frozenset()
    assert policy.external_authority == "x.example"


def test_policy_invalid_external_disables_peer_classification():
    policy = build_origin_policy("https://", "", "https://peer.example")
    assert policy.external_authority is None
    assert policy.dedicated_peer_authority is None and policy.shared_peer_authority is None
    assert policy.quarantined_authorities == frozenset({"peer.example"})


def test_policy_interaction_case_1():
    # Spec §13.2 interaction basis, case 1.
    policy = build_origin_policy("https://app.example", "ftp://share.example", "https://peer.example/forbidden")
    assert policy.share_hostname is None
    assert policy.dedicated_peer_authority is None and policy.shared_peer_authority is None
    assert policy.quarantined_hostnames == frozenset({"share.example"})
    assert policy.quarantined_authorities == frozenset({"peer.example"})
    assert policy.external_authority == "app.example"


def test_policy_interaction_case_2():
    # Spec §13.2 interaction basis, case 2: the recognizable invalid Peer
    # operand joins the Share-and-Peer conflict and takes valid Share down.
    policy = build_origin_policy("https://", "https://share.example", "https://share.example/forbidden")
    assert policy.share_hostname is None
    assert policy.dedicated_peer_authority is None and policy.shared_peer_authority is None
    assert policy.quarantined_hostnames == frozenset({"share.example"})
    assert policy.quarantined_authorities == frozenset({"share.example"})
    assert policy.external_authority is None


def test_policy_interaction_case_3():
    # Spec §13.2 interaction basis, case 3: External precedence removes only
    # app.example from the authority-quarantine set.
    policy = build_origin_policy("https://app.example", "ftp://share.example", "https://app.example/forbidden")
    assert policy.share_hostname is None
    assert policy.dedicated_peer_authority is None and policy.shared_peer_authority is None
    assert policy.quarantined_hostnames == frozenset({"share.example"})
    assert policy.quarantined_authorities == frozenset()
    assert policy.external_authority == "app.example"


def _authority(value):
    return parse_request_authority(value)


def test_classify_request_routing_table():
    policy = build_origin_policy("https://app.example", "https://share.example", "https://peer.example:8443")
    # Share hostname → share surface, any port, both protocols.
    assert classify_request(policy, _authority("share.example"), "/share/tok/", "http") == "share_surface"
    assert classify_request(policy, _authority("share.example:9999"), "/share/tok/", "http") == "share_surface"
    assert classify_request(policy, _authority("share.example"), "/ws/share/tok/", "websocket") == "share_surface"
    assert classify_request(policy, _authority("share.example"), "/peer/messages/", "http") == "share_surface"
    assert classify_request(policy, _authority("share.example"), "/api/sessions/", "http") == "share_surface"
    assert classify_request(policy, _authority("share.example"), "/ws/", "websocket") == "share_surface"
    # Dedicated Peer authority → only /peer/ HTTP; no WebSocket.
    assert classify_request(policy, _authority("peer.example:8443"), "/peer/messages/", "http") == "inner_app"
    assert classify_request(policy, _authority("peer.example:8443"), "/", "http") == "reject"
    assert classify_request(policy, _authority("peer.example:8443"), "/static/app.js", "http") == "reject"
    assert classify_request(policy, _authority("peer.example:8443"), "/mcp", "http") == "reject"
    assert classify_request(policy, _authority("peer.example:8443"), "/share/tok/", "http") == "reject"
    assert classify_request(policy, _authority("peer.example:8443"), "/ws/", "websocket") == "reject"
    # The peer hostname WITHOUT its port is just another authority.
    assert classify_request(policy, _authority("peer.example"), "/peer/messages/", "http") == "reject"
    assert classify_request(policy, _authority("peer.example"), "/api/sessions/", "http") == "inner_app"
    # External and every other authority → full app, hidden share, no /peer/.
    assert classify_request(policy, _authority("app.example"), "/api/sessions/", "http") == "inner_app"
    assert classify_request(policy, _authority("app.example"), "/peer/messages/", "http") == "reject"
    assert classify_request(policy, _authority("localhost:3501"), "/api/sessions/", "http") == "inner_app"
    assert classify_request(policy, _authority("localhost:3501"), "/peer/messages/", "http") == "reject"
    assert classify_request(policy, _authority("app.example"), "/share/tok/", "http") == "reject"
    assert classify_request(policy, _authority("app.example"), "/_twicc/share/x.js", "http") == "reject"
    assert classify_request(policy, _authority("app.example"), "/ws/share/tok/", "websocket") == "reject"
    assert classify_request(policy, _authority("app.example"), "/ws/", "websocket") == "inner_app"
    # No valid Host → reject.
    assert classify_request(policy, None, "/api/sessions/", "http") == "reject"


def test_classify_request_shared_peer():
    policy = build_origin_policy("https://x.example", "", "https://x.example")
    assert classify_request(policy, _authority("x.example"), "/peer/messages/", "http") == "inner_app"
    assert classify_request(policy, _authority("x.example"), "/api/sessions/", "http") == "inner_app"
    assert classify_request(policy, _authority("x.example"), "/share/tok/", "http") == "reject"
    assert classify_request(policy, _authority("x.example"), "/_twicc/share/x.js", "http") == "reject"
    assert classify_request(policy, _authority("x.example"), "/ws/share/tok/", "websocket") == "reject"
    assert classify_request(policy, _authority("x.example"), "/ws/", "websocket") == "inner_app"
    assert classify_request(policy, _authority("other.example"), "/peer/messages/", "http") == "reject"
    assert classify_request(policy, _authority("other.example"), "/api/sessions/", "http") == "inner_app"


def test_classify_request_quarantine_and_precedence():
    policy = build_origin_policy("https://x.example", "https://x.example:9443", "")
    # Hostname quarantine matches every port…
    assert classify_request(policy, _authority("x.example:9443"), "/api/sessions/", "http") == "reject"
    assert classify_request(policy, _authority("x.example:1234"), "/ws/", "websocket") == "reject"
    # …except the exact valid External authority (§11 precedence).
    assert classify_request(policy, _authority("x.example"), "/api/sessions/", "http") == "inner_app"
    # Peer stays hidden there: no valid shared Peer origin exists.
    assert classify_request(policy, _authority("x.example"), "/peer/messages/", "http") == "reject"


def test_get_origin_policy_memoizes_and_tracks_changes():
    settings = {"publicBaseUrl": "https://app.example", "shareBaseUrl": "", "peerBaseUrl": ""}
    first = get_origin_policy(settings)
    assert get_origin_policy(dict(settings)) is first
    changed = get_origin_policy({**settings, "peerBaseUrl": "https://peer.example"})
    assert changed is not first
    assert changed.dedicated_peer_authority == "peer.example"
