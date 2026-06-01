"""Tests for the annotation filter parser and ORM helper."""

from __future__ import annotations

import pytest

from twicc.cli._annotation_filters import (
    AnnotationFilter,
    AnnotationOp,
    parse_annotation_filter,
)


# --- parse_annotation_filter ---------------------------------------------

def test_parse_eq_string():
    assert parse_annotation_filter("role=implementer") == AnnotationFilter(
        ("role",), AnnotationOp.EQ, "implementer"
    )


def test_parse_eq_typed_int_and_float_and_bool_and_null():
    assert parse_annotation_filter("weight=5").value == 5
    assert parse_annotation_filter("ratio=1.5").value == 1.5
    assert parse_annotation_filter("active=true").value is True
    assert parse_annotation_filter("active=false").value is False
    assert parse_annotation_filter("note=null").value is None


def test_parse_eq_empty_string_value():
    assert parse_annotation_filter("note=").value == ""


def test_parse_dotted_key_splits_into_path_tuple():
    f = parse_annotation_filter("team.lead.name=alice")
    assert f.path == ("team", "lead", "name")
    assert f.op is AnnotationOp.EQ


def test_parse_ne():
    f = parse_annotation_filter("status!=done")
    assert f == AnnotationFilter(("status",), AnnotationOp.NE, "done")


def test_parse_exists_and_not_exists():
    assert parse_annotation_filter("team:exists") == AnnotationFilter(
        ("team",), AnnotationOp.EXISTS, None
    )
    assert parse_annotation_filter("team:not-exists") == AnnotationFilter(
        ("team",), AnnotationOp.NOT_EXISTS, None
    )


def test_parse_in_typed_elements():
    f = parse_annotation_filter("status:in:done,blocked,5,true")
    assert f.path == ("status",)
    assert f.op is AnnotationOp.IN
    assert f.value == ["done", "blocked", 5, True]


def test_parse_in_escaped_comma_and_backslash():
    f = parse_annotation_filter("note:in:hello\\,world,foo\\\\bar")
    assert f.value == ["hello,world", "foo\\bar"]


def test_parse_rejects_empty_spec():
    with pytest.raises(ValueError):
        parse_annotation_filter("")


def test_parse_rejects_empty_key():
    with pytest.raises(ValueError):
        parse_annotation_filter("=value")
    with pytest.raises(ValueError):
        parse_annotation_filter("!=value")
    with pytest.raises(ValueError):
        parse_annotation_filter(":exists")
    with pytest.raises(ValueError):
        parse_annotation_filter(":not-exists")
    with pytest.raises(ValueError):
        parse_annotation_filter(":in:value")


def test_parse_rejects_unknown_operator_suffix():
    with pytest.raises(ValueError, match="unknown operator"):
        parse_annotation_filter("key:foobar")


def test_parse_rejects_missing_operator():
    with pytest.raises(ValueError, match="no operator"):
        parse_annotation_filter("just-a-key")


def test_parse_rejects_empty_in_list():
    with pytest.raises(ValueError, match="empty :in: list"):
        parse_annotation_filter("status:in:")


def test_parse_rejects_invalid_key_characters():
    with pytest.raises(ValueError, match="invalid key"):
        parse_annotation_filter("space key=foo")
    with pytest.raises(ValueError, match="invalid key"):
        parse_annotation_filter("a@b=foo")


# --- apply_annotation_filters --------------------------------------------

from datetime import timedelta
from django.utils import timezone

from twicc.cli._annotation_filters import apply_annotation_filters
from twicc.core.models import Project, Session, SessionType


@pytest.fixture
def project(db):
    return Project.objects.create(id="-tmp-ann-filter", directory="/tmp/ann-filter")


def _mksession(project, sid, *, annotations, minutes=0):
    now = timezone.now() + timedelta(minutes=minutes)
    return Session.objects.create(
        id=sid,
        project=project,
        provider="claude_code",
        file_path=f"{sid}.jsonl",
        type=SessionType.SESSION,
        title=sid,
        created_at=now,
        last_new_content_at=now,
        user_message_count=1,
        annotations=annotations,
    )


def _ids(qs):
    return sorted(qs.values_list("id", flat=True))


def test_apply_eq_matches_typed_value(project):
    _mksession(project, "S1", annotations={"role": "implementer"})
    _mksession(project, "S2", annotations={"role": "reviewer"})
    _mksession(project, "S3", annotations={"role": "implementer", "weight": 5})

    qs = Session.objects.all()
    out = apply_annotation_filters(
        qs, [AnnotationFilter(("role",), AnnotationOp.EQ, "implementer")]
    )
    assert _ids(out) == ["S1", "S3"]


def test_apply_eq_distinguishes_int_from_string(project):
    _mksession(project, "INT5", annotations={"weight": 5})
    _mksession(project, "STR5", annotations={"weight": "5"})

    out = apply_annotation_filters(
        Session.objects.all(),
        [AnnotationFilter(("weight",), AnnotationOp.EQ, 5)],
    )
    assert _ids(out) == ["INT5"]

    out = apply_annotation_filters(
        Session.objects.all(),
        [AnnotationFilter(("weight",), AnnotationOp.EQ, "5")],
    )
    assert _ids(out) == ["STR5"]


def test_apply_ne_excludes_matching_but_includes_absent(project):
    _mksession(project, "DONE", annotations={"status": "done"})
    _mksession(project, "TODO", annotations={"status": "todo"})
    _mksession(project, "NONE", annotations={})

    out = apply_annotation_filters(
        Session.objects.all(),
        [AnnotationFilter(("status",), AnnotationOp.NE, "done")],
    )
    # NE matches TODO and NONE (absent key); documented behaviour.
    assert _ids(out) == ["NONE", "TODO"]


def test_apply_ne_with_null_value_filter(project):
    """NE with f.value=None: explicit null excluded, absent and non-null included."""
    _mksession(project, "NULL", annotations={"status": None})
    _mksession(project, "ABSENT", annotations={})
    _mksession(project, "SET", annotations={"status": "done"})

    out = apply_annotation_filters(
        Session.objects.all(),
        [AnnotationFilter(("status",), AnnotationOp.NE, None)],
    )
    assert _ids(out) == ["ABSENT", "SET"]


def test_apply_exists_matches_present_keys_including_null(project):
    _mksession(project, "PRESENT", annotations={"team": "frontend"})
    _mksession(project, "NULL", annotations={"team": None})
    _mksession(project, "ABSENT", annotations={})

    out = apply_annotation_filters(
        Session.objects.all(),
        [AnnotationFilter(("team",), AnnotationOp.EXISTS, None)],
    )
    assert _ids(out) == ["NULL", "PRESENT"]


def test_apply_not_exists_matches_absent_only(project):
    _mksession(project, "PRESENT", annotations={"team": "frontend"})
    _mksession(project, "NULL", annotations={"team": None})
    _mksession(project, "ABSENT", annotations={})

    out = apply_annotation_filters(
        Session.objects.all(),
        [AnnotationFilter(("team",), AnnotationOp.NOT_EXISTS, None)],
    )
    assert _ids(out) == ["ABSENT"]


def test_apply_in_typed_elements(project):
    _mksession(project, "DONE", annotations={"status": "done"})
    _mksession(project, "BLOCKED", annotations={"status": "blocked"})
    _mksession(project, "TODO", annotations={"status": "todo"})

    out = apply_annotation_filters(
        Session.objects.all(),
        [AnnotationFilter(("status",), AnnotationOp.IN, ["done", "blocked"])],
    )
    assert _ids(out) == ["BLOCKED", "DONE"]


def test_apply_nested_dotted_path(project):
    _mksession(project, "ALICE", annotations={"team": {"lead": "alice"}})
    _mksession(project, "BOB", annotations={"team": {"lead": "bob"}})
    _mksession(project, "FLAT", annotations={"team": "alice"})

    out = apply_annotation_filters(
        Session.objects.all(),
        [AnnotationFilter(("team", "lead"), AnnotationOp.EQ, "alice")],
    )
    assert _ids(out) == ["ALICE"]


def test_apply_multiple_filters_compose_with_and(project):
    _mksession(project, "MATCH", annotations={"role": "impl", "status": "done"})
    _mksession(project, "ROLE_ONLY", annotations={"role": "impl", "status": "todo"})
    _mksession(project, "STATUS_ONLY", annotations={"role": "reviewer", "status": "done"})

    out = apply_annotation_filters(
        Session.objects.all(),
        [
            AnnotationFilter(("role",), AnnotationOp.EQ, "impl"),
            AnnotationFilter(("status",), AnnotationOp.EQ, "done"),
        ],
    )
    assert _ids(out) == ["MATCH"]


def test_apply_with_custom_field_prefix_routes_lookup(project):
    """Verify the `field=` keyword changes the JSONField lookup prefix.

    Using a non-existent field demonstrates that the prefix is wired through
    to the ORM. We expect a FieldError because 'nonexistent_jsonfield' doesn't
    exist on Session — confirming the prefix reached the ORM and was not
    silently swapped for the default.
    """
    from django.core.exceptions import FieldError
    with pytest.raises(FieldError):
        apply_annotation_filters(
            Session.objects.all(),
            [AnnotationFilter(("role",), AnnotationOp.EQ, "impl")],
            field="nonexistent_jsonfield",
        )
