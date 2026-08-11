"""Tool derivation from the Click tree: selection, naming, metadata."""

from twicc.mcp.tools import build_mcp_registry, iter_mcp_tools
from twicc.rpc.generator import build_registry


def test_selection_matches_the_skill_surface():
    reg = build_mcp_registry()
    paths = set(reg)
    assert "whoami" in paths                       # re-admitted local-only
    assert not any(p.split("/")[0] == "settings" for p in paths)
    # ``share`` is agent-gated server-side (agent-sharing design): the tools
    # are ALWAYS exposed — a disabled setting rejects at call time (A4).
    assert any(p.split("/")[0] == "share" for p in paths)
    assert "share/create" not in paths  # the group is no longer callable (silent no-op fix)
    for banned in ("password", "token", "run", "claude", "codex"):
        assert not any(p.split("/")[0] == banned for p in paths)
    # Everything else from the RPC registry is present.
    rpc_paths = {p for p in build_registry() if p.split("/")[0] != "settings"}
    assert rpc_paths <= paths


def test_tool_names_are_mcp_safe_and_bijective():
    tools = iter_mcp_tools()
    names = [t.name for t in tools]
    assert len(names) == len(set(names))
    for n in names:
        assert n.replace("_", "").isalnum() and n == n.lower()
    assert "create_session" in names
    assert "update_session_settings" in names
    assert "session_content" in names
    assert "share_create_session" in names
    assert "share_create_artifact" in names
    assert "share_create" not in names


def test_schemas_and_descriptions():
    by_name = {t.name: t for t in iter_mcp_tools()}
    reg = build_mcp_registry()
    assert by_name["create_session"].inputSchema == reg["create-session"].json_schema
    assert by_name["create_session"].description  # full help, non-empty
    assert len(by_name["create_session"].description) > len(reg["create-session"].summary)


def test_annotations_and_always_load():
    by_name = {t.name: t for t in iter_mcp_tools()}
    assert by_name["sessions"].annotations.readOnlyHint is True
    assert by_name["create_session"].annotations.readOnlyHint is False
    assert (by_name["whoami"].meta or {}).get("anthropic/alwaysLoad") is True
    assert (by_name["update_workspace"].meta or {}).get("anthropic/alwaysLoad") is None
    assert by_name["share"].annotations.readOnlyHint is True
    assert by_name["share_show"].annotations.readOnlyHint is True
    for name in (
        "share_create_session",
        "share_create_artifact",
        "share_update",
        "share_revoke",
        "share_unrevoke",
        "share_delete",
        "share_propagate",
    ):
        assert by_name[name].annotations.readOnlyHint is False
