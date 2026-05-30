"""CLI subpackage implementing ``twicc send-message``.

Drops a ``kind="session:send_message"`` payload in ``<data_dir>/drop-requests/``
so the live TwiCC server forwards the message to the existing session via
``manager.send_to_session``. Built on the shared infrastructure from
:mod:`twicc.cli._drop_request` (drop-file, polling, output, attachments).
"""
