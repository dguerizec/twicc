"""Hatch build hook: build the Vue.js frontend and fetch the bundled Codex binary."""

from __future__ import annotations

import io
import os
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

try:
    from hatchling.builders.hooks.plugin.interface import BuildHookInterface
except ImportError:
    # hatchling is only present in the build environment (it's in
    # `[build-system].requires` and uv installs it into a temporary env when
    # running `uv build`). Running this file standalone — `python hatch_build.py`
    # to repopulate `_bundled/` for an editable dev install — does not need
    # hatchling at all; only the CLI path below is exercised. Fall back to
    # `object` so the module still imports and `CustomBuildHook` remains
    # defined (and harmlessly unused) outside a Hatch invocation.
    BuildHookInterface = object  # type: ignore[assignment, misc]

FRONTEND_DIR = "frontend"
STATIC_DIR = os.path.join("src", "twicc", "static", "frontend")

# Codex binary is sourced from the openai/codex GitHub release that matches the
# vendored SDK in src/codex_app_server (extracted from tag rust-v0.131.0-alpha.4).
# We pull the same wheels that PyPI publishes under openai-codex-cli-bin==0.131.0a4,
# but from the GitHub release URL — this avoids depending on the PyPI package whose
# Linux wheels are tagged musllinux (rejected by uv on glibc systems even though
# the binary itself is a static-pie ELF that runs anywhere). We bundle the binary
# directly into our wheel; on Linux we restamp it as manylinux_2_17 (PyPI refuses
# the plain `linux_*` tag, and the static-pie binary is compatible with any
# glibc ≥ 2.17 system anyway), and on macOS / Windows we keep the upstream tag.
CODEX_RELEASE_TAG = "rust-v0.131.0-alpha.4"
CODEX_BIN_VERSION = "0.131.0a4"
CODEX_RELEASE_URL = f"https://github.com/openai/codex/releases/download/{CODEX_RELEASE_TAG}"

# Map normalized build-platform tags → upstream wheel filename. Keys cover the
# canonical wheel tags we will stamp on the resulting TwiCC wheel; values are
# the upstream wheels we extract the binary from. On Linux the upstream wheel
# is musllinux but we restamp our wheel manylinux_2_17 — see the module docstring
# above for why this is correct.
CODEX_BIN_WHEELS = {
    "manylinux_2_17_x86_64": f"openai_codex_cli_bin-{CODEX_BIN_VERSION}-py3-none-musllinux_1_1_x86_64.whl",
    "manylinux_2_17_aarch64": f"openai_codex_cli_bin-{CODEX_BIN_VERSION}-py3-none-musllinux_1_1_aarch64.whl",
    "macosx_11_0_arm64": f"openai_codex_cli_bin-{CODEX_BIN_VERSION}-py3-none-macosx_11_0_arm64.whl",
    "macosx_10_9_x86_64": f"openai_codex_cli_bin-{CODEX_BIN_VERSION}-py3-none-macosx_10_9_x86_64.whl",
    "win_amd64": f"openai_codex_cli_bin-{CODEX_BIN_VERSION}-py3-none-win_amd64.whl",
    "win_arm64": f"openai_codex_cli_bin-{CODEX_BIN_VERSION}-py3-none-win_arm64.whl",
}

# Where the binary lives inside the TwiCC wheel. Mirrors the upstream layout
# (codex_cli_bin/bin/codex) but rooted in the vendored SDK package so runtime
# discovery is a single `Path(codex_app_server.__file__).parent / "_bundled" / "codex"`.
CODEX_BUNDLED_DIR = os.path.join("src", "codex_app_server", "_bundled")
CODEX_BUNDLED_BIN_NAME = "codex"
CODEX_BUNDLED_BIN_NAME_WIN = "codex.exe"


def _resolve_target_platform_tag() -> str:
    """Decide which wheel platform tag we are building for.

    Priority: explicit env override (``TWICC_BUILD_PLATFORM``), then the host's
    canonical wheel tag normalized to one of our supported keys.
    """
    explicit = os.environ.get("TWICC_BUILD_PLATFORM")
    if explicit:
        return explicit

    # Fall back to the host platform. Linux is tagged manylinux_2_17 because
    # PyPI rejects the plain `linux_*` tag, and the static-pie binary is
    # ABI-compatible with any glibc ≥ 2.17 system.
    import platform

    machine = platform.machine().lower()
    system = sys.platform

    if system.startswith("linux"):
        if machine in ("x86_64", "amd64"):
            return "manylinux_2_17_x86_64"
        if machine in ("aarch64", "arm64"):
            return "manylinux_2_17_aarch64"
    elif system == "darwin":
        if machine == "arm64":
            return "macosx_11_0_arm64"
        if machine == "x86_64":
            return "macosx_10_9_x86_64"
    elif system == "win32":
        if machine in ("amd64", "x86_64"):
            return "win_amd64"
        if machine in ("arm64", "aarch64"):
            return "win_arm64"

    raise RuntimeError(
        f"Unsupported build platform: system={system!r}, machine={machine!r}. "
        f"Set TWICC_BUILD_PLATFORM to one of {sorted(CODEX_BIN_WHEELS)}."
    )


def _binary_filename_for(platform_tag: str) -> str:
    return CODEX_BUNDLED_BIN_NAME_WIN if platform_tag.startswith("win_") else CODEX_BUNDLED_BIN_NAME


def fetch_codex_binary(platform_tag: str, dest_dir: Path) -> Path:
    """Download the upstream wheel for ``platform_tag`` and place the codex
    binary in ``dest_dir``. Returns the full path to the extracted binary.

    The upstream wheel layout is ``codex_cli_bin/bin/{codex,codex.exe}`` plus
    Linux-only ``codex_cli_bin/bin/bwrap``. We extract only the codex binary —
    bwrap (the Linux user-namespace sandbox helper) is left out for now; Codex
    falls back gracefully when it is missing.
    """
    if platform_tag not in CODEX_BIN_WHEELS:
        raise RuntimeError(
            f"No Codex binary mapping for platform_tag={platform_tag!r}. "
            f"Known tags: {sorted(CODEX_BIN_WHEELS)}"
        )

    wheel_name = CODEX_BIN_WHEELS[platform_tag]
    url = f"{CODEX_RELEASE_URL}/{wheel_name}"
    bin_name = _binary_filename_for(platform_tag)
    member = f"codex_cli_bin/bin/{bin_name}"

    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / bin_name

    print(f"[hatch_build] Fetching Codex binary for {platform_tag} from {url}")
    with urllib.request.urlopen(url) as resp:
        data = resp.read()

    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        try:
            with zf.open(member) as src, open(dest_path, "wb") as dst:
                shutil.copyfileobj(src, dst)
        except KeyError as exc:
            raise RuntimeError(
                f"Upstream wheel {wheel_name} does not contain {member!r}"
            ) from exc

    # Make sure the binary is executable on POSIX. Windows ignores chmod, the
    # .exe is invokable as-is.
    if not platform_tag.startswith("win_"):
        os.chmod(dest_path, 0o755)

    print(f"[hatch_build] Wrote {dest_path} ({dest_path.stat().st_size:,} bytes)")
    return dest_path


class CustomBuildHook(BuildHookInterface):
    def initialize(self, version, build_data):
        self._build_frontend()
        if self.target_name == "wheel":
            self._bundle_codex_binary(build_data)

    def clean(self, versions):
        static_dir = os.path.join(self.root, STATIC_DIR)
        if os.path.exists(static_dir):
            shutil.rmtree(static_dir)

        bundled_dir = os.path.join(self.root, CODEX_BUNDLED_DIR)
        if os.path.exists(bundled_dir):
            shutil.rmtree(bundled_dir)

    # ------------------------------------------------------------------
    # Frontend (Vue) — pre-existing behavior
    # ------------------------------------------------------------------

    def _build_frontend(self) -> None:
        frontend_dir = os.path.join(self.root, FRONTEND_DIR)

        if not os.path.isfile(os.path.join(frontend_dir, "package.json")):
            # No frontend source (shouldn't happen but fail safe).
            return

        if self.target_name == "wheel":
            # When uv build creates sdist then wheel, the wheel is built from
            # the extracted sdist which already contains fresh assets.
            static_dir = os.path.join(self.root, STATIC_DIR)
            if os.path.isfile(os.path.join(static_dir, "index.html")):
                return

        subprocess.run(["npm", "ci"], cwd=frontend_dir, check=True)
        subprocess.run(["npm", "run", "build"], cwd=frontend_dir, check=True)

    # ------------------------------------------------------------------
    # Codex binary — fetched from upstream GitHub release at build time
    # ------------------------------------------------------------------

    def _bundle_codex_binary(self, build_data: dict) -> None:
        """Fetch the codex binary for the target platform, mark the wheel as
        platform-specific, and force-include the binary in the wheel.
        """
        platform_tag = _resolve_target_platform_tag()
        bundled_dir = Path(self.root) / CODEX_BUNDLED_DIR
        bin_name = _binary_filename_for(platform_tag)
        bin_path = _populate_bundled(platform_tag, bundled_dir)

        # Mark the wheel as platform-specific. Without these, hatchling produces
        # a pure-python wheel tagged `py3-none-any`, which would lie about its
        # contents (we ship a native binary).
        build_data["pure_python"] = False
        build_data["infer_tag"] = False
        build_data["tag"] = f"py3-none-{platform_tag}"

        # Force-include the binary at its in-wheel path. The `force_include`
        # map is `src on disk → path inside the wheel`. We rebase from
        # `src/codex_app_server/_bundled/<bin>` to `codex_app_server/_bundled/<bin>`
        # so the runtime can locate it as a sibling of the SDK's __init__.py.
        force_include = build_data.setdefault("force_include", {})
        force_include[str(bin_path)] = f"codex_app_server/_bundled/{bin_name}"


def _populate_bundled(platform_tag: str, bundled_dir: Path) -> Path:
    """Place the codex binary for ``platform_tag`` into ``bundled_dir``,
    clearing any stale files left there for a different platform, and refresh
    the ``.platform`` marker. Skips the network fetch (and the cleanup) when
    the existing cached binary already matches the requested platform.

    Shared between the Hatch build hook (one call per wheel target) and the
    standalone CLI (so an editable dev install can repopulate the directory).
    """
    bin_name = _binary_filename_for(platform_tag)
    bin_path = bundled_dir / bin_name
    marker_path = bundled_dir / ".platform"

    # We can't trust the binary's filename alone to identify its origin —
    # `codex` is the same name on Linux and macOS — so we track the platform
    # via a sentinel marker written next to the binary.
    cached_platform = (
        marker_path.read_text().strip()
        if marker_path.is_file()
        else None
    )
    if cached_platform != platform_tag:
        if bundled_dir.exists():
            for f in bundled_dir.iterdir():
                if f.is_file():
                    f.unlink()
        fetch_codex_binary(platform_tag, bundled_dir)
        marker_path.write_text(platform_tag)
    return bin_path


def _main_cli() -> None:
    """Allow running ``python hatch_build.py [platform_tag]`` to populate the
    bundled binary directory locally (useful for editable dev setups where the
    hatch hook does not run).
    """
    platform_tag = sys.argv[1] if len(sys.argv) > 1 else _resolve_target_platform_tag()
    repo_root = Path(__file__).resolve().parent
    _populate_bundled(platform_tag, repo_root / CODEX_BUNDLED_DIR)


if __name__ == "__main__":
    _main_cli()
