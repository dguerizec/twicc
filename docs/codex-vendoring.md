# Codex Python SDK — vendored

> Maintainer-level notes on how the Codex provider is bundled, and how to update it.

The Codex provider relies on OpenAI's Codex Python SDK (`codex_app_server`) plus the Codex CLI binary it drives over JSON-RPC. Neither is consumed from PyPI in a normal way:

- The SDK itself (`openai-codex-app-server-sdk`) is **not published on PyPI**. We vendor its source into `src/codex_app_server/`, extracted from the `openai/codex` GitHub repo at tag [`rust-v0.131.0-alpha.4`](https://github.com/openai/codex/releases/tag/rust-v0.131.0-alpha.4).
- The Codex CLI binary (`openai-codex-cli-bin`) **is** on PyPI, but the Linux wheels are tagged `musllinux_1_1_*` only — uv refuses those on glibc systems even though the binaries are static-pie ELFs that run anywhere. So we **fetch the wheel ourselves at build time** from the same GitHub release, extract just the binary, and bundle it into our wheel under a regular `linux_x86_64` (or `macosx_*` / `win_amd64`) platform tag.

## Layout

| Path                                              | Origin                                                    |
|---------------------------------------------------|-----------------------------------------------------------|
| `src/codex_app_server/`                           | Vendored SDK source. Treat as read-only — edits land upstream, then we re-sync. |
| `src/codex_app_server/_bundled/codex` (or `.exe`) | Fetched at build by `hatch_build.py`. Gitignored.         |
| `hatch_build.py`                                  | Hatch build hook: fetches the binary for the target platform and stamps the wheel platform-specific. |
| `scripts/build-release.sh`                        | Drives `hatch_build.py` once per target platform to produce all release wheels. |

## Updating to a newer Codex version

Assumes a local clone of `openai/codex` exists.

1. Pick a release tag matching the upstream version you want, e.g. `rust-v0.131.0-beta.1`. Verify it has both the matching `sdk/python/src/codex_app_server/` source and an `openai_codex_cli_bin-…whl` per target platform.
2. Update `CODEX_RELEASE_TAG` and `CODEX_BIN_VERSION` in `hatch_build.py`.
3. Re-vendor the SDK source:
   ```bash
   rm -rf src/codex_app_server
   mkdir -p src/codex_app_server
   ( cd <codex_repo> && \
     git archive <new_tag> sdk/python/src/codex_app_server ) | \
     tar -x --strip-components=4 -C src/codex_app_server
   ```
4. Diff the new SDK's `pyproject.toml` against ours — copy any new runtime dependency over (today only `pydantic>=2.12` is shared).
5. Run `./scripts/build-release.sh` and check each wheel installs and runs locally.

## Exit condition

The day OpenAI publishes a `manylinux` wheel for `openai-codex-cli-bin`, every line of this vendoring goes away: depend on the two packages from PyPI as regular dependencies, drop `src/codex_app_server/`, drop the binary-fetching logic in `hatch_build.py`, and the wheel is back to a single platform-agnostic `py3-none-any`.
