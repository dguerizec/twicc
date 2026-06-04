"""Async Django views for the /rpc/ API: dispatch, index, openapi."""

from __future__ import annotations

import asyncio

import orjson
from django.db import close_old_connections
from django.http import HttpRequest, HttpResponse

from twicc.rpc.generator import build_registry, render_argv
from twicc.rpc.invoker import invoke


def _json(payload, *, status: int = 200) -> HttpResponse:
    return HttpResponse(
        orjson.dumps(payload, option=orjson.OPT_INDENT_2),
        status=status,
        content_type="application/json",
    )


def _validate_body(spec, body) -> tuple[bool, dict | None]:
    """Lightweight in-house validation (presence + unknown fields)."""
    if not isinstance(body, dict):
        return False, {"error": "Body must be a JSON object."}
    props = spec.json_schema["properties"]
    unknown = sorted(k for k in body if k not in props)
    if unknown:
        return False, {"error": f"Unknown field(s): {', '.join(unknown)}"}
    missing = [r for r in spec.json_schema.get("required", []) if r not in body]
    if missing:
        return False, {"error": f"Missing required field(s): {', '.join(missing)}"}
    return True, None


def _run_invoke(argv: list[str]):
    # The invoker runs sync command logic (ORM reads / drop-file + poll) in this
    # worker thread. Bracket with close_old_connections() so per-thread DB
    # connections from read commands are not leaked.
    close_old_connections()
    try:
        return invoke(argv)
    finally:
        close_old_connections()


async def dispatch(request: HttpRequest, command_path: str) -> HttpResponse:
    if request.method != "POST":
        return _json({"error": "Use POST."}, status=405)
    spec = build_registry().get(command_path.rstrip("/"))
    if spec is None:
        return _json({"error": f"Unknown command: {command_path}"}, status=404)

    raw = request.body
    if not raw:
        body = {}
    else:
        try:
            body = orjson.loads(raw)
        except ValueError:
            # Only an explicit application/json content-type makes an
            # unparseable body an error; otherwise (test-client multipart,
            # stray form posts) fall back to empty so a valid JSON body sent
            # without the header is still honored above, and junk is ignored.
            if (request.content_type or "").startswith("application/json"):
                return _json({"error": "Malformed JSON body."}, status=400)
            body = {}

    # Forward-compat: the future --remote forwarder sends {"argv": [...]}.
    # The argv form bypasses body→argv rendering, so it must be allowlist-checked
    # itself: non-empty, and its first token must be an exposed top-level command
    # (denylisted commands are absent from the registry, hence rejected).
    if isinstance(body, dict) and set(body) == {"argv"} and isinstance(body["argv"], list):
        argv = [str(x) for x in body["argv"]]
        allowed_roots = {path.split("/", 1)[0] for path in build_registry()}
        if not argv or argv[0] not in allowed_roots:
            return _json(
                {"error": "argv must start with an allowed command."}, status=400
            )
    else:
        ok, err = _validate_body(spec, body)
        if not ok:
            return _json(err, status=400)
        argv = render_argv(spec, body)

    result = await asyncio.to_thread(_run_invoke, argv)
    return _json({"exit_code": result.exit_code, "result": result.result, "error": result.error})


async def index(request: HttpRequest) -> HttpResponse:
    reg = build_registry()
    return _json(
        {
            "commands": [{"path": p, "summary": s.summary} for p, s in sorted(reg.items())],
            "openapi": "/rpc/openapi.json",
        }
    )


async def openapi(request: HttpRequest) -> HttpResponse:
    from twicc.rpc.openapi import build_openapi

    return _json(build_openapi(build_registry()))
