"""Hatch build hook: build the Vue.js frontend before packaging.

The Codex CLI binary used to be fetched here and stamped into a per-platform
wheel. That dance is gone now that `openai-codex-cli-bin` ships manylinux
wheels on PyPI — TwiCC depends on it directly and builds a single
platform-agnostic `py3-none-any` wheel.
"""

from __future__ import annotations

import os
import shutil
import subprocess

from hatchling.builders.hooks.plugin.interface import BuildHookInterface

FRONTEND_DIR = "frontend"
STATIC_DIR = os.path.join("src", "twicc", "static", "frontend")


class CustomBuildHook(BuildHookInterface):
    def initialize(self, version, build_data):
        self._build_frontend()

    def clean(self, versions):
        static_dir = os.path.join(self.root, STATIC_DIR)
        if os.path.exists(static_dir):
            shutil.rmtree(static_dir)

    def _build_frontend(self) -> None:
        frontend_dir = os.path.join(self.root, FRONTEND_DIR)

        if not os.path.isfile(os.path.join(frontend_dir, "package.json")):
            # No frontend source (shouldn't happen but fail safe).
            return

        # Skip the npm rebuild whenever the built assets are already
        # present. Two cases hit this:
        #   * `uv build` produces sdist first, then builds the wheel from
        #     the extracted sdist (which ships the pre-built assets).
        #   * Someone runs `pip install` from our published sdist on a
        #     machine without npm — the sdist already carries the assets.
        static_dir = os.path.join(self.root, STATIC_DIR)
        if os.path.isfile(os.path.join(static_dir, "index.html")):
            return

        subprocess.run(["npm", "ci"], cwd=frontend_dir, check=True)
        subprocess.run(["npm", "run", "build"], cwd=frontend_dir, check=True)
