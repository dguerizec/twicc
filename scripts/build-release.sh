#!/usr/bin/env bash
#
# Build a release: one sdist plus one wheel per supported target platform.
#
# Why multi-wheel: each wheel ships the Codex CLI binary at
# src/codex_app_server/_bundled/codex (or codex.exe on Windows), which is
# platform-specific. The build hook stamps each wheel with its target
# platform tag so `uv tool install twicc` resolves the right one on every
# machine.
#
# The day OpenAI publishes a manylinux wheel for openai-codex-cli-bin (today
# only musllinux is on PyPI, which uv refuses on glibc), we can retire this
# script and the surrounding bundling logic, depend on the PyPI package
# directly, and go back to a single `py3-none-any` wheel.

set -euo pipefail

cd "$(dirname "$0")/.."

PLATFORMS=(
    linux_x86_64
    macosx_11_0_arm64
    macosx_10_9_x86_64
    win_amd64
)

echo "=== Cleaning previous artifacts ==="
rm -rf dist/ src/codex_app_server/_bundled

echo
echo "=== Building sdist (platform-agnostic) ==="
uv build --sdist

for tag in "${PLATFORMS[@]}"; do
    echo
    echo "=== Building wheel for ${tag} ==="
    TWICC_BUILD_PLATFORM="${tag}" uv build --wheel
done

echo
echo "=== Final dist/ ==="
ls -lh dist/
