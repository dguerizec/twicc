"""CLI subpackage implementing ``twicc update-session``.

Drops a ``kind="session:update_*"`` payload (``update_settings``,
``update_title``, ``update_archived``, ``update_pinned``, ``update_hidden``)
in ``<data_dir>/drop-requests/`` so the live TwiCC server applies the
change via the matching service. Built on the shared infrastructure from
:mod:`twicc.cli._drop_request` (drop-file, polling, output, presets,
validation, session lookup, ...).
"""
