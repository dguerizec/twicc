"""Apply create / update / delete to the workspaces.json file.

Called by :class:`DropRequestsWatcher` when the CLI drops a request file
whose ``kind`` matches one of the supported workspace actions:

- ``kind="workspace:create"`` → :func:`create_workspace_from_payload`.
- ``kind="workspace:update"`` → :func:`update_workspace_from_payload`.
- ``kind="workspace:delete"`` → :func:`delete_workspace_from_payload`.

Each function validates the payload (shape + types + cross-checks against
DB-tracked projects), delegates the actual write to the matching atomic
op in :mod:`twicc.workspaces` (which runs under ``_workspaces_lock`` and
broadcasts ``workspaces_updated`` after the write), and returns a
:class:`WorkspaceMutationResult` the watcher serialises into the status
file.

The functions do NOT raise for business-rule errors (workspace not found,
project not found, invalid name, ...); they return a failed result with
a list of structured errors. Unexpected exceptions propagate normally
and are translated by the watcher to ``status: failed``.

The drop-file is a trust boundary: the CLI does its own pre-flight
validation for nicer error messages, but every check is re-run here in
case a forged or version-skewed caller submits a malformed payload.
"""

from __future__ import annotations

import logging

from asgiref.sync import sync_to_async

from twicc.workspaces import (
    WorkspaceMutationError,
    WorkspaceMutationResult,
    clean_browser_url_ops,
    create_workspace_atomic,
    delete_workspace_atomic,
    normalize_browser_url,
    update_workspace_atomic,
    validate_browser_url,
)


logger = logging.getLogger(__name__)


def _invalid_payload_result(field: str, message: str) -> WorkspaceMutationResult:
    return WorkspaceMutationResult(False, None, None, [
        WorkspaceMutationError(field, "invalid_payload", message),
    ])


def _coerce_str_list(value, *, field: str) -> tuple[list[str] | None, WorkspaceMutationResult | None]:
    """Return (list, None) or (None, error_result) for a list-of-string payload field.

    ``None`` / missing → empty list, OK. Non-list or non-string element → error.
    """
    if value is None:
        return [], None
    if not isinstance(value, list):
        return None, _invalid_payload_result(field, f"{field} must be a list (got {type(value).__name__}).")
    for item in value:
        if not isinstance(item, str):
            return None, _invalid_payload_result(field, f"{field} entries must be strings (got {type(item).__name__}).")
    return [s for s in value], None


async def _check_projects_exist(project_ids: list[str], *, field: str) -> list[WorkspaceMutationError]:
    """Return one ``project_not_found`` error per missing project id.

    Used both at create and at update time; on update we only check the
    incoming ``add_projects`` (we accept that an already-listed but now
    deleted project id can stay in the list — the UI handles that
    gracefully and we don't want to surprise the user by silently dropping
    entries during an unrelated update).
    """
    if not project_ids:
        return []
    from twicc.core.models import Project

    existing = set(await sync_to_async(
        lambda: list(Project.objects.filter(id__in=project_ids).values_list("id", flat=True))
    )())
    return [
        WorkspaceMutationError(field, "project_not_found",
                               f"Project {pid!r} not found.")
        for pid in project_ids
        if pid not in existing
    ]


async def create_workspace_from_payload(payload: dict) -> WorkspaceMutationResult:
    """Drop-file glue for ``kind="workspace:create"``.

    Expected payload shape::

        {
            "kind": "workspace:create",
            "name": str,
            "color": str | None,                  # optional
            "project_ids": [str, ...],            # optional
            "auto_project_patterns": [str, ...],  # optional
            "archived": bool,                     # optional, default False
            "browser_url": str | None,            # optional
        }
    """
    name = payload.get("name")
    if not isinstance(name, str):
        return _invalid_payload_result("name", "name must be a non-empty string.")

    color = payload.get("color")
    if color is not None and not isinstance(color, str):
        return _invalid_payload_result("color", "color must be a string or null.")

    project_ids, err = _coerce_str_list(payload.get("project_ids"), field="project_ids")
    if err is not None:
        return err

    patterns, err = _coerce_str_list(payload.get("auto_project_patterns"),
                                     field="auto_project_patterns")
    if err is not None:
        return err

    archived = payload.get("archived", False)
    if not isinstance(archived, bool):
        return _invalid_payload_result("archived", "archived must be a boolean.")

    # Single URL at creation time (it becomes the default entry); more URLs
    # are added through ``update-workspace``.
    browser_url = payload.get("browser_url")
    if browser_url is not None and not isinstance(browser_url, str):
        return _invalid_payload_result("browser_url", "browser_url must be a string or null.")
    browser_url = normalize_browser_url(browser_url)
    url_errors = validate_browser_url(browser_url, field="--browser-url")
    if url_errors:
        return WorkspaceMutationResult(False, None, None, url_errors)
    browser_urls = [{"url": browser_url, "default": True}] if browser_url else []

    project_errors = await _check_projects_exist(project_ids, field="--add-project")
    if project_errors:
        return WorkspaceMutationResult(False, None, None, project_errors)

    return await create_workspace_atomic(
        name=name,
        color=color,
        project_ids=project_ids,
        auto_project_patterns=patterns,
        archived=archived,
        browser_urls=browser_urls,
    )


async def update_workspace_from_payload(payload: dict) -> WorkspaceMutationResult:
    """Drop-file glue for ``kind="workspace:update"``.

    Expected payload shape (every patch field optional)::

        {
            "kind": "workspace:update",
            "workspace_id": str,
            "name": str,                           # optional rename
            "color": str | null,                   # optional (use unset_color to clear)
            "unset_color": bool,                   # optional
            "add_projects": [str, ...],
            "remove_projects": [str, ...],
            "add_patterns": [str, ...],
            "remove_patterns": [str, ...],
            "archived": bool,                      # optional
            # Saved Browser-pane URL ops (combinable; applied clear → remove →
            # add → set-default) — same shape as the project update payload:
            "add_browser_url": {"url": str, "label": str | null,
                                "set_default": bool} | null,
            "remove_browser_url": str | null,      # optional, idempotent
            "set_default_browser_url": str | null, # optional, must be listed
            "clear_browser_urls": bool,            # optional, drop every entry
        }

    The CLI ensures at least one patch field is present (``no_op`` is
    enforced locally for a nicer error).
    """
    workspace_id = payload.get("workspace_id")
    if not isinstance(workspace_id, str) or not workspace_id:
        return _invalid_payload_result("workspace_id", "workspace_id must be a non-empty string.")

    new_name = payload.get("name")
    if new_name is not None and not isinstance(new_name, str):
        return _invalid_payload_result("name", "name must be a string.")

    color = payload.get("color")
    if color is not None and not isinstance(color, str):
        return _invalid_payload_result("color", "color must be a string or null.")
    unset_color = payload.get("unset_color", False)
    if not isinstance(unset_color, bool):
        return _invalid_payload_result("unset_color", "unset_color must be a boolean.")
    if unset_color and color is not None:
        return WorkspaceMutationResult(False, workspace_id, None, [
            WorkspaceMutationError("--color", "conflicting_flags",
                                   "--color and --unset-color cannot be used together."),
        ])

    add_projects, err = _coerce_str_list(payload.get("add_projects"), field="add_projects")
    if err is not None:
        return err
    remove_projects, err = _coerce_str_list(payload.get("remove_projects"), field="remove_projects")
    if err is not None:
        return err
    add_patterns, err = _coerce_str_list(payload.get("add_patterns"), field="add_patterns")
    if err is not None:
        return err
    remove_patterns, err = _coerce_str_list(payload.get("remove_patterns"), field="remove_patterns")
    if err is not None:
        return err

    archived = payload.get("archived")
    if archived is not None and not isinstance(archived, bool):
        return _invalid_payload_result("archived", "archived must be a boolean.")

    browser_ops, op_errors = clean_browser_url_ops(payload)
    if op_errors:
        return WorkspaceMutationResult(False, workspace_id, None, op_errors)

    project_errors = await _check_projects_exist(add_projects, field="--add-project")
    if project_errors:
        return WorkspaceMutationResult(False, workspace_id, None, project_errors)

    return await update_workspace_atomic(
        workspace_id,
        new_name=new_name,
        color=color,
        unset_color=unset_color,
        add_projects=add_projects,
        remove_projects=remove_projects,
        add_patterns=add_patterns,
        remove_patterns=remove_patterns,
        archived=archived,
        **browser_ops,
    )


async def delete_workspace_from_payload(payload: dict) -> WorkspaceMutationResult:
    """Drop-file glue for ``kind="workspace:delete"``.

    Expected payload shape::

        {
            "kind": "workspace:delete",
            "workspace_id": str,
        }
    """
    workspace_id = payload.get("workspace_id")
    if not isinstance(workspace_id, str) or not workspace_id:
        return _invalid_payload_result("workspace_id", "workspace_id must be a non-empty string.")

    return await delete_workspace_atomic(workspace_id)
