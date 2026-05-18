"""Resolve the ``--project`` argument to a ``(project_id, directory)`` pair.

Heuristic:
- ``os.path.isdir(value)`` → path. Resolve realpath and derive the canonical
  ``project_id`` via ``path_to_project_id``. No DB write — actual Project
  creation happens server-side in :func:`create_session_from_payload`,
  which runs inside the TwiCC server's main process and can therefore
  broadcast ``project_added`` / ``workspaces_updated`` over WS while UI
  clients are connected.
- otherwise → project id. Try ``value`` first, then ``"-" + value``
  (sucre syntaxique for the common case where the id starts with a
  dash from a leading ``/`` in the original path).
- ``--project`` absent → default to ``os.getcwd()``.
"""

from __future__ import annotations

import os
from typing import NamedTuple


class ProjectError(Exception):
    pass


class ResolvedProject(NamedTuple):
    project_id: str
    directory: str
    # True when the project was already in DB at resolution time. False means
    # the server will create it from the drop-file payload.
    existed: bool


def resolve_project(project_arg: str | None) -> ResolvedProject:
    from twicc.core.models import Project
    from twicc.paths import path_to_project_id

    if project_arg is None or project_arg == "":
        project_arg = os.getcwd()

    if os.path.isdir(project_arg):
        resolved_dir = os.path.realpath(project_arg)
        project_id = path_to_project_id(resolved_dir)
        existed = Project.objects.filter(id=project_id).exists()
        return ResolvedProject(
            project_id=project_id,
            directory=resolved_dir,
            existed=existed,
        )

    # Treat as canonical id. Try the value as-is, then with a leading "-".
    for candidate in (project_arg, "-" + project_arg):
        try:
            project = Project.objects.get(id=candidate)
        except Project.DoesNotExist:
            continue
        if not project.directory:
            raise ProjectError(
                f"--project: project {project.id!r} exists but has no directory set"
            )
        return ResolvedProject(
            project_id=project.id,
            directory=project.directory,
            existed=True,
        )

    raise ProjectError(
        f"--project: {project_arg!r} is neither an existing directory "
        f"nor a known project_id (tried also with leading '-')."
    )
