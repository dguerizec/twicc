"""Apply create / update to the ``Project`` model.

Called by :class:`DropRequestsWatcher` when the CLI drops a request file
whose ``kind`` matches one of the supported project actions:

- ``kind="project:create"`` → :func:`create_project_from_payload`.
- ``kind="project:update"`` → :func:`update_project_from_payload`.
- ``kind="project:trust"`` → :func:`decide_project_trust_from_payload`.

There is no ``project:delete`` by design: a Project row is bound to its
sessions (cost aggregates, workspace memberships, etc.); removing it
from the CLI would orphan that state in confusing ways. Projects are
archived, never deleted.

Both functions validate the payload (shape + types + cross-checks),
delegate the actual write to the matching helper in
:mod:`twicc.projects` (which runs under the DB write lock and emits the
matching ``project_added`` / ``project_updated`` broadcast), and return
a :class:`ProjectMutationResult` the watcher serialises into the status
file.

The drop-file is a trust boundary: the CLI does its own pre-flight
validation for nicer error messages, but every check is re-run here in
case a forged or version-skewed caller submits a malformed payload.
"""

from __future__ import annotations

import asyncio
import logging
import os

from django.db import IntegrityError

from twicc.core.models import Project
from twicc.paths import path_to_project_id
from twicc.projects import (
    ProjectMutationError,
    ProjectMutationResult,
    register_project,
    update_project_atomic,
    validate_project_name_format,
)
from twicc.workspaces import validate_color


logger = logging.getLogger(__name__)


def _invalid_payload_result(field: str, message: str) -> ProjectMutationResult:
    return ProjectMutationResult(False, None, None, [
        ProjectMutationError(field, "invalid_payload", message),
    ])


async def create_project_from_payload(payload: dict) -> ProjectMutationResult:
    """Drop-file glue for ``kind="project:create"``.

    Expected payload shape::

        {
            "kind": "project:create",
            "directory": "/absolute/path",
            "name": str | None,                # optional
            "color": str | None,               # optional (hex)
            "create_directory": bool,          # optional, default False
        }

    There is no ``archived`` field by design: creating an already-archived
    project (with zero sessions, by definition) makes no practical sense;
    archive instead through ``update-project`` once the project has lived.

    The server normalises the directory via :func:`os.path.realpath`,
    derives the project id, ensures the directory exists on disk
    (creating it when ``create_directory`` is True), checks the id is
    not already taken, runs format validation on ``name`` / ``color``,
    enforces ``name`` global uniqueness, then calls
    :func:`twicc.projects.register_project` under the DB write lock —
    the same entry point the HTTP endpoint uses, so workspace auto-add
    and the ``project_added`` broadcast fire as usual.
    """
    from twicc.providers.db_writer import run_under_db_write_lock

    directory = payload.get("directory")
    if not isinstance(directory, str) or not directory:
        return _invalid_payload_result("directory",
                                       "directory must be a non-empty string.")

    name = payload.get("name")
    if name is not None and not isinstance(name, str):
        return _invalid_payload_result("name", "name must be a string or null.")

    color = payload.get("color")
    if color is not None and not isinstance(color, str):
        return _invalid_payload_result("color", "color must be a string or null.")

    create_directory = payload.get("create_directory", False)
    if not isinstance(create_directory, bool):
        return _invalid_payload_result("create_directory",
                                       "create_directory must be a boolean.")

    # Resolve to a realpath and check absoluteness — the project id is
    # derived from this, so any non-canonical input would silently mint
    # a duplicate id.
    resolved = await asyncio.to_thread(os.path.realpath, directory)
    if not os.path.isabs(resolved):
        return ProjectMutationResult(False, None, None, [
            ProjectMutationError("DIRECTORY", "invalid_directory",
                                  f"Directory must be an absolute path (got {directory!r})."),
        ])

    exists = await asyncio.to_thread(os.path.exists, resolved)
    if exists:
        is_dir = await asyncio.to_thread(os.path.isdir, resolved)
        if not is_dir:
            return ProjectMutationResult(False, None, None, [
                ProjectMutationError("DIRECTORY", "invalid_directory",
                                      f"Path {resolved!r} exists but is not a directory."),
            ])
    elif create_directory:
        try:
            await asyncio.to_thread(os.makedirs, resolved, exist_ok=True)
        except OSError as e:
            return ProjectMutationResult(False, None, None, [
                ProjectMutationError("DIRECTORY", "directory_creation_failed",
                                      f"Failed to create directory {resolved!r}: {e}"),
            ])
    else:
        return ProjectMutationResult(False, None, None, [
            ProjectMutationError("DIRECTORY", "directory_not_found",
                                  f"Directory {resolved!r} does not exist. "
                                  "Pass --create-directory to create it."),
        ])

    project_id = path_to_project_id(resolved)

    # ID collision (project already exists for this directory).
    if await Project.objects.filter(id=project_id).aexists():
        return ProjectMutationResult(False, project_id, None, [
            ProjectMutationError("DIRECTORY", "project_already_exists",
                                  f"A project already exists for directory {resolved!r} "
                                  f"(id: {project_id!r})."),
        ])

    # Format checks on name + color.
    errors: list[ProjectMutationError] = []
    errors.extend(validate_project_name_format(name))
    errors.extend(ProjectMutationError(e.field, e.code, e.message)
                  for e in validate_color(color, field="color"))
    if errors:
        return ProjectMutationResult(False, project_id, None, errors)

    # Global name uniqueness.
    normalized_name: str | None = None
    if name is not None:
        trimmed = name.strip()
        normalized_name = trimmed if trimmed else None
    if normalized_name is not None:
        if await Project.objects.filter(name=normalized_name).aexists():
            return ProjectMutationResult(False, project_id, None, [
                ProjectMutationError("name", "duplicate_name",
                                      f"Another project already uses the name {normalized_name!r}."),
            ])

    # Single entry point — fires project_added broadcast + workspace auto-add.
    try:
        project, created = await run_under_db_write_lock(
            lambda: register_project(
                project_id,
                directory=resolved,
                name=normalized_name,
                color=color or None,
            )
        )
    except IntegrityError as e:
        logger.warning("create_project IntegrityError for %s: %s", project_id, e)
        return ProjectMutationResult(False, project_id, None, [
            ProjectMutationError("name", "duplicate_name",
                                  "Another project already uses this name "
                                  "(race-detected at write time)."),
        ])
    if not created:
        # Lost a race with another creator between the exists-check and now.
        return ProjectMutationResult(False, project_id, project, [
            ProjectMutationError("DIRECTORY", "project_already_exists",
                                  f"A project already exists for directory {resolved!r} "
                                  f"(race-detected at write time)."),
        ])

    return ProjectMutationResult(True, project_id, project, None)


async def update_project_from_payload(payload: dict) -> ProjectMutationResult:
    """Drop-file glue for ``kind="project:update"``.

    Expected payload shape (every patch field optional)::

        {
            "kind": "project:update",
            "project_id": str,
            "name": str | None,           # optional rename
            "unset_name": bool,           # optional, clears the custom name
            "color": str | None,          # optional recolor
            "unset_color": bool,          # optional, clears the color
            "archived": bool,             # optional
        }

    The CLI ensures at least one patch field is present (``no_op``
    enforced locally) and that mutually-exclusive flags (``name`` vs
    ``unset_name``, ``color`` vs ``unset_color``) are not combined.
    """
    project_id = payload.get("project_id")
    if not isinstance(project_id, str) or not project_id:
        return _invalid_payload_result("project_id",
                                       "project_id must be a non-empty string.")

    new_name = payload.get("name")
    if new_name is not None and not isinstance(new_name, str):
        return _invalid_payload_result("name", "name must be a string or null.")
    unset_name = payload.get("unset_name", False)
    if not isinstance(unset_name, bool):
        return _invalid_payload_result("unset_name", "unset_name must be a boolean.")
    if unset_name and new_name is not None:
        return ProjectMutationResult(False, project_id, None, [
            ProjectMutationError("--name", "conflicting_flags",
                                  "--name and --unset-name cannot be used together."),
        ])

    color = payload.get("color")
    if color is not None and not isinstance(color, str):
        return _invalid_payload_result("color", "color must be a string or null.")
    unset_color = payload.get("unset_color", False)
    if not isinstance(unset_color, bool):
        return _invalid_payload_result("unset_color", "unset_color must be a boolean.")
    if unset_color and color is not None:
        return ProjectMutationResult(False, project_id, None, [
            ProjectMutationError("--color", "conflicting_flags",
                                  "--color and --unset-color cannot be used together."),
        ])

    archived = payload.get("archived")
    if archived is not None and not isinstance(archived, bool):
        return _invalid_payload_result("archived", "archived must be a boolean.")

    # Format checks.
    errors: list[ProjectMutationError] = []
    if not unset_name and new_name is not None:
        errors.extend(validate_project_name_format(new_name, field="--name"))
    if not unset_color and color is not None:
        errors.extend(ProjectMutationError(e.field, e.code, e.message)
                      for e in validate_color(color, field="--color"))
    if errors:
        return ProjectMutationResult(False, project_id, None, errors)

    # Global name uniqueness (excluding self).
    normalized_name: str | None = None
    if not unset_name and new_name is not None:
        trimmed = new_name.strip()
        normalized_name = trimmed if trimmed else None
        if normalized_name is not None:
            collision = await (Project.objects
                               .filter(name=normalized_name)
                               .exclude(id=project_id)
                               .aexists())
            if collision:
                return ProjectMutationResult(False, project_id, None, [
                    ProjectMutationError("--name", "duplicate_name",
                                          f"Another project already uses the name "
                                          f"{normalized_name!r}."),
                ])

    return await update_project_atomic(
        project_id,
        new_name=new_name,
        unset_name=unset_name,
        color=color,
        unset_color=unset_color,
        archived=archived,
    )


async def decide_project_trust_from_payload(payload: dict) -> ProjectMutationResult:
    """Drop-file glue for ``kind="project:trust"``.

    Expected payload shape::

        {
            "kind": "project:trust",
            "project_id": str,
            "trusted": bool | None,       # True / False = explicit, null = reset to inherit
            "propagation": bool | None,   # optional; defaults to "is under git" for an
                                          # explicit decision, ignored on reset
        }

    Project **trust is a human-only decision**: this path backs the
    ``twicc update-project --trust/--untrust/--reset-trust`` flags and the
    web dialog, never an agent-facing skill. Delegates to
    :func:`twicc.core.services.trust.decide_project_trust` (provider-config
    projection + DB write under lock + ``project_updated`` broadcast).
    """
    from twicc.core.services.trust import decide_project_trust

    project_id = payload.get("project_id")
    if not isinstance(project_id, str) or not project_id:
        return _invalid_payload_result("project_id", "project_id must be a non-empty string.")

    if "trusted" not in payload:
        return _invalid_payload_result("trusted", "trusted is required (true, false, or null).")
    trusted = payload.get("trusted")
    if trusted is not None and not isinstance(trusted, bool):
        return _invalid_payload_result("trusted", "trusted must be true, false, or null.")

    propagation = payload.get("propagation")
    if propagation is not None and not isinstance(propagation, bool):
        return _invalid_payload_result("propagation", "propagation must be a boolean or null.")

    project = await Project.objects.filter(id=project_id).afirst()
    if project is None:
        return ProjectMutationResult(False, project_id, None, [
            ProjectMutationError("PROJECT", "project_not_found",
                                  f"Project {project_id!r} not found."),
        ])

    result = await decide_project_trust(project_id, trusted, propagation)
    if not result.get("ok"):
        code = result.get("error", "trust_decision_failed")
        return ProjectMutationResult(False, project_id, None, [
            ProjectMutationError("trust", code, f"Trust decision failed: {code}."),
        ])

    project = await Project.objects.filter(id=project_id).afirst()
    return ProjectMutationResult(True, project_id, project, None)
