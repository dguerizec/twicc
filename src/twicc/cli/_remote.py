"""Client-side ``--remote`` forwarder — pre-flight half.

This module forwards a local ``twicc`` command to a *remote* TwiCC's ``/rpc/``
HTTP API. It is the inverse of the server's generator: it reuses the very same
``build_registry()`` (the canonical command↔URL mapping) so what maps
command→URL on the server maps argv→URL on the client.

This file implements the **pre-flight** half (path resolution + local
rejections), **attachment inlining**, and the **network** half — the HTTP
call to ``/rpc/`` and the envelope→exit mapping (:func:`forward`). The
entry-point wiring (intercepting ``--remote`` before Typer) lands in a later
task (R5). The public surface is intentionally small:

- :class:`RemoteUsageError` / :class:`RemoteTransportError` — error types with
  reserved exit codes, so callers can map a failure to a process exit code;
- :class:`Resolved` — the result of resolving an argv to a registry route;
- :func:`resolve_command` — argv → :class:`Resolved` (or a usage error);
- :func:`reject_host_bound` — reject ``self`` / ``parent`` where they reference
  the *local* session and have no meaning on a remote;
- :func:`inline_attachments` — rewrite each ``--attach <local path>`` into a
  ``data:`` URI so the server can decode it without a shared filesystem;
- :func:`forward` — resolve, pre-flight, POST to ``<base>/rpc/<path>`` and map
  the ``{exit_code, result, error}`` envelope to local stdout/stderr/exit;
- :data:`HOST_BOUND_PARAMS` — the audited set of param names that accept the
  ``self`` / ``parent`` keywords.
"""

from __future__ import annotations

import base64
import os
import sys
from typing import NamedTuple

import click
import httpx
import orjson

from twicc.cli._drop_request.attachments import _sniff_mime
from twicc.cli._local_only import LOCAL_ONLY_COMMANDS
from twicc.rpc.generator import CommandSpec, build_registry
from twicc.rpc.invoker import get_command


class RemoteUsageError(Exception):
    """Client-side misuse of ``--remote`` (no HTTP attempted).

    Raised for a local-only command, an unknown command, or a ``self`` /
    ``parent`` reference that only makes sense on the local host. The caller
    prints ``twicc: <message>`` to stderr and exits with :attr:`exit_code`.
    """

    exit_code = 2


class RemoteTransportError(Exception):
    """Transport / remote-layer failure (connect, DNS, timeout, non-2xx, …).

    Defined here for the network half (R4) to raise; the pre-flight code never
    raises it. The caller prints ``twicc: <message>`` to stderr and exits with
    :attr:`exit_code` — a code reserved for transport failures, distinct from
    the command vocabulary (``0/1/2/5/64``).
    """

    exit_code = 7


class Resolved(NamedTuple):
    """A resolved remote command: its registry path, spec, and bound params."""

    path: str                # registry key, e.g. "session/content"
    spec: CommandSpec        # the matching CommandSpec from build_registry()
    params: dict             # merged Click params across every navigated level


# Param names that accept the host-bound ``self`` / ``parent`` keywords.
#
# Audited from the CLI command signatures:
# - Filiation filter options ``--spawned-by`` / ``--spawn-tree`` /
#   ``--descendants`` (Click param names ``spawned_by`` / ``spawn_tree`` /
#   ``descendants``) — interpreted by ``resolve_spawned_by_filter`` /
#   ``resolve_spawn_tree_filter`` / ``resolve_descendants_filter`` in
#   ``cli/_drop_request/whoami.py``. Present on ``sessions``, ``search``,
#   ``processes`` (all three keywords), and ``processes stop`` / ``processes
#   wait`` (``spawned_by`` / ``descendants`` only). ``spawn_tree`` rejects
#   ``parent`` itself, but it still accepts ``self`` — so it belongs here.
# - Session-id positionals (Click param name ``session_id``). ``send-message``,
#   ``update-session`` and ``topology`` truly RESOLVE ``self`` / ``parent`` from
#   the local session, so they must be rejected over --remote. ``session`` and
#   ``process`` (plus ``process stop`` / ``process wait``) treat the id literally
#   (a session *named* "self" — implausible), so rejecting them is conservative
#   but harmless: such an id is meaningless against a remote anyway.
# - Variadic session-id positionals: ``processes stop`` (``session_ids``),
#   ``processes wait`` (``items`` — a mixed list of ids and statuses), and
#   ``update-sessions`` (``session_ids`` — every batch sub-command). Like
#   ``update-session``, ``update-sessions`` truly RESOLVES ``self`` in its
#   explicit ids, so rejecting it over --remote is meaningful, not just
#   conservative. The batch readers ``sessions get`` / ``processes get`` use
#   plain ``session_ids`` too; including the name stays harmless there (they
#   don't special-case ``self`` / ``parent``, so the value would never
#   legitimately be either).
#
# Only these specific params are inspected — never a blind argv scan — so a
# free-text value (e.g. a message body that happens to be "self") is untouched.
HOST_BOUND_PARAMS: frozenset[str] = frozenset(
    {
        # filiation filter options
        "spawned_by",
        "spawn_tree",
        "descendants",
        # session-id positionals (scalar)
        "session_id",
        # session-id positionals (variadic / mixed)
        "session_ids",
        "items",
    }
)

_HOST_BOUND_KEYWORDS = frozenset({"self", "parent"})


def _make_context(cmd: click.Command, args: list[str], parent: click.Context | None) -> click.Context:
    """Parse ``args`` for ``cmd`` in resilient mode (missing args don't raise)."""
    return cmd.make_context(cmd.name or "twicc", list(args), parent=parent, resilient_parsing=True)


def _remaining_tokens(ctx: click.Context) -> list[str]:
    """Tokens left for a group after parsing its own options/arguments.

    ``protected_args`` holds the (deprecated, Click < 9) reserved subcommand
    token; ``args`` holds the rest. Concatenating them gives the tokens that
    the group must resolve into ``<subcommand> <rest…>``.
    """
    return list(ctx.protected_args) + list(ctx.args)


def resolve_command(argv: list[str]) -> Resolved:
    """Resolve a CLI ``argv`` to a remote route (path + spec + bound params).

    Navigates the Click tree from :func:`get_command` so that group arguments
    interleaved between subcommand tokens (e.g. ``session <id> content`` →
    ``session/content``) are consumed correctly and the resulting path equals a
    :func:`build_registry` key. The merged Click params (across every level)
    are returned so :func:`reject_host_bound` can inspect the bound values.

    Raises :class:`RemoteUsageError` for an empty argv, a local-only root, an
    unknown command, or a genuinely malformed command (no raw Click traceback
    leaks out).
    """
    if not argv:
        raise RemoteUsageError("no command given; nothing to forward to --remote")

    root_token = argv[0]
    if root_token in LOCAL_ONLY_COMMANDS:
        # Local-only roots are absent from the registry; the explicit check
        # produces a helpful message instead of a generic "unknown command".
        raise RemoteUsageError(
            f"'{root_token}' is a local-only command; not available over --remote"
        )

    try:
        path, params = _navigate(argv)
    except RemoteUsageError:
        raise
    except Exception as exc:  # surface any Click parse failure as a usage error
        raise RemoteUsageError(
            f"could not parse command: {' '.join(argv)} ({type(exc).__name__}: {exc})"
        ) from exc

    registry = build_registry()
    spec = registry.get(path)
    if spec is None:
        raise RemoteUsageError(f"unknown command: {' '.join(argv)}")

    return Resolved(path=path, spec=spec, params=params)


def _navigate(argv: list[str]) -> tuple[str, dict]:
    """Walk the Click tree, returning the registry path and merged params.

    Each level is parsed with ``make_context`` so a group consumes its own
    options/arguments before its subcommand token is resolved — this is what
    keeps interleaved group arguments (the ``<id>`` in ``session <id> content``)
    out of the path.

    A group that carries a *required positional* (``session`` / ``process`` /
    ``update-session``) takes the id *before* its subcommand — the canonical
    ``<group> <id> <subcommand>`` order, which navigates cleanly (the id binds to
    the group's positional, then the subcommand resolves). The wrong order
    (``<group> <subcommand> <id>``) is rejected as a usage error here, exactly as
    the local CLI and the remote server reject it — the forwarder mirrors CLI
    semantics rather than silently "fixing" the order.
    """
    root = get_command()
    cmd: click.Command = root
    args = list(argv)
    parent: click.Context | None = None
    path_tokens: list[str] = []
    merged: dict = {}

    while True:
        ctx = _make_context(cmd, args, parent)
        if not isinstance(cmd, click.Group):
            merged.update(ctx.params)
            break

        remaining = _remaining_tokens(ctx)
        if not remaining:
            # Group invoked without a subcommand: it is itself a route
            # (invoke_without_command). Its own params are the bound values.
            merged.update(ctx.params)
            break

        name, sub, rest = cmd.resolve_command(ctx, remaining)
        if sub is None:
            # In resilient mode resolve_command returns sub=None for an unknown
            # token instead of raising. This also covers a subcommand placed
            # before a group's required positional (wrong order, e.g.
            # ``process wait <id>``): the positional swallows the subcommand and
            # the next token is not a command — rejected here exactly as the
            # local CLI and the remote server would reject it.
            raise RemoteUsageError(f"unknown command: {' '.join(argv)}")

        merged.update(ctx.params)
        path_tokens.append(name)
        cmd = sub
        args = rest
        parent = ctx

    return "/".join(path_tokens), merged


def reject_host_bound(resolved: Resolved) -> None:
    """Reject ``self`` / ``parent`` where they reference the local session.

    Inspects only the audited :data:`HOST_BOUND_PARAMS` of the resolved command
    (handling scalars and tuples/lists). A free-text value that merely equals
    ``self`` / ``parent`` (e.g. a message body) is never affected.

    Raises :class:`RemoteUsageError` if any host-bound param is set to
    ``self`` / ``parent``.
    """
    for name in HOST_BOUND_PARAMS:
        if name not in resolved.params:
            continue
        value = resolved.params[name]
        if value is None:
            continue
        values = value if isinstance(value, (list, tuple)) else (value,)
        for item in values:
            if item in _HOST_BOUND_KEYWORDS:
                raise RemoteUsageError(
                    "self/parent reference the local session and have no "
                    "meaning over --remote; pass an explicit session id"
                )


# Click param name of the repeatable ``--attach`` option, present on both
# ``create-session`` and ``send-message`` (audited from their command
# signatures: option string ``--attach``, no short alias). The forwarder never
# hardcodes the option string — it reads it from the resolved spec's ParamSpec,
# so a rename of the option stays correct.
_ATTACH_PARAM_NAME = "attach"


def _attach_option_strings(resolved: Resolved) -> list[str] | None:
    """Return the option string(s) of the attach option, or None if absent.

    Looks up the audited :data:`_ATTACH_PARAM_NAME` among the resolved command's
    options. ``ParamSpec`` only exposes the primary (``opt``) and the off-form
    secondary (``secondary_opt``) — there is no separate "short alias" field, so
    a short alias would only be handled if it happened to be the primary opt.
    For ``--attach`` the primary is the long form and there is no short alias.
    """
    for opt in resolved.spec.options:
        if opt.name == _ATTACH_PARAM_NAME:
            return [o for o in (opt.opt, opt.secondary_opt) if o]
    return None


def _inline_one(value: str) -> str:
    """Rewrite a single attach value to a ``data:`` URI (pass-through if already one).

    A value already in ``data:`` form is returned unchanged. Otherwise the value
    is treated as a local file path (relative paths are read against the client's
    cwd): its bytes are read, the MIME is sniffed (falling back to
    ``application/octet-stream`` when the sniffer can't recognize the bytes —
    same fallback the server uses for an unknown declared media type), and the
    payload is base64-encoded into ``data:<mime>;base64,<payload>``. The declared
    MIME is only a label; the server re-sniffs from the decoded bytes.

    Raises :class:`RemoteUsageError` if the file is missing or unreadable
    (a client-side error — no HTTP is attempted).
    """
    if value.startswith("data:"):
        return value
    try:
        with open(value, "rb") as f:
            data = f.read()
    except OSError:
        raise RemoteUsageError(f"attachment not found: {value}")
    mime = _sniff_mime(data) or "application/octet-stream"
    payload = base64.b64encode(data).decode("ascii")
    return f"data:{mime};base64,{payload}"


def inline_attachments(argv: list[str], resolved: Resolved) -> list[str]:
    """Return a copy of ``argv`` with each ``--attach <local path>`` inlined.

    Over ``--remote``, the server only sees the forwarded argv — it has no
    access to the client's filesystem. So every ``--attach`` value that names a
    *local* file is rewritten to a ``data:<mime>;base64,<payload>`` URI that the
    server decodes unchanged (it re-sniffs the MIME, so the label is advisory).

    Both token forms are handled: ``--attach VALUE`` (two tokens) and
    ``--attach=VALUE`` (one token). A value already in ``data:`` form is left as
    is. Commands without an attach option return ``argv`` unchanged.

    No size pre-check is done here: an oversized payload fails later at the HTTP
    layer and surfaces as a remote error. The original ``argv`` is never mutated
    — a fresh list is returned, preserving every other token and the order.

    Raises :class:`RemoteUsageError` if an ``--attach`` file is missing or
    unreadable (a client-side error — no HTTP is attempted).
    """
    option_strings = _attach_option_strings(resolved)
    if not option_strings:
        return list(argv)

    eq_prefixes = tuple(f"{opt}=" for opt in option_strings)

    out: list[str] = []
    i = 0
    n = len(argv)
    while i < n:
        token = argv[i]
        if token in option_strings:
            # Two-token form: "--attach" "VALUE". Keep the option token, then
            # rewrite the next token (the value). A dangling option with no
            # value (last token) is left untouched — Click would reject it
            # anyway; the forwarder does not invent a value.
            out.append(token)
            if i + 1 < n:
                out.append(_inline_one(argv[i + 1]))
                i += 2
            else:
                i += 1
            continue
        matched_prefix = next((p for p in eq_prefixes if token.startswith(p)), None)
        if matched_prefix is not None:
            # One-token form: "--attach=VALUE". Rewrite only the value part,
            # preserving the exact "--attach=" prefix that was used.
            value = token[len(matched_prefix):]
            out.append(f"{matched_prefix}{_inline_one(value)}")
            i += 1
            continue
        out.append(token)
        i += 1
    return out


# Registry paths of the blocking long-poll commands. The server holds the
# response open up to the command's own ``--timeout``, so the HTTP read timeout
# must be at least that long (+ a margin) — see :func:`_request_timeout`.
_WAIT_PATHS: frozenset[str] = frozenset({"process/wait", "processes/wait"})

# Read-timeout margin (seconds) added on top of a wait command's ``--timeout``
# so the local read does not race the server's own deadline.
_WAIT_TIMEOUT_MARGIN = 15.0

# Default connect/read timeout (seconds) for ordinary (non-wait) commands. Read
# commands and drop-and-poll mutations all complete well within this.
_DEFAULT_TIMEOUT = 30.0

# Connection-establishment timeout (seconds). Kept short and constant: it bounds
# only the TCP/TLS handshake, never the server's processing time. The per-request
# read timeout (above) is what accommodates a long-poll ``wait``.
_CONNECT_TIMEOUT = 10.0


def _endpoint_url(base: str, path: str) -> str:
    """Join the remote base URL and the registry ``path`` into a ``/rpc/`` URL.

    The base may or may not carry a trailing slash (``http://box:3501`` or
    ``http://box:3501/``); either way the result is ``<base>/rpc/<path>`` with a
    single separator and no doubled ``/rpc/``. ``path`` is a clean registry key
    (e.g. ``session/content``), never user free-text, so no escaping is needed.
    """
    return f"{base.rstrip('/')}/rpc/{path}"


def _request_timeout(resolved: Resolved) -> httpx.Timeout:
    """Pick the httpx timeout for this command.

    For the long-poll ``wait`` commands the server holds the response up to the
    command's ``--timeout``; the local **read** timeout is therefore set to that
    value plus :data:`_WAIT_TIMEOUT_MARGIN` so the client never gives up before
    the server answers. The bound ``timeout`` param already folds in the Click
    default when the flag is omitted — but ``wait``'s ``--timeout`` is required,
    so an omitted flag leaves it ``None`` (the server will reject the missing
    option fast); in that case fall back to :data:`_DEFAULT_TIMEOUT`.

    Every other command uses :data:`_DEFAULT_TIMEOUT` for both connect and read.
    The connect timeout is always the short, constant :data:`_CONNECT_TIMEOUT`.
    """
    read = _DEFAULT_TIMEOUT
    if resolved.path in _WAIT_PATHS:
        command_timeout = resolved.params.get("timeout")
        if isinstance(command_timeout, (int, float)) and command_timeout > 0:
            read = float(command_timeout) + _WAIT_TIMEOUT_MARGIN
    return httpx.Timeout(read, connect=_CONNECT_TIMEOUT)


def _error_detail(response: httpx.Response) -> str | None:
    """Best-effort extraction of a JSON ``{"error": …}`` detail from a response.

    The ``/rpc/`` error responses (401/404/400/405/…) all carry a JSON body of
    the shape ``{"error": "<message>"}``. Return that message when present;
    otherwise ``None`` so the caller can fall back to a generic phrasing rather
    than echo a raw HTML/empty body.
    """
    try:
        body = orjson.loads(response.content)
    except ValueError:  # orjson.JSONDecodeError is a ValueError subclass
        return None
    if isinstance(body, dict):
        detail = body.get("error")
        if isinstance(detail, str) and detail:
            return detail
    return None


def _map_status_error(url: str, path: str, response: httpx.Response) -> RemoteTransportError:
    """Turn a non-200 HTTP response into a :class:`RemoteTransportError`.

    Special-cases the two operator-facing failures — ``401`` (the remote
    requires a token we did not / wrongly supplied) and ``404`` (the remote does
    not expose this command, e.g. version skew) — and otherwise reports the raw
    status plus any JSON ``error`` detail.
    """
    status = response.status_code
    detail = _error_detail(response)
    if status == 401:
        return RemoteTransportError(
            "authentication rejected — check --remote-token / TWICC_REMOTE_TOKEN"
            + (f" ({detail})" if detail else "")
        )
    if status == 404:
        return RemoteTransportError(f"remote has no such command: {path}")
    suffix = f": {detail}" if detail else ""
    return RemoteTransportError(f"remote returned HTTP {status}{suffix}")


def forward(url: str, token: str | None, argv: list[str]) -> int:
    """Forward a CLI command to a remote TwiCC ``/rpc/`` and map the result.

    Resolves ``argv`` against the local registry, applies the same pre-flight
    rejections as a local run would, inlines local ``--attach`` files, then POSTs
    ``{"argv": [...]}`` to ``<url>/rpc/<path>``. On a 200 envelope it reproduces
    the command's own output exactly (``result`` to stdout in the same indented
    JSON shape as :func:`twicc.cli._output.emit_json`, ``error`` to stderr) and
    returns the remote ``exit_code`` — so a script behaves identically whether
    run locally or via ``--remote``.

    Returns the remote command's ``exit_code`` on completion.

    Raises :class:`RemoteUsageError` for a local misuse caught pre-flight
    (unknown / local-only command, host-bound ``self`` / ``parent``, missing
    attachment), and :class:`RemoteTransportError` for any transport- or
    remote-layer failure (cannot connect, DNS, timeout, non-200, malformed
    response). The caller prints ``twicc: <message>`` to stderr and exits with
    the exception's ``exit_code``; :func:`forward` itself only ever writes the
    *command's* own ``result`` / ``error``.
    """
    resolved = resolve_command(argv)
    reject_host_bound(resolved)
    argv2 = inline_attachments(argv, resolved)

    endpoint = _endpoint_url(url, resolved.path)
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = orjson.dumps({"argv": argv2})

    try:
        with httpx.Client(timeout=_request_timeout(resolved)) as client:
            response = client.post(endpoint, content=body, headers=headers)
    except httpx.TimeoutException as exc:
        raise RemoteTransportError(f"remote timed out: {exc}") from exc
    except httpx.ConnectError as exc:
        raise RemoteTransportError(f"cannot reach remote {url}: {exc}") from exc
    except httpx.RequestError as exc:
        # Any other transport-level failure (read/write error, protocol error,
        # invalid URL, …) — never a server reply, so it is a transport error.
        raise RemoteTransportError(f"cannot reach remote {url}: {exc}") from exc

    if response.status_code != 200:
        raise _map_status_error(url, resolved.path, response)

    try:
        envelope = orjson.loads(response.content)
    except ValueError as exc:  # orjson.JSONDecodeError is a ValueError subclass
        raise RemoteTransportError(f"malformed response from remote: {exc}") from exc
    if not isinstance(envelope, dict) or "exit_code" not in envelope:
        raise RemoteTransportError("malformed response from remote: missing exit_code")

    result = envelope.get("result")
    # Invariant: result is None only on the error path — no command emits
    # emit_json(None), so a successful emit_json([])/{}/0/"" is not None and is
    # printed. Byte-for-byte identical to emit_json() so remote output matches a
    # local run exactly (indented UTF-8 JSON + trailing newline, on stdout).
    if result is not None:
        sys.stdout.buffer.write(orjson.dumps(result, option=orjson.OPT_INDENT_2))
        sys.stdout.buffer.write(b"\n")
    error = envelope.get("error")
    if error is not None:
        print(error, file=sys.stderr)

    return envelope["exit_code"]


# Environment fallbacks for the URL and token (see :func:`parse_remote_invocation`).
# An *empty* value is treated as unset for both, so an exported-but-blank var never
# masks the real precedence (inline value, then env, then unset).
_ENV_URL = "TWICC_REMOTE_URL"
_ENV_TOKEN = "TWICC_REMOTE_TOKEN"


def parse_remote_invocation(argv: list[str]) -> tuple[str, str | None, list[str]] | None:
    """Front-parse the leading global flags of ``argv`` for ``--remote`` mode.

    ``argv`` is ``sys.argv[1:]``. Only the *leading* global flags are parsed —
    parsing stops at the first token that is neither a recognized global flag nor
    a value consumed by one. This deliberately avoids colliding with a command's
    own free-text arguments (a ``--remote`` buried inside a message body is left
    alone, so the local app runs normally).

    Recognized leading flags, in any order:

    - ``--remote=<url>`` sets the URL inline; ``--remote <url>`` sets it only when
      the next token looks like a URL (contains ``://``) and is not itself a flag
      — command names never contain ``://``, so the space form never swallows a
      subcommand. A **bare** ``--remote`` (no URL token after it) leaves the URL
      unset here and falls back to :data:`_ENV_URL` below.
    - ``--remote-token=<tok>`` / ``--remote-token <tok>`` set the token (the space
      form always consumes the next token).

    Returns ``None`` when ``--remote`` is absent from the leading flags (the
    caller then runs the normal local app). When ``--remote`` is present, returns
    ``(url, token, command_argv)`` where ``command_argv`` is everything after the
    consumed leading flags.

    URL precedence: inline value, else :data:`_ENV_URL` (empty treated as unset),
    else :class:`RemoteUsageError`. Token precedence: inline value, else
    :data:`_ENV_TOKEN` (empty treated as ``None``), else ``None``.
    """
    remote_seen = False
    url: str | None = None
    token: str | None = None

    i = 0
    n = len(argv)
    while i < n:
        token_str = argv[i]

        if token_str.startswith("--remote-token="):
            token = token_str[len("--remote-token="):]
            i += 1
            continue
        if token_str == "--remote-token":
            # Space form: always consume the next token as the value (if any).
            if i + 1 < n:
                token = argv[i + 1]
                i += 2
            else:
                i += 1
            continue

        if token_str.startswith("--remote="):
            remote_seen = True
            url = token_str[len("--remote="):]
            i += 1
            continue
        if token_str == "--remote":
            remote_seen = True
            # Space form: only consume the next token as the URL when it actually
            # looks like one (contains "://") and is not another flag. Otherwise
            # this is a bare --remote and the URL comes from the environment.
            nxt = argv[i + 1] if i + 1 < n else None
            if nxt is not None and "://" in nxt and not nxt.startswith("-"):
                url = nxt
                i += 2
            else:
                i += 1
            continue

        # First token that is not a leading global flag (nor a consumed value):
        # stop front-parsing — everything from here is the command.
        break

    if not remote_seen:
        return None

    if not url:
        env_url = os.environ.get(_ENV_URL)
        url = env_url if env_url else None
    if not url:
        raise RemoteUsageError(
            "--remote given without a URL (pass --remote <url> or set TWICC_REMOTE_URL)"
        )
    if "://" not in url:
        # A scheme-less value (e.g. "box:3501") would otherwise reach httpx as a
        # relative/unsupported URL and surface as an opaque transport error.
        raise RemoteUsageError(
            f"remote URL must include a scheme (e.g. http://): {url}"
        )

    if not token:
        env_token = os.environ.get(_ENV_TOKEN)
        token = env_token if env_token else None

    command_argv = argv[i:]
    return url, token, command_argv


def maybe_forward(argv: list[str]) -> int | None:
    """Forward ``argv`` to a remote ``/rpc/`` when ``--remote`` is present.

    ``argv`` is ``sys.argv[1:]``. Parses the leading global flags via
    :func:`parse_remote_invocation`: when ``--remote`` is absent it returns
    ``None`` (the signal for the caller to run the local Typer app). Otherwise it
    calls :func:`forward` and returns the remote command's exit code.

    Both the parse and the forward run under a single ``try`` so the no-URL
    :class:`RemoteUsageError` is handled here too: on any
    :class:`RemoteUsageError` / :class:`RemoteTransportError` the message is
    printed as ``twicc: <message>`` to stderr and the exception's reserved
    ``exit_code`` is returned.
    """
    try:
        invocation = parse_remote_invocation(argv)
        if invocation is None:
            return None
        url, token, command_argv = invocation
        return forward(url, token, command_argv)
    except RemoteTransportError as exc:
        # Transport / remote-layer failures are labelled "remote error:" to set
        # them apart from a usage error (caught pre-flight, before any HTTP) and
        # from the forwarded command's own error (printed verbatim by forward()).
        print(f"twicc: remote error: {exc}", file=sys.stderr)
        return exc.exit_code
    except RemoteUsageError as exc:
        print(f"twicc: {exc}", file=sys.stderr)
        return exc.exit_code
