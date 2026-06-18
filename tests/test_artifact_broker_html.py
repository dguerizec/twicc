"""Phase 3 — the "serve an HTML artifact document" helper: inject the broker
shim as the first child of <head> and lock egress down with the CSP header
(design 2026-06-18 §7 + §8.3)."""

import pytest

from twicc.artifacts.broker_html import (
    ARTIFACT_CSP,
    BROKER_SHIM_URL,
    artifact_html_response,
    inject_broker_shim,
    is_artifact_document_request,
)

_TAG = f'<script src="{BROKER_SHIM_URL}"></script>'.encode()


def test_inject_as_first_child_of_head():
    out = inject_broker_shim(b"<html><head><title>x</title></head><body>hi</body></html>")
    assert _TAG in out
    # First child of <head>: before any existing head content (the <title>).
    assert out.index(_TAG) < out.index(b"<title>")


def test_inject_handles_head_attributes_and_case():
    out = inject_broker_shim(b"<HTML><HEAD data-x='1'><meta charset='utf-8'></HEAD></HTML>")
    assert _TAG in out
    assert out.index(_TAG) < out.index(b"<meta")


def test_inject_falls_back_to_html_when_no_head():
    out = inject_broker_shim(b"<html><body>hi</body></html>")
    assert _TAG in out
    assert out.index(_TAG) < out.index(b"<body>")


def test_inject_prepends_when_no_head_or_html():
    out = inject_broker_shim(b"<div>fragment</div>")
    assert out.startswith(_TAG)


def test_csp_locks_down_egress():
    assert "default-src 'none'" in ARTIFACT_CSP
    assert "connect-src 'none'" in ARTIFACT_CSP   # no fetch/XHR/WS/EventSource/beacon
    assert "worker-src 'none'" in ARTIFACT_CSP    # no separately-policed worker scope
    assert "script-src 'self' 'unsafe-inline'" in ARTIFACT_CSP  # shim + the artifact itself


def test_artifact_html_response_injects_and_sets_csp():
    resp = artifact_html_response(b"<html><head></head><body></body></html>")
    assert resp["Content-Security-Policy"] == ARTIFACT_CSP
    assert resp["Content-Type"].startswith("text/html")
    assert resp["X-Content-Type-Options"] == "nosniff"
    assert _TAG in resp.content


@pytest.mark.parametrize(
    "dest, expected",
    [
        ("iframe", True),     # the in-SPA preview loads the artifact in an <iframe>
        ("document", True),   # the dedicated page opens it as a top-level document
        ("script", False),    # a sub-resource — never wrap/CSP it
        ("style", False),
        ("image", False),
        ("empty", False),     # a fetch()/XHR
        (None, False),        # header absent (non-browser client) → treat as raw
    ],
)
def test_is_artifact_document_request(dest, expected):
    assert is_artifact_document_request(dest) is expected
