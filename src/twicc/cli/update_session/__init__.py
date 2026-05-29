"""CLI subpackage implementing ``twicc update-session``.

Drops a ``kind="update_settings"`` (and, in the future, other ``kind`` values
for title / archive / pin / stop) payload in ``<data_dir>/sessions-pending/``
so the live TwiCC server applies the change via the matching service. Built
on the shared infrastructure from :mod:`twicc.cli._session_request`
(drop-file, polling, output, presets, validation, session lookup, ...).
"""
