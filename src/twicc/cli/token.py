"""CLI commands to manage RPC API tokens.

Tokens gate the ``/rpc/`` HTTP API. Like ``twicc password``, this sub-app is
**local/human only** and is never exposed through the API itself. ``create``
prints the secret once (via plain ``typer.echo``, never ``emit_json``, so it
can never be captured through the API path); only the digest is persisted.
"""

from __future__ import annotations

import typer

from twicc.auth import tokens

app = typer.Typer(
    name="token",
    help="Manage RPC API tokens (local only).",
    no_args_is_help=True,
)


@app.command("create")
def create_token(
    name: str = typer.Option(..., "--name", help="Human label for this token."),
) -> None:
    """Mint a new API token and print it once (only its digest is stored)."""
    label = name.strip()
    if not label:
        typer.echo("Error: --name cannot be empty.", err=True)
        raise typer.Exit(1)
    token, rec = tokens.create_token(label)
    typer.echo(token)
    typer.echo(
        f"\nSaved token id {rec.id} (name: {rec.name}). "
        "This secret is shown ONCE — store it now.",
        err=True,
    )


@app.command("list")
def list_tokens() -> None:
    """List token metadata as JSON (never the secret)."""
    from twicc.cli._output import emit_json

    emit_json(
        [
            {
                "id": r.id,
                "name": r.name,
                "created_at": r.created_at,
                "last_used_at": r.last_used_at,
            }
            for r in tokens.load_tokens()
        ]
    )


@app.command("revoke")
def revoke_token(
    token_id: str = typer.Argument(help="The token id (tok_...) to revoke."),
) -> None:
    """Revoke a token by id."""
    if tokens.revoke_token(token_id):
        typer.echo(f"Revoked {token_id}.")
    else:
        typer.echo(f"No token with id {token_id}.", err=True)
        raise typer.Exit(1)
