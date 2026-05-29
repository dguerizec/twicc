#!/usr/bin/env bash
#
# Build a release: one sdist + one platform-agnostic wheel.
#
# TwiCC depends on `openai-codex-cli-bin` (PyPI, manylinux/macos/win wheels)
# for the Codex CLI binary instead of bundling it per-platform. The wheel is
# pure Python (`py3-none-any`).

set -euo pipefail

cd "$(dirname "$0")/.."

echo "=== Cleaning previous artifacts ==="
rm -rf dist/

echo
echo "=== Building sdist + wheel ==="
uv build

echo
echo "=== Final dist/ ==="
ls -lh dist/
