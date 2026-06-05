"""CLI subpackage implementing ``twicc update-sessions`` (batch updates).

Batch sibling of ``twicc update-session``: applies the SAME update to a set of
sessions in one call. Each sub-command resolves its target ids (explicit
``SESSION_ID...`` merged with the optional ``--spawned-by`` / ``--descendants``
/ ``--annotation`` scope — union, explicit ids first), then fans out one
drop-request per id reusing the EXACT same ``kind`` + payload the singular
command would have produced (no new server-side handler). See
:mod:`twicc.cli.update_sessions._runner` for the shared runner and
:mod:`twicc.cli.update_sessions.command` for the sub-app.
"""
