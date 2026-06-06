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
# Drop the built frontend too, so the hatch hook always rebuilds it from
# source. The hook skips the npm build whenever src/twicc/static/frontend/
# already holds an index.html — great for the sdist→wheel and pip-install
# paths, but in this repo a stale dev build left there would otherwise be
# packaged as-is, silently shipping an outdated UI.
rm -rf dist/ src/twicc/static/frontend/

echo
echo "=== Building sdist + wheel ==="
uv build

echo
echo "=== Final dist/ ==="
ls -lh dist/
