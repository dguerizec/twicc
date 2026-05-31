"""Parse free-form session annotations for create-session."""

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
        parsed = _parse_annotation_entry(entry)
        if isinstance(parsed, ValidationError):
            errors.append(parsed)
            continue

        path, value = parsed
        error = _set_annotation_path(annotations, path, value)
        if error is not None:
            errors.append(error)

    return annotations, errors


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


def _parse_annotation_entry(entry: str) -> tuple[list[str], Any] | ValidationError:
    key, separator, raw_value = entry.partition("=")
    if not separator:
        return ValidationError(
            "--annotation",
            "invalid_annotation",
            "Annotation must use key=value syntax.",
        )

    path = key.split(".")
    if any(segment == "" for segment in path):
        return ValidationError(
            "--annotation",
            "invalid_annotation_path",
            f"Annotation path {key!r} contains an empty segment.",
        )

    value, error = _parse_scalar_value(raw_value)
    if error is not None:
        return error
    return path, value


def _parse_scalar_value(raw_value: str) -> tuple[Any, ValidationError | None]:
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
                "--annotation",
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
