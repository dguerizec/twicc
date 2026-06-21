"""``twicc settings`` command group — read and write synced settings.

The bare ``twicc settings`` prints the full synced settings JSON (offline read,
``_version`` stripped). ``twicc settings get <KEY>`` prints the value of a
single key as ``{key: value}``.

``twicc settings set <KEY> <VALUE>`` and ``twicc settings unset <KEY>`` write
a scalar setting via the ``settings:update`` drop-request kind.  The key
classification from :mod:`twicc.cli.settings._keys` gates which keys are
accepted; provider and notification keys are routed to their dedicated
sub-commands.

All command bodies call ``django.setup()`` lazily (after ``--help``) because
``read_synced_settings`` / ``SYNCED_SETTINGS_DEFAULTS`` need the app registry.
Tests run under pytest-django which calls ``django.setup()`` before collecting.
"""

from __future__ import annotations

import typer

from twicc.cli.settings.provider import provider_app

settings_app = typer.Typer(
    name="settings",
    help="Read (and in later tasks, write) synced settings.",
    invoke_without_command=True,
)

settings_app.add_typer(provider_app)


def build_settings_dump() -> dict:
    """Return the full synced settings dict with ``_version`` stripped.

    Pure, unit-testable core for the read commands. Requires Django to be
    set up before calling (``SYNCED_SETTINGS_DEFAULTS`` needs the app
    registry). Command bodies call ``django.setup()`` first; pytest-django
    handles it in the test suite.
    """
    from twicc.synced_settings import prepare_settings_for_client, read_synced_settings

    settings = read_synced_settings()
    clean, _version = prepare_settings_for_client(settings)
    return clean


@settings_app.callback(invoke_without_command=True)
def _settings_default(ctx: typer.Context) -> None:
    """Print all synced settings as JSON (default action)."""
    if ctx.invoked_subcommand is not None:
        return

    # Lazy Django setup — keeps --help fast (no app registry until needed).
    import django

    django.setup()

    from twicc.cli._output import emit_json

    emit_json(build_settings_dump())


@settings_app.command("get")
def settings_get(
    key: str = typer.Argument(help="Settings key to retrieve (e.g. autoUnpinOnArchive)."),
) -> None:
    """Print the value of a single synced settings key as JSON."""
    # Lazy Django setup — keeps --help fast.
    import django

    django.setup()

    from twicc.cli._output import emit_error, emit_json

    dump = build_settings_dump()
    if key not in dump:
        emit_error(f"Error: unknown settings key {key!r}.", code=1)
    emit_json({key: dump[key]})


def _reject_key(key: str) -> None:
    """Emit a validation error and exit 1 if the key is not settable via CLI.

    Checks the four rejection categories:

    - ``excluded``      — UI-only visual preference; not settable via CLI.
    - ``provider``      — use ``twicc settings provider …``.
    - ``notifications`` — use ``twicc settings notifications …``.
    - ``unknown``       — no such setting.

    Returns silently when the key classifies as ``generic`` (accepted).
    """
    from twicc.cli._drop_request.output import emit_validation_errors
    from twicc.cli._drop_request.validation import ValidationError
    from twicc.cli.settings._keys import classify_key

    category = classify_key(key)
    if category == "excluded":
        emit_validation_errors([ValidationError(
            "KEY", "excluded",
            f"{key!r} is a UI-only visual preference; not settable via CLI.",
        )])
        raise typer.Exit(1)
    if category == "provider":
        emit_validation_errors([ValidationError(
            "KEY", "provider_key",
            f"{key!r} is a provider setting; use `twicc settings provider …`.",
        )])
        raise typer.Exit(1)
    if category == "notifications":
        emit_validation_errors([ValidationError(
            "KEY", "notifications_key",
            f"{key!r} is a notification setting; use `twicc settings notifications …`.",
        )])
        raise typer.Exit(1)
    if category == "unknown":
        emit_validation_errors([ValidationError(
            "KEY", "unknown_key",
            f"No such setting {key!r}.",
        )])
        raise typer.Exit(1)
    # category == "generic" → accepted; fall through silently.


@settings_app.command("set")
def settings_set(
    key: str = typer.Argument(help="Settings key to set (e.g. autoUnpinOnArchive)."),
    value: str = typer.Argument(help="New value (type-coerced to match the key's default type)."),
    timeout: int = typer.Option(
        30,
        "--timeout",
        help=(
            "Seconds to wait for the server's final status before giving up. "
            "The request stays on disk; the update may still apply."
        ),
    ),
) -> None:
    """Set a generic synced setting to VALUE."""
    import os
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "twicc.settings")
    import django
    django.setup()

    from twicc.cli._drop_request.discovery import ServerDownError, check_heartbeat
    from twicc.cli._drop_request.drop_file import write_drop_file
    from twicc.cli._drop_request.output import emit_validation_errors
    from twicc.cli._drop_request.polling import poll_status
    from twicc.cli._drop_request.validation import ValidationError
    from twicc.cli._output import emit_error
    from twicc.cli.settings._keys import ValueParseError, parse_value
    from twicc.cli.settings._output import emit_settings_final

    _reject_key(key)

    try:
        parsed = parse_value(key, value)
    except ValueParseError as exc:
        emit_validation_errors([ValidationError("VALUE", "invalid_value", str(exc))])
        raise typer.Exit(1)

    try:
        check_heartbeat()
    except ServerDownError as e:
        emit_error(str(e), code=2)

    payload = {"patch": {key: parsed}}
    drop = write_drop_file(payload, kind="settings:update")

    status_path = drop.path.with_name(f"{drop.request_uuid}.status.json")
    outcome = poll_status(status_path, timeout_seconds=timeout)

    drop.path.unlink(missing_ok=True)
    status_path.unlink(missing_ok=True)

    code = emit_settings_final(outcome, request_uuid=drop.request_uuid, timeout=timeout)
    raise typer.Exit(code)


@settings_app.command("unset")
def settings_unset(
    key: str = typer.Argument(help="Settings key to reset to its default value."),
    timeout: int = typer.Option(
        30,
        "--timeout",
        help=(
            "Seconds to wait for the server's final status before giving up. "
            "The request stays on disk; the update may still apply."
        ),
    ),
) -> None:
    """Reset a generic synced setting to its default value."""
    import os
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "twicc.settings")
    import django
    django.setup()

    from twicc.cli._drop_request.discovery import ServerDownError, check_heartbeat
    from twicc.cli._drop_request.drop_file import write_drop_file
    from twicc.cli._drop_request.polling import poll_status
    from twicc.cli._output import emit_error
    from twicc.cli.settings._output import emit_settings_final
    from twicc.synced_settings import SYNCED_SETTINGS_DEFAULTS

    _reject_key(key)

    default = SYNCED_SETTINGS_DEFAULTS[key]

    try:
        check_heartbeat()
    except ServerDownError as e:
        emit_error(str(e), code=2)

    payload = {"patch": {key: default}}
    drop = write_drop_file(payload, kind="settings:update")

    status_path = drop.path.with_name(f"{drop.request_uuid}.status.json")
    outcome = poll_status(status_path, timeout_seconds=timeout)

    drop.path.unlink(missing_ok=True)
    status_path.unlink(missing_ok=True)

    code = emit_settings_final(outcome, request_uuid=drop.request_uuid, timeout=timeout)
    raise typer.Exit(code)
