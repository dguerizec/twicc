"""Shared building blocks for CLI commands that talk to the live TwiCC server
via the ``sessions-pending/`` drop-file protocol (``twicc create-session``,
``twicc send-message``, future ``twicc update-session`` ...).

The modules in this sub-package are intentionally agnostic of the action
performed on the server side: they handle discovery (heartbeat check),
atomic drop-file write, status polling, output formatting, prompt resolution,
attachment validation+encoding, and the local bootstrap snapshot. Each
command module composes them with its own action-specific glue.

Leading underscore in the package name signals "internal to twicc.cli".
"""
