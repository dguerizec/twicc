"""Tests for the deterministic project-color helpers and the assignment hook."""

import asyncio
import re

import pytest

from twicc.project_color import color_for_project, generate_project_color

HEX_RE = re.compile(r"^#[0-9a-f]{6}$")


def test_generate_is_valid_hex():
    assert HEX_RE.match(generate_project_color("twicc-poc"))


def test_generate_is_deterministic():
    assert generate_project_color("frontend") == generate_project_color("frontend")


def test_generate_differs_for_close_names():
    # SHA-256 avalanche: a one-character change must move the hue noticeably.
    assert generate_project_color("api") != generate_project_color("apo")
    assert generate_project_color("test") != generate_project_color("rest")


def test_color_for_project_prefers_name():
    assert color_for_project("my-name", "/home/x/other-dir") == generate_project_color("my-name")


def test_color_for_project_falls_back_to_final_segment():
    assert color_for_project(None, "/home/x/dev/twicc-poc") == generate_project_color("twicc-poc")
    assert color_for_project("  ", "/home/x/dev/twicc-poc/") == generate_project_color("twicc-poc")


def test_color_for_project_none_without_label():
    assert color_for_project(None, None) is None
    assert color_for_project("", "") is None


# ---------------------------------------------------------------------------
# ensure_project_color (DB-backed)
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_ensure_assigns_from_directory_and_is_idempotent():
    from twicc.core.models import Project
    from twicc.projects import ensure_project_color

    Project.objects.create(id="-pc-a", directory="/home/x/dev/twicc-poc")
    color = asyncio.run(ensure_project_color("-pc-a", "/home/x/dev/twicc-poc"))
    assert color == generate_project_color("twicc-poc")
    assert Project.objects.get(id="-pc-a").color == color

    # Second call is a no-op: already colored, returns None, color untouched.
    assert asyncio.run(ensure_project_color("-pc-a", "/home/x/dev/twicc-poc")) is None
    assert Project.objects.get(id="-pc-a").color == color


@pytest.mark.django_db(transaction=True)
def test_ensure_prefers_name_over_directory():
    from twicc.core.models import Project
    from twicc.projects import ensure_project_color

    Project.objects.create(id="-pc-b", directory="/home/x/dev/twicc-poc", name="custom")
    color = asyncio.run(ensure_project_color("-pc-b", "/home/x/dev/twicc-poc"))
    assert color == generate_project_color("custom")


@pytest.mark.django_db(transaction=True)
def test_ensure_skips_worktree():
    from twicc.core.models import Project
    from twicc.projects import ensure_project_color

    main = Project.objects.create(id="-pc-main", directory="/repo", name="repo")
    Project.objects.create(id="-pc-wt", directory="/repo/wt", worktree_of=main)
    assert asyncio.run(ensure_project_color("-pc-wt", "/repo/wt")) is None
    assert Project.objects.get(id="-pc-wt").color is None


@pytest.mark.django_db(transaction=True)
def test_ensure_keeps_user_chosen_color():
    from twicc.core.models import Project
    from twicc.projects import ensure_project_color

    Project.objects.create(id="-pc-c", directory="/home/x/dev/thing", color="#123456")
    assert asyncio.run(ensure_project_color("-pc-c", "/home/x/dev/thing")) is None
    assert Project.objects.get(id="-pc-c").color == "#123456"
