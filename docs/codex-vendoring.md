# Codex Python SDK — vendored

> Maintainer-level notes on how the Codex provider is bundled, and how to update it.

The Codex provider relies on OpenAI's Codex Python SDK (`openai_codex`) plus the Codex CLI binary it drives over JSON-RPC.

- The **SDK** is vendored from the `openai/codex` repository at tag [`rust-v0.135.0`](https://github.com/openai/codex/releases/tag/rust-v0.135.0). A PyPI release (`openai-codex`) exists but currently pins an older runtime version, so we stay on the vendored source to ride a known-good combination with the matching upstream tag.
- The **CLI binary** is a regular PyPI dependency: `openai-codex-cli-bin==0.135.0`. Since version `0.133.0` it publishes manylinux / macOS / Windows wheels — no more local bundling, no more per-platform TwiCC wheels.

## Layout

| Path                                         | Origin                                                                              |
|----------------------------------------------|-------------------------------------------------------------------------------------|
| `src/openai_codex/`                          | Vendored SDK source. Treat as read-only — edits land upstream, then we re-sync.     |
| `pyproject.toml` → `openai-codex-cli-bin==…` | Codex CLI binary, pulled as a regular PyPI dependency at install time.              |
| `src/twicc/providers/codex/sdk_wrappers.py`  | TwiCC subclasses (`TwiccAsyncCodex`, `TwiccAsyncThread`) that expose `*_with_policy` methods so we can keep our 5 fine-grained presets — the upstream SDK now only exposes the coarse `ApprovalMode`. |
| `src/twicc/providers/codex/bin.py`           | Thin wrapper around `codex_cli_bin.bundled_codex_path()` so every code path resolves the binary the same way. |

## Updating to a newer Codex version

Assumes the new version is already published on PyPI as `openai-codex-cli-bin` **with manylinux wheels**.

1. Pick a release tag matching the upstream version you want, e.g. `rust-v0.136.0`. Verify that:
   - `sdk/python/src/openai_codex/` exists at that tag
   - `openai-codex-cli-bin==<matching version>` is published on PyPI **with manylinux wheels** (not just musllinux)
2. Re-vendor the SDK source. Example using a GitHub tarball (avoids touching any local clone of `openai/codex`):
   ```bash
   rm -rf src/openai_codex
   mkdir -p /tmp/codex-sdk
   curl -sL "https://codeload.github.com/openai/codex/tar.gz/refs/tags/<new_tag>" \
     | tar -xz --strip-components=4 --wildcards "*/sdk/python/src/openai_codex/*" -C /tmp/codex-sdk
   mv /tmp/codex-sdk/openai_codex src/openai_codex
   ```
3. Bump `openai-codex-cli-bin==<matching version>` in `pyproject.toml`, run `uv lock` and `uv sync`.
4. Diff the new SDK's `pyproject.toml` against ours — copy any new runtime dependency over (today only `pydantic>=2.12` is shared).
5. Run the checklist from the `reference_codex_sdk_update_procedure.md` memory: verify the monkey-patch path `_client._sync._approval_handler`, that `ThreadStartParams`/`TurnStartParams` still accept `approval_policy`/`approvals_reviewer`/`sandbox(_policy)`, that the SDK subclassing in `sdk_wrappers.py` still compiles, that `codex_cli_bin.bundled_codex_path` still exists, etc.
6. Run `./scripts/build-release.sh` and check the resulting wheel installs and runs locally.

## Why we still vendor the SDK

The PyPI package `openai-codex` exists but currently pins an older runtime than the wheel we want to ship, and TwiCC reaches into private SDK attributes:

- `_client._sync._approval_handler` for the approval bridge (`CodexAgent.__init__`)
- `_client._sync._proc` for the subprocess PID (`CodexAgent.get_pid`)
- `_inputs._normalize_run_input` / `_inputs._to_wire_input` for the `*_with_policy` wrappers

Vendoring keeps the SDK pinned to the same commit as the binary we depend on, and makes any upstream refactor of those private attributes visible in the diff (during the next vendor refresh) rather than as a runtime `AttributeError`.

If/when the PyPI SDK catches up and TwiCC migrates off those private attributes (e.g. when OpenAI exposes a public approval-handler hook), we can drop the vendoring entirely and depend on `openai-codex` from PyPI like any other library.
