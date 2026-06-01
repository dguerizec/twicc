"""Tests for create-session annotation parsing."""

from __future__ import annotations

import orjson
import pytest

from twicc.cli._drop_request.annotations import (
    parse_annotation_update_operations,
    parse_annotations,
)
from twicc.core.services import session_update
from twicc.core.services.session_creation import _validate_annotations
from twicc.core.services.session_update import apply_annotation_operations


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


def test_parse_annotation_update_operations_preserves_order(tmp_path):
    path = tmp_path / "annotations.json"
    path.write_bytes(orjson.dumps({"from_file": True}))

    operations, errors = parse_annotation_update_operations([
        "unset:foo",
        "set:foo.point=bar",
        f"merge-file:{path}",
        "clear",
    ])

    assert errors == []
    assert operations == [
        {"op": "unset", "path": ["foo"]},
        {"op": "set", "path": ["foo", "point"], "value": "bar"},
        {"op": "merge", "value": {"from_file": True}},
        {"op": "clear"},
    ]


def test_parse_annotation_update_operations_rejects_invalid_operations():
    operations, errors = parse_annotation_update_operations([
        "wat:foo",
        "clear:foo",
        "unset:",
        "set:items=[1,2]",
    ])

    assert operations == []
    assert [error.code for error in errors] == [
        "invalid_annotation_operation",
        "invalid_annotation_operation",
        "invalid_annotation_path",
        "annotation_non_scalar",
    ]


def test_apply_annotation_operations_supports_unset_then_set():
    current = {"foo": "old", "keep": True}

    annotations, errors = apply_annotation_operations(current, [
        {"op": "unset", "path": ["foo"]},
        {"op": "set", "path": ["foo", "point"], "value": "bar"},
    ])

    assert errors == []
    assert current == {"foo": "old", "keep": True}
    assert annotations == {"foo": {"point": "bar"}, "keep": True}


def test_apply_annotation_operations_merges_replaces_and_clears():
    annotations, errors = apply_annotation_operations(
        {"task": {"priority": 1, "ids": [1]}, "temporary": True},
        [
            {"op": "merge", "value": {"task": {"priority": 2, "done": False}}},
            {"op": "replace", "value": {"role": "reviewer"}},
            {"op": "clear"},
            {"op": "set", "path": ["ready"], "value": True},
        ],
    )

    assert errors == []
    assert annotations == {"ready": True}


def test_apply_annotation_operations_merges_nested_objects():
    annotations, errors = apply_annotation_operations(
        {
            "owner": "alice",
            "task": {
                "state": "open",
                "meta": {
                    "priority": 1,
                    "tags": ["old"],
                },
            },
            "replace_object": {"a": 1},
        },
        [
            {
                "op": "merge",
                "value": {
                    "owner": {"name": "bob"},
                    "task": {
                        "meta": {
                            "priority": 2,
                            "tags": ["new"],
                            "done": False,
                        },
                    },
                    "replace_object": "scalar",
                },
            },
        ],
    )

    assert errors == []
    assert annotations == {
        "owner": {"name": "bob"},
        "task": {
            "state": "open",
            "meta": {
                "priority": 2,
                "tags": ["new"],
                "done": False,
            },
        },
        "replace_object": "scalar",
    }


def test_apply_annotation_operations_rejects_path_conflicts():
    annotations, errors = apply_annotation_operations({"foo": "old"}, [
        {"op": "set", "path": ["foo", "point"], "value": "bar"},
    ])

    assert annotations == {}
    assert len(errors) == 1
    assert errors[0].code == "annotation_path_conflict"


@pytest.mark.parametrize(
    ("operation", "helper_name", "message"),
    [
        (
            {"op": "merge", "value": {"new": True}},
            "_merge_annotation_object",
            "merge exploded",
        ),
        (
            {"op": "set", "path": ["new"], "value": True},
            "_set_annotation_path",
            "set exploded",
        ),
        (
            {"op": "unset", "path": ["old"]},
            "_unset_annotation_path",
            "unset exploded",
        ),
    ],
)
def test_apply_annotation_operations_returns_structured_error_when_mutation_raises(
    monkeypatch,
    operation,
    helper_name,
    message,
):
    def raise_error(*args, **kwargs):
        raise RuntimeError(message)

    monkeypatch.setattr(session_update, helper_name, raise_error)

    annotations, errors = session_update.apply_annotation_operations(
        {"keep": True},
        [operation],
    )

    assert annotations == {}
    assert len(errors) == 1
    assert errors[0].field == "operations"
    assert errors[0].code == "annotation_operation_failed"
    assert f"Operation #1 ({operation['op']!r})" in errors[0].message
    assert message in errors[0].message


def test_apply_annotation_operations_returns_structured_error_when_replace_raises(
    monkeypatch,
):
    def raise_deepcopy(value):
        raise RuntimeError("replace exploded")

    monkeypatch.setattr(session_update, "deepcopy", raise_deepcopy)

    annotations, errors = session_update.apply_annotation_operations(
        None,
        [{"op": "replace", "value": {"new": True}}],
    )

    assert annotations == {}
    assert len(errors) == 1
    assert errors[0].field == "operations"
    assert errors[0].code == "annotation_operation_failed"
    assert "Operation #1 ('replace')" in errors[0].message
    assert "replace exploded" in errors[0].message
