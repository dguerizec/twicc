"""Download and cache the Codex CLI runtime from GitHub Releases.

OpenAI stopped publishing stable ``openai-codex-cli-bin`` wheels on PyPI after
0.136.0 (the wheel is ~122 MB, above practical PyPI quotas), but every tagged
stable ships the same manylinux/macOS wheels as GitHub Release assets. Since we
can no longer depend on the PyPI package, we download the platform wheel at
launch, extract it into a shared cache, and point the SDK at the extracted
binary via ``CodexConfig(codex_bin=...)``.

The whole ``codex_cli_bin/`` tree is extracted (not just the ``codex`` binary)
so the sibling resources ship too: ``codex-resources/bwrap`` (Linux sandbox
helper) and ``codex-resources/zsh`` are found by ``codex`` relative to its own
path, and ``codex-path/rg`` (ripgrep) is put on PATH by
``twicc.providers.codex.bin.make_codex_config``.

Cache location: ``$XDG_CACHE_HOME/twicc/codex-runtime/<version>/`` (default
``~/.cache/twicc/...``). Shared across the main instance and every worktree,
so the ~300 MB extracted tree is downloaded once. Independent of
``TWICC_DATA_DIR`` on purpose — this is a regenerable runtime cache, not user
data.
"""

from __future__ import annotations

import asyncio
import fcntl
import hashlib
import logging
import os
import platform
import shutil
import urllib.request
import zipfile
from contextlib import contextmanager
from pathlib import Path

logger = logging.getLogger(__name__)

CODEX_VERSION = "0.144.2"
CODEX_RELEASE_TAG = "rust-v0.144.2"
_RELEASE_URL = f"https://github.com/openai/codex/releases/download/{CODEX_RELEASE_TAG}"

# our platform key -> (wheel filename, sha256 of the wheel)
# Recompute the sha256 values on every version bump (see the bump note at the
# bottom of docs/plans/2026-07-09-codex-runtime-download-plan.md).
_WHEELS: dict[str, tuple[str, str]] = {
    "manylinux_2_17_x86_64": (
        f"openai_codex_cli_bin-{CODEX_VERSION}-py3-none-manylinux_2_17_x86_64.whl",
        "27c19483a29c65b03a900c743796a696dd0f1153d71cf92a6ff710f830b72723",
    ),
    "manylinux_2_17_aarch64": (
        f"openai_codex_cli_bin-{CODEX_VERSION}-py3-none-manylinux_2_17_aarch64.whl",
        "e5bb74d1a191ded1695a952356cf9ddb0ef775029b7dc6d161d442f591daed06",
    ),
    "macosx_11_0_arm64": (
        f"openai_codex_cli_bin-{CODEX_VERSION}-py3-none-macosx_11_0_arm64.whl",
        "b24c5ef836a67d7c09d2ab42000305c6d5929f493b987737e883cf01a4d07d1e",
    ),
    "macosx_10_9_x86_64": (
        f"openai_codex_cli_bin-{CODEX_VERSION}-py3-none-macosx_10_9_x86_64.whl",
        "61300866f3c237651b79cb9f7e32183f240a0a04f5e91424edb6cc9f61292e41",
    ),
}

# Executable bits to restore after extraction (relative to the store dir).
_EXECUTABLES = (
    "codex_cli_bin/bin/codex",
    "codex_cli_bin/bin/codex-code-mode-host",
    "codex_cli_bin/codex-path/rg",
    "codex_cli_bin/codex-resources/bwrap",
    "codex_cli_bin/codex-resources/zsh/bin/zsh",
)


class CodexRuntimeError(RuntimeError):
    """Base error for Codex runtime provisioning."""


class CodexRuntimeUnsupportedPlatform(CodexRuntimeError):
    """The current OS/arch has no published Codex binary."""


class CodexRuntimeIntegrityError(CodexRuntimeError):
    """Downloaded wheel failed sha256 verification."""


# In-process short-circuit once the runtime is confirmed present.
_ready_in_process = False


def _platform_tag() -> str:
    system = platform.system()
    machine = platform.machine().lower()
    if system == "Linux":
        if machine in ("x86_64", "amd64"):
            return "manylinux_2_17_x86_64"
        if machine in ("aarch64", "arm64"):
            return "manylinux_2_17_aarch64"
    elif system == "Darwin":
        if machine == "arm64":
            return "macosx_11_0_arm64"
        if machine == "x86_64":
            return "macosx_10_9_x86_64"
    raise CodexRuntimeUnsupportedPlatform(
        f"No Codex binary for system={system!r} machine={machine!r}. "
        f"Supported: {sorted(_WHEELS)}."
    )


def _cache_root() -> Path:
    xdg = os.environ.get("XDG_CACHE_HOME", "").strip()
    base = Path(xdg) if xdg else Path.home() / ".cache"
    return base / "twicc" / "codex-runtime"


def _store_dir() -> Path:
    return _cache_root() / CODEX_VERSION


def _ready_marker() -> Path:
    return _store_dir() / ".ready"


def codex_binary_path() -> Path:
    return _store_dir() / "codex_cli_bin" / "bin" / "codex"


def codex_path_dir() -> Path:
    """Directory holding ``rg`` — to prepend to PATH (SDK does this itself only
    when codex_bin is auto-resolved, which is never our case)."""
    return _store_dir() / "codex_cli_bin" / "codex-path"


def is_runtime_ready() -> bool:
    return _ready_marker().is_file() and codex_binary_path().is_file()


@contextmanager
def _file_lock(path: Path):
    """Inter-process exclusive lock (POSIX flock). TwiCC is Linux/macOS only."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(exist_ok=True)
    handle = open(path, "r+")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(handle, fcntl.LOCK_UN)
        handle.close()


def _download(url: str, dest: Path) -> None:
    logger.info("Downloading Codex runtime %s from %s", CODEX_VERSION, url)
    with urllib.request.urlopen(url) as resp:  # follows the GitHub 302 redirect
        total = int(resp.headers.get("Content-Length", 0))
        downloaded = 0
        next_pct = 10
        with open(dest, "wb") as out:
            while True:
                chunk = resp.read(1024 * 1024)
                if not chunk:
                    break
                out.write(chunk)
                downloaded += len(chunk)
                if total:
                    pct = downloaded * 100 // total
                    if pct >= next_pct:
                        logger.info(
                            "Codex runtime download: %d%% (%d/%d MB)",
                            pct, downloaded // (1024 * 1024), total // (1024 * 1024),
                        )
                        next_pct += 10
    logger.info("Codex runtime download complete (%d bytes)", downloaded)


def _verify_sha256(path: Path, expected: str) -> None:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    got = digest.hexdigest()
    if got != expected:
        path.unlink(missing_ok=True)
        raise CodexRuntimeIntegrityError(
            f"sha256 mismatch for {path.name}: expected {expected}, got {got}"
        )


def _download_and_extract() -> None:
    tag = _platform_tag()
    wheel_name, expected_sha = _WHEELS[tag]
    url = f"{_RELEASE_URL}/{wheel_name}"

    cache_root = _cache_root()
    cache_root.mkdir(parents=True, exist_ok=True)

    with _file_lock(cache_root / f"{CODEX_VERSION}.lock"):
        if is_runtime_ready():
            return

        tmp_whl = cache_root / f"{wheel_name}.part"
        tmp_extract = cache_root / f"{CODEX_VERSION}.tmp"
        store = _store_dir()

        _download(url, tmp_whl)
        _verify_sha256(tmp_whl, expected_sha)

        shutil.rmtree(tmp_extract, ignore_errors=True)
        with zipfile.ZipFile(tmp_whl) as zf:
            zf.extractall(tmp_extract)
        tmp_whl.unlink(missing_ok=True)

        for rel in _EXECUTABLES:
            member = tmp_extract / rel
            if member.is_file():
                member.chmod(0o755)

        # Atomic-ish swap: remove any stale store, move the fresh tree in,
        # then write the .ready marker last so a crash mid-swap is never
        # mistaken for a ready runtime.
        shutil.rmtree(store, ignore_errors=True)
        tmp_extract.rename(store)
        _ready_marker().write_text(f"{CODEX_VERSION}\n{tag}\n", encoding="utf-8")
        logger.info("Codex runtime %s ready at %s", CODEX_VERSION, store)


def ensure_codex_runtime_sync() -> Path:
    """Ensure the runtime is present on disk; download+extract if missing.

    Blocking. Idempotent. Safe across threads and processes. Returns the store
    directory. Callers in an async context must use :func:`ensure_codex_runtime`
    instead so the download runs off the event loop.
    """
    global _ready_in_process
    if _ready_in_process or is_runtime_ready():
        _ready_in_process = True
        return _store_dir()
    _download_and_extract()
    _ready_in_process = True
    return _store_dir()


async def ensure_codex_runtime() -> Path:
    """Async wrapper: run the (blocking) provisioning in a worker thread."""
    return await asyncio.to_thread(ensure_codex_runtime_sync)
