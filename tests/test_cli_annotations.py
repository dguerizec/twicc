"""Tests for create-session annotation parsing."""

from __future__ import annotations

import orjson

from twicc.cli._drop_request.annotations import parse_annotations
from twicc.core.services.session_creation import _validate_annotations


def test_parse_annotations_supports_dotted_scalar_values():
    annotations, errors = parse_annotations(
        [
            "role=reviewer",
            "task.priority=2",
            "task.blocking=false",
            "task.ratio=1.5",
            "task.note=",
            "result=null",
        ],
        None,
    )

    assert errors == []
    assert annotations == {
        "role": "reviewer",
        "task": {
            "priority": 2,
            "blocking": False,
            "ratio": 1.5,
            "note": "",
        },
        "result": None,
    }


def test_parse_annotations_merges_file_then_flags(tmp_path):
    path = tmp_path / "annotations.json"
    path.write_bytes(orjson.dumps({
        "role": "base",
        "task": {"ids": [1, 2], "priority": 1},
    }))

    annotations, errors = parse_annotations(
        ["role=reviewer", "task.priority=2"],
        str(path),
    )

    assert errors == []
    assert annotations == {
        "role": "reviewer",
        "task": {"ids": [1, 2], "priority": 2},
    }


def test_parse_annotations_rejects_structure_conflicts_and_inline_objects():
    annotations, errors = parse_annotations(
        ["task=abc", "task.id=42", "items=[1,2]"],
        None,
    )

    assert annotations == {"task": "abc"}
    assert [error.code for error in errors] == [
        "annotation_path_conflict",
        "annotation_non_scalar",
    ]


def test_parse_annotations_rejects_non_object_file(tmp_path):
    path = tmp_path / "annotations.json"
    path.write_bytes(orjson.dumps(["not", "an", "object"]))

    annotations, errors = parse_annotations([], str(path))

    assert annotations == {}
    assert len(errors) == 1
    assert errors[0].code == "invalid_annotations_file"


def test_service_annotation_validation_requires_json_object():
    assert _validate_annotations({"ok": True}) is None

    error = _validate_annotations(["not", "an", "object"])
    assert error is not None
    assert error.code == "invalid_annotations"

    error = _validate_annotations({1: "bad key"})
    assert error is not None
    assert error.code == "invalid_annotations"
