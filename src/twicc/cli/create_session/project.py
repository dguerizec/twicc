"""Resolve the ``--project`` argument to a ``Project`` row.

Heuristic:
- ``os.path.isdir(value)`` → path. Resolve realpath, ``get_or_create``.
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
    created: bool


def resolve_project(project_arg: str | None) -> ResolvedProject:
    from twicc.core.models import Project
    from twicc.paths import path_to_project_id

    if project_arg is None or project_arg == "":
        project_arg = os.getcwd()

    if os.path.isdir(project_arg):
        resolved_dir = os.path.realpath(project_arg)
        project_id = path_to_project_id(resolved_dir)
        project, created = Project.objects.get_or_create(
            id=project_id,
            defaults={"directory": resolved_dir},
        )
        if not project.directory:
            project.directory = resolved_dir
            project.save(update_fields=["directory"])
        return ResolvedProject(project_id=project.id,
                               directory=project.directory,
                               created=created)

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
        return ResolvedProject(project_id=project.id,
                               directory=project.directory,
                               created=False)

    raise ProjectError(
        f"--project: {project_arg!r} is neither an existing directory "
        f"nor a known project_id (tried also with leading '-')."
    )
