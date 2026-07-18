import pytest

from twicc.telemetry import install_method


@pytest.mark.parametrize(
    ("executable", "expected"),
    [
        ("/home/u/.local/share/uv/tools/twicc/bin/python", "uv-tool"),
        ("/home/u/.cache/uv/archive-v0/AbCd/bin/python", "uvx"),
        ("/home/u/.local/share/pipx/venvs/twicc/bin/python", "pipx"),
        ("/home/u/.local/pipx/venvs/twicc/bin/python", "pipx"),
        ("/home/u/venvs/main/bin/python", "pip"),
        ("/usr/bin/python3", "other"),
    ],
)
def test_detection_from_executable(monkeypatch, executable, expected):
    monkeypatch.setattr(install_method, "_is_git_checkout", lambda: False)
    monkeypatch.setattr(install_method.sys, "executable", executable)
    assert install_method.detect_install_method() == expected


def test_git_checkout_wins(monkeypatch):
    monkeypatch.setattr(install_method, "_is_git_checkout", lambda: True)
    assert install_method.detect_install_method() == "git-dev"


def test_detection_alternate_uvx_cache_signature(monkeypatch):
    # The rarer "/uv/cache/" layout (as opposed to "/.cache/uv/"), covered
    # by the second half of the uvx OR-branch in detect_install_method.
    monkeypatch.setattr(install_method, "_is_git_checkout", lambda: False)
    monkeypatch.setattr(
        install_method.sys, "executable", "/home/u/uv/cache/archive/AbCd/bin/python"
    )
    assert install_method.detect_install_method() == "uvx"


def test_in_virtualenv_detects_real_pyvenv_cfg_without_venv_in_name(tmp_path):
    # Production venv whose directory name doesn't mention "venv" at all
    # (e.g. /opt/app/bin/python) — only detectable via the real pyvenv.cfg
    # file, not the path-signature fallback.
    venv_root = tmp_path / "opt" / "app"
    (venv_root / "bin").mkdir(parents=True)
    (venv_root / "pyvenv.cfg").write_text("home = /usr/bin\n")
    exe = venv_root / "bin" / "python"
    assert install_method._in_virtualenv(exe) is True


def test_is_git_checkout_true_for_real_git_directory(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    package_dir = repo / "src" / "twicc"
    package_dir.mkdir(parents=True)
    (repo / ".git").mkdir()
    (package_dir / "__init__.py").write_text("")
    monkeypatch.setattr(
        install_method.twicc, "__file__", str(package_dir / "__init__.py")
    )
    assert install_method._is_git_checkout() is True


def test_is_git_checkout_false_when_pyproject_stops_the_walk_before_git(
    tmp_path, monkeypatch
):
    # A pyproject.toml above the package source stops the upward walk
    # before it would otherwise reach a .git dir further up — e.g. an
    # installed package nested inside an unrelated outer git repo.
    outer = tmp_path / "outer"
    (outer / ".git").mkdir(parents=True)
    repo = outer / "repo"
    package_dir = repo / "src" / "twicc"
    package_dir.mkdir(parents=True)
    (repo / "pyproject.toml").write_text("")
    (package_dir / "__init__.py").write_text("")
    monkeypatch.setattr(
        install_method.twicc, "__file__", str(package_dir / "__init__.py")
    )
    assert install_method._is_git_checkout() is False
