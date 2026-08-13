"""The §7.4 parity fixture, Python side. The SAME file drives
frontend/src/utils/shareUrl.test.js — never edit one side's expectations."""

from pathlib import Path

import orjson
import pytest

FIXTURE = orjson.loads(
    (Path(__file__).parent / "fixtures" / "share_url_parity.json").read_bytes()
)


@pytest.mark.parametrize("case", FIXTURE["cases"], ids=[c["name"] for c in FIXTURE["cases"]])
def test_build_share_url_parity(case):
    from twicc.core.services.share_url import build_share_url

    assert build_share_url(case["stored"], FIXTURE["url_path"]) == case["expected"]


def test_normalize_empty_stays_empty():
    from twicc.core.services.share_url import normalize_share_base

    assert normalize_share_base("") == ""
    assert normalize_share_base("   ") == ""
    assert normalize_share_base(None) == ""


def test_normalize_invalid_share_base_fails_closed():
    from twicc.core.services.share_url import normalize_share_base

    assert normalize_share_base("ftp://share.example.com") == ""
