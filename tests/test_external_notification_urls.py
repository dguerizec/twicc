from twicc.external_notifications import _build_session_url


def test_session_link_uses_only_a_valid_external_address():
    assert _build_session_url(
        {"publicBaseUrl": "HTTPS://Public.Example.COM/"}, "project", "session",
    ) == "https://public.example.com/project/project/session/session"
    assert _build_session_url(
        {"publicBaseUrl": "ftp://unsafe.example.com"}, "project", "session",
    ) is None
