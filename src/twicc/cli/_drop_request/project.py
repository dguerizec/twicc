"""Resolve a ``--project`` / project argument to a canonical project_id.

Every CLI command that accepts a project_id also accepts a directory path:
the caller can write ``twicc project /path/to/repo`` or ``twicc project
-path-to-repo`` interchangeably.

Heuristic:
- ``os.path.isdir(value)`` → path. Derive the canonical ``project_id`` via
  ``path_to_project_id(realpath(value))``. The directory is the realpath.
- otherwise → project id. Normalise (auto-prepend leading dash if missing);
  the DB-aware resolver also tries the value as-is, then with a leading
  dash, as a small ergonomic fallback for the common case where the dash
  was stripped by the shell.
- empty/None → fall back to ``os.getcwd()``.

Two entry points:
- :func:`derive_project_id` is pure (no Django, no DB). Use it from Typer
  wrappers to keep ``--help`` cheap.
- :func:`resolve_project` does the DB lookup and pulls the registered
  directory when the input is a known id. Use it when you need the
  directory (e.g. ``create-session`` builds a drop-file payload with it).
"""

from __future__ import annotations

import os
from typing import NamedTuple

from twicc.cli._output import emit_error, in_api_mode
from twicc.paths import path_to_project_id


class ResolvedProject(NamedTuple):
    project_id: str
    # None when the input was an id with no matching Project row and no
    # on-disk directory backing it. Callers that need the directory
    # (e.g. to create the project server-side) must reject in that case.
    directory: str | None
    # True iff a Project row exists in DB for ``project_id`` at resolution
    # time. False means either the path is a fresh directory not yet
    # registered, or the input was an id that doesn't match any row.
    exists: bool


def _normalize_id(value: str) -> str:
    """Prepend a leading dash if missing — every project_id starts with one
    (path_to_project_id always replaces the leading ``/`` of an absolute
    path with ``-``)."""
    return value if value.startswith("-") else f"-{value}"


def _reject_relative_over_api(value: str | None) -> None:
    """In API mode, forbid the empty(->CWD) default and relative-path project inputs.

    A project id never contains a path separator (path_to_project_id turns
    every '/' into '-'), so a value with a separator — or one that resolves
    to an existing directory — is a filesystem path; if it is not absolute it
    would be realpath'd against the backend's CWD, which is meaningless to an
    API caller. Ids (with or without leading dash) pass through untouched.
    """
    if not in_api_mode():
        return
    if value is None or value == "":
        emit_error(
            "a project or directory is required over the API (no caller working "
            "directory); pass an absolute path or a project id",
            code=2,
        )
    sep_hit = os.sep in value or (os.altsep is not None and os.altsep in value)
    if not os.path.isabs(value) and (sep_hit or os.path.isdir(value)):
        emit_error(
            f"relative path {value!r} is not allowed over the API (no caller "
            "working directory); pass an absolute path or a project id "
            "(a leading-dash id like -home-foo-bar)",
            code=2,
        )


def derive_project_id(value: str | None) -> tuple[str, str | None]:
    """Pure: derive ``(project_id, directory_or_none)`` from a path-or-id input.

    No Django, no DB. ``directory`` is the realpath when ``value`` points
    to an existing directory, ``None`` otherwise (id input).
    """
    _reject_relative_over_api(value)
    if value is None or value == "":
        value = os.getcwd()

    if os.path.isdir(value):
        resolved_dir = os.path.realpath(value)
        return path_to_project_id(resolved_dir), resolved_dir

    return _normalize_id(value), None


def resolve_project(value: str | None) -> ResolvedProject:
    """DB-aware resolution to a full :class:`ResolvedProject` (never raises).

    For path inputs: ``project_id`` derived from the realpath, ``directory``
    is the realpath, ``exists`` reflects the DB state.

    For id inputs: tries ``value`` then ``"-" + value`` against the DB. When
    a matching row exists, returns the Project's stored ``directory`` (and
    ``exists=True``); otherwise returns the normalised id with
    ``directory=None`` and ``exists=False`` — the caller decides whether
    that's acceptable.
    """
    _reject_relative_over_api(value)
    from twicc.core.models import Project

    if value is None or value == "":
        value = os.getcwd()

    if os.path.isdir(value):
        resolved_dir = os.path.realpath(value)
        project_id = path_to_project_id(resolved_dir)
        exists = Project.objects.filter(id=project_id).exists()
        return ResolvedProject(
            project_id=project_id,
            directory=resolved_dir,
            exists=exists,
        )

    for candidate in (value, "-" + value) if not value.startswith("-") else (value,):
        try:
            project = Project.objects.get(id=candidate)
        except Project.DoesNotExist:
            continue
        return ResolvedProject(
            project_id=project.id,
            directory=project.directory or None,
            exists=True,
        )

    return ResolvedProject(
        project_id=_normalize_id(value),
        directory=None,
        exists=False,
    )
