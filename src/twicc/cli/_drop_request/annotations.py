"""Parse free-form session annotations for create/update commands."""

from __future__ import annotations

import math
from pathlib import Path
import re
from typing import Any

import orjson

from twicc.cli._drop_request.validation import ValidationError

_INT_RE = re.compile(r"^[+-]?(0|[1-9][0-9]*)$")
_FLOAT_RE = re.compile(
    r"^[+-]?((([0-9]+\.[0-9]*)|(\.[0-9]+))([eE][+-]?[0-9]+)?|[0-9]+[eE][+-]?[0-9]+)$"
)


def parse_annotations(
    entries: list[str],
    annotations_file: str | None,
) -> tuple[dict, list[ValidationError]]:
    """Return annotations from a JSON file plus repeated key=value entries."""
    annotations: dict[str, Any] = {}
    errors: list[ValidationError] = []

    if annotations_file:
        file_annotations, error = _load_annotations_file(annotations_file)
        if error is None:
            annotations.update(file_annotations)
        else:
            errors.append(error)

    for entry in entries:
        parsed = _parse_annotation_entry(entry, field="--annotation")
        if isinstance(parsed, ValidationError):
            errors.append(parsed)
            continue

        path, value = parsed
        error = _set_annotation_path(annotations, path, value)
        if error is not None:
            errors.append(error)

    return annotations, errors


def parse_annotation_update_operations(
    operations: list[str],
) -> tuple[list[dict], list[ValidationError]]:
    """Parse ordered annotation operations for update-session annotations."""
    parsed_operations: list[dict] = []
    errors: list[ValidationError] = []

    for operation in operations:
        if operation == "clear":
            parsed_operations.append({"op": "clear"})
            continue
        if operation.startswith("clear:"):
            errors.append(ValidationError(
                "OPERATION", "invalid_annotation_operation",
                "clear does not take a value.",
            ))
            continue

        op, separator, value = operation.partition(":")
        if not separator:
            errors.append(ValidationError(
                "OPERATION", "invalid_annotation_operation",
                "Annotation operation must be clear or use op:value syntax.",
            ))
            continue

        if op == "set":
            parsed = _parse_annotation_entry(value, field="OPERATION")
            if isinstance(parsed, ValidationError):
                errors.append(parsed)
                continue
            path, scalar = parsed
            parsed_operations.append({"op": "set", "path": path, "value": scalar})
        elif op == "unset":
            path_error = _validate_annotation_path(value, field="OPERATION")
            if path_error is not None:
                errors.append(path_error)
                continue
            parsed_operations.append({"op": "unset", "path": value.split(".")})
        elif op in ("merge-file", "replace-file"):
            annotations, error = _load_annotations_file(value)
            if error is not None:
                errors.append(error)
                continue
            parsed_operations.append({
                "op": "merge" if op == "merge-file" else "replace",
                "value": annotations,
            })
        else:
            errors.append(ValidationError(
                "OPERATION", "invalid_annotation_operation",
                f"Unknown annotation operation {op!r}.",
            ))

    if not parsed_operations and not errors:
        errors.append(ValidationError(
            "OPERATION", "no_op",
            "At least one annotation operation is required.",
        ))

    return parsed_operations, errors


def _load_annotations_file(path: str) -> tuple[dict[str, Any], ValidationError | None]:
    try:
        raw = Path(path).read_bytes()
    except OSError as e:
        return {}, ValidationError(
            "--annotations-file",
            "invalid_annotations_file",
            f"Could not read annotations file {path!r}: {e.strerror or e}",
        )

    try:
        parsed = orjson.loads(raw)
    except orjson.JSONDecodeError as e:
        return {}, ValidationError(
            "--annotations-file",
            "invalid_annotations_file",
            f"Annotations file {path!r} is not valid JSON: {e}",
        )

    if not isinstance(parsed, dict):
        return {}, ValidationError(
            "--annotations-file",
            "invalid_annotations_file",
            "Annotations file must contain a JSON object at the root.",
        )

    return parsed, None


def _parse_annotation_entry(
    entry: str,
    *,
    field: str,
) -> tuple[list[str], Any] | ValidationError:
    key, separator, raw_value = entry.partition("=")
    if not separator:
        return ValidationError(
            field,
            "invalid_annotation",
            "Annotation must use key=value syntax.",
        )

    path_error = _validate_annotation_path(key, field=field)
    if path_error is not None:
        return path_error

    value, error = _parse_scalar_value(raw_value, field=field)
    if error is not None:
        return error
    return key.split("."), value


def _validate_annotation_path(path: str, *, field: str) -> ValidationError | None:
    if not path:
        return ValidationError(
            field,
            "invalid_annotation_path",
            "Annotation path cannot be empty.",
        )
    if any(segment == "" for segment in path.split(".")):
        return ValidationError(
            field,
            "invalid_annotation_path",
            f"Annotation path {path!r} contains an empty segment.",
        )
    return None


def _parse_scalar_value(raw_value: str, *, field: str) -> tuple[Any, ValidationError | None]:
    if raw_value == "true":
        return True, None
    if raw_value == "false":
        return False, None
    if raw_value == "null":
        return None, None
    if _INT_RE.match(raw_value):
        return int(raw_value), None
    if _FLOAT_RE.match(raw_value):
        value = float(raw_value)
        if math.isfinite(value):
            return value, None

    stripped = raw_value.lstrip()
    if stripped.startswith(("{", "[")):
        try:
            parsed = orjson.loads(raw_value)
        except orjson.JSONDecodeError:
            return raw_value, None
        if isinstance(parsed, dict | list):
            return None, ValidationError(
                field,
                "annotation_non_scalar",
                "Use --annotations-file for object or list annotation values.",
            )

    return raw_value, None


def _set_annotation_path(
    annotations: dict[str, Any],
    path: list[str],
    value: Any,
) -> ValidationError | None:
    current = annotations
    for segment in path[:-1]:
        if segment not in current:
            current[segment] = {}
        elif not isinstance(current[segment], dict):
            return ValidationError(
                "--annotation",
                "annotation_path_conflict",
                f"Annotation path {'.'.join(path)!r} conflicts with existing scalar value.",
            )
        current = current[segment]

    leaf = path[-1]
    if isinstance(current.get(leaf), dict):
        return ValidationError(
            "--annotation",
            "annotation_path_conflict",
            f"Annotation path {'.'.join(path)!r} would replace an existing object.",
        )

    current[leaf] = value
    return None
