"""CLI subpackage implementing ``twicc update-project``.

A Typer group with a twist for backward compatibility: the flat field patch
(``--name`` / ``--color`` / ``--archive`` / trust flags / agent-defaults
provider) lives on the group callback itself (``invoke_without_command``),
while ``settings`` is a real sub-command — so both
``twicc update-project <PROJECT> --name X`` and
``twicc update-project <PROJECT> settings --provider P --model M`` work.
Built on the shared infrastructure from :mod:`twicc.cli._drop_request`.
"""
