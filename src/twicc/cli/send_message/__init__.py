"""CLI subpackage implementing ``twicc send-message``.

Drops a ``kind="send"`` payload in ``<data_dir>/sessions-pending/`` so the
live TwiCC server forwards the message to the existing session via
``manager.send_to_session``. Built on the shared infrastructure from
:mod:`twicc.cli._session_request` (drop-file, polling, output, attachments).
"""
