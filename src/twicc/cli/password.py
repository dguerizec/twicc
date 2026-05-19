"""CLI commands to manage TwiCC password protection.

Reads and writes the ``TWICC_PASSWORD_HASH`` key in the data dir's ``.env``
file. All sub-commands require an interactive terminal — they are not
designed for scripting (the password should never be passed via flag or
stdin to avoid shell history leaks).
"""

import os
import stat
import sys
from pathlib import Path

import typer
from dotenv import dotenv_values, set_key, unset_key

from twicc.auth.hashers import hash_password, is_legacy_hash
from twicc.paths import get_env_path

KEY = "TWICC_PASSWORD_HASH"

app = typer.Typer(
    name="password",
    help="Manage password protection (interactive only).",
    no_args_is_help=True,
)


def _require_interactive() -> None:
    if not sys.stdin.isatty():
        typer.echo("Error: this command requires an interactive terminal.", err=True)
        raise typer.Exit(code=1)


def _read_current_hash() -> str:
    env_path = get_env_path()
    if not env_path.exists():
        return ""
    return (dotenv_values(env_path).get(KEY) or "").strip()


@app.command("set")
def set_password() -> None:
    """Set or replace the password (interactive prompt with confirmation)."""
    _require_interactive()

    if _read_current_hash():
        if not typer.confirm("A password is already configured. Replace it?", default=False):
            typer.echo("Aborted.")
            raise typer.Exit(code=0)

    password = typer.prompt(
        "New password",
        hide_input=True,
        confirmation_prompt="Confirm password",
    )

    if not password.strip():
        typer.echo("Error: password cannot be empty.", err=True)
        raise typer.Exit(code=1)

    env_path = get_env_path()
    env_path.parent.mkdir(parents=True, exist_ok=True)
    set_key(str(env_path), KEY, hash_password(password), quote_mode="never")
    _restrict_env_permissions(env_path)

    typer.echo(f"Password protection enabled (written to {env_path}).")
    typer.echo("Restart TwiCC for the change to take effect.")


@app.command("clear")
def clear_password() -> None:
    """Disable password protection (removes TWICC_PASSWORD_HASH from .env)."""
    _require_interactive()

    if not _read_current_hash():
        typer.echo("No password is configured.")
        raise typer.Exit(code=0)

    if not typer.confirm("Disable password protection?", default=False):
        typer.echo("Aborted.")
        raise typer.Exit(code=0)

    env_path = get_env_path()
    unset_key(str(env_path), KEY)

    typer.echo(f"Password protection disabled ({env_path}).")
    typer.echo("Restart TwiCC for the change to take effect.")


@app.command("status")
def status_password() -> None:
    """Show whether password protection is currently configured."""
    _require_interactive()

    env_path = get_env_path()
    current = _read_current_hash()

    if not current:
        typer.echo(f"Password protection: disabled ({env_path})")
    else:
        typer.echo(f"Password protection: enabled (configured in {env_path})")
        if is_legacy_hash(current):
            typer.echo("")
            typer.echo(
                "Note: your password is stored in an older format. Re-run "
                "`twicc password set` to upgrade it to the current, more "
                "secure storage — you'll just be re-prompted for your password."
            )

    _warn_if_env_world_readable(env_path)


def _restrict_env_permissions(env_path: Path) -> None:
    """Force chmod 600 on the .env so the stored hash is owner-only.

    Skipped on native Windows (POSIX bits don't apply). No-op if the file
    is already at 600 or doesn't exist.
    """
    if os.name == "nt":
        return
    if env_path.exists():
        env_path.chmod(0o600)


def _warn_if_env_world_readable(env_path: Path) -> None:
    """Warn if the .env file is accessible to group or other users.

    The .env may hold the password hash (and other infra config) — a
    non-zero group/other permission bit means another local account can
    read it. Silently skips on native Windows (POSIX bits don't apply).
    """
    if os.name == "nt":
        return
    if not env_path.exists():
        return
    mode = stat.S_IMODE(env_path.stat().st_mode)
    if mode & (stat.S_IRWXG | stat.S_IRWXO):
        typer.echo("")
        typer.echo(
            f"Warning: {env_path} has permissions {mode:03o} — accessible "
            "to other users on this machine. Run "
            f"`chmod 600 {env_path}` to restrict it to your account only."
        )
