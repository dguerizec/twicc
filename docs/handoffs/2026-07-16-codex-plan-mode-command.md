# Codex `/plan` command and collaboration mode hand-off

## Status

Research and implementation direction are complete. No product code has been
changed for this feature yet.

The agreed first increment is:

1. Handle Codex `/plan` as a TwiCC hardcoded slash command.
2. Add `/plan` to the Codex built-in command list shown by the frontend
   autocomplete.
3. Use Codex's native collaboration mode protocol through TwiCC's existing SDK
   wrapper layer.

Do not model Codex Plan mode as a permission mode.

## Product model

Claude Code and Codex use the word "plan" for different protocol concepts.

- Claude Code exposes `plan` as a permission mode. TwiCC stores it in
  `Session.permission_mode`.
- Codex exposes `plan` as a collaboration mode. Its known collaboration modes
  are `default` and `plan`.
- Codex permissions remain a separate sandbox/approval axis. A Codex turn may
  therefore be in Plan collaboration mode while using any applicable TwiCC
  permission preset (`strict`, `read_only`, `auto`, `yolo`, and so on).
- Codex's `update_plan` tool is also unrelated. It updates task/plan progress
  displayed by TwiCC; calling it does not enter Plan collaboration mode.

The official Codex clients enter Plan mode with `/plan`; current documentation
also accepts an inline prompt such as:

```text
/plan Propose a migration plan for this service
```

Official references:

- <https://developers.openai.com/codex/cli/slash-commands>
- <https://developers.openai.com/codex/app-server>

The official command table calls `/plan` a toggle, while the detailed prose says
"switch ... into plan mode." Before implementing an exit/toggle behavior, see
the open decision below.

## Verified App Server protocol

The App Server treats collaboration mode as a per-thread/per-turn setting, not
as a `thread/start` option.

The current protocol exposes:

- `turn/start.collaborationMode`: applies a collaboration mode to that turn and
  subsequent turns.
- `thread/settings/update.collaborationMode`: changes the loaded thread's mode
  for subsequent turns without opening a turn.
- `collaborationMode/list`: returns the available collaboration mode presets.
  This endpoint is experimental.
- `thread/settings/updated`: notification carrying the effective
  `ThreadSettings`, including `collaborationMode`.

The wire shape is:

```json
{
  "collaborationMode": {
    "mode": "plan",
    "settings": {
      "model": "gpt-5.6",
      "reasoning_effort": null,
      "developer_instructions": null
    }
  }
}
```

`settings.developer_instructions: null` means "use Codex's built-in
instructions for the selected collaboration mode." It does not clear the mode
instructions.

`settings.model` is required by the protocol. Use the session's already
resolved SDK model. `reasoning_effort` is optional; do not casually copy the
ordinary turn effort into it without checking Codex's Plan-mode preset
semantics. Codex has a separate `plan_mode_reasoning_effort` configuration and
`collaborationMode/list` can advertise a preset effort.

To reproduce the schema inspection against an installed Codex binary:

```bash
codex app-server generate-json-schema \
  --experimental \
  --out /tmp/codex-app-server-schema
```

Then inspect:

```text
/tmp/codex-app-server-schema/v2/TurnStartParams.json
/tmp/codex-app-server-schema/v2/ThreadSettingsUpdateParams.json
/tmp/codex-app-server-schema/v2/ThreadSettingsUpdateResponse.json
/tmp/codex-app-server-schema/v2/CollaborationModeListResponse.json
```

Verify against the bundled runtime version used by TwiCC, not only an unrelated
global `codex` executable. The target version and download path live in
`src/twicc/providers/codex/runtime.py`; vendoring notes live in
`docs/codex-vendoring.md`.

## SDK situation: use the existing TwiCC override

The vendored SDK schema lags the binary in a narrow way:

- `src/openai_codex/generated/v2_all.py` already defines `ModeKind`,
  `Settings`, `CollaborationMode`, `CollaborationModeMask`, and
  `ThreadSettingsUpdatedNotification`.
- Its generated `TurnStartParams` does not yet declare `collaboration_mode`.
- It does not expose a high-level `thread/settings/update` method for
  collaboration mode.
- The low-level client already accepts raw JSON dictionaries and arbitrary RPC
  method strings.

Do not regenerate or hand-edit the vendored SDK for this feature.

`src/twicc/providers/codex/sdk_wrappers.py` is the compatibility layer intended
for exactly this situation. It already:

- calls raw RPCs missing from the generated SDK (`thread/goal/*`,
  `thread/inject_items`, and others);
- defines `_ThreadSettingsUpdateResponse`, the empty response envelope needed
  by `thread/settings/update`;
- implements `TwiccAsyncThread.update_settings_with_policy()` using
  `thread/settings/update` for the persistent next-turn sandbox.

Extend that existing method or refactor it into a slightly more general
next-turn settings helper. Do not introduce a parallel raw-RPC abstraction.

A representative payload is:

```python
await self._codex._client.request(
    "thread/settings/update",
    {
        "threadId": self.id,
        "collaborationMode": collaboration_mode.model_dump(
            by_alias=True,
            exclude_none=False,
            mode="json",
        ),
    },
    response_model=_ThreadSettingsUpdateResponse,
)
```

Important serialization detail: `developer_instructions=None` is meaningful
and must remain present as JSON `null`. A blanket `exclude_none=True` on the
collaboration mode subtree would remove it and change the protocol meaning.
The existing sandbox payload can continue to omit `None` values.

If an atomic mode-plus-turn operation is later needed,
`AsyncCodexClient.turn_start()` and the sync client's `_params_dict()` accept a
raw dictionary. The wrapper can therefore add `collaborationMode` to the
`turn/start` payload without changing generated `TurnStartParams`. This is not
required for the simplest `/plan` implementation because
`thread/settings/update` followed by a normal turn is sufficient while the
manager lock keeps command dispatch serialized and the agent is idle.

## Current TwiCC command path

The Codex slash-command implementation is intentionally owned by TwiCC:

1. `frontend/src/providers/codex/helpers.js`
   - `BUILTIN_COMMANDS` is the display-only autocomplete catalogue.
   - It currently contains `compact` and `goal`.
   - `getBuiltInCommands('/')` returns this list.
2. `src/twicc/providers/codex/agent/hardcoded_commands.py`
   - `KNOWN_COMMANDS` owns backend capture.
   - `parse_hardcoded_command()` returns the command name plus its trimmed
     trailing arguments.
3. `src/twicc/providers/codex/agent/manager.py`
   - Captures commands in both `send_to_session()` and `create_session()`.
   - A command can therefore target an existing session or be the first input
     of a brand-new draft.
   - `_dispatch_hardcoded_command()` cold-resumes an existing thread without
     opening a turn when necessary.
   - Commands are normally refused while a real assistant turn is active.
4. `src/twicc/providers/codex/agent/agent.py`
   - `start(..., command=...)` can run a first/cold command after the thread is
     loaded, without scheduling a normal turn.
   - `run_hardcoded_command()` dispatches to command-specific agent methods.

This existing path should be reused unchanged at the architectural level.

Some comments currently say that the Codex CLI has no slash-command
vocabulary. That was true when this code was introduced but is no longer true
of the current interactive Codex clients. Update those comments while touching
the command lists. The relevant architectural fact is now: TwiCC drives Codex
through the App Server rather than the interactive TUI, so TwiCC must implement
client-side slash-command behavior itself.

## Recommended first-increment behavior

### `/plan` with no arguments

Recommended minimum:

1. Capture the command on the backend.
2. Ensure the thread is loaded, using the existing live/cold/new command path.
3. Call `thread/settings/update` with Plan collaboration mode.
4. Do not open a model turn.
5. Acknowledge the command normally so the frontend clears its in-flight send
   state.

Whether a second bare `/plan` returns to Default is an open product decision.
For the smallest unambiguous increment, treating `/plan` as "enter Plan mode"
matches the detailed official documentation and avoids needing to recover the
current mode on a cold resume. If strict TUI toggle parity is required, first
add a reliable current-mode source as described below.

### `/plan <prompt>`

Recommended behavior:

1. Enter Plan collaboration mode.
2. Schedule a normal turn using only `<prompt>` as the user input.
3. Do not send the literal `/plan` prefix to the model.
4. Preserve image attachments if the command-entry plumbing is extended to
   carry them. The current `HardcodedCommand` contains only `name` and `args`,
   so image support is not automatic.

Be careful with the `command` startup path: `CodexAgent.start()` currently
returns immediately after `run_hardcoded_command(command)`. The `/plan`
handler must explicitly schedule the argument turn after updating the mode, or
the inline prompt will be lost.

### While Codex is working

Match the existing hardcoded-command gate and the official client behavior:
reject or disable `/plan` while a real turn is active. Do not steer `/plan`
text into the active turn.

The special `/goal` continuation exception in
`CodexAgentManager._dispatch_hardcoded_command()` should not automatically
apply to `/plan`; changing collaboration mode during a Codex-owned goal
continuation needs separate protocol validation.

## Frontend autocomplete

Add a display-only entry to `BUILTIN_COMMANDS` in
`frontend/src/providers/codex/helpers.js`, for example:

```javascript
{
    name: 'plan',
    plugin_name: null,
    is_builtin: true,
    is_global: true,
    description: 'Enter Plan mode before implementation',
    argument_hint: '[prompt]',
}
```

Use final UI wording consistent with the chosen enter-versus-toggle behavior.
The backend remains authoritative; adding only the frontend entry would make
`/plan` visible but still send it as ordinary model text.

No new Web Awesome component is needed for this first increment.

An active-mode badge, composer switch, Shift+Tab shortcut, or settings picker
would be a follow-up UI surface. Do not silently expand the first increment to
those features.

## Current-mode state and toggle parity

Entering Plan mode does not require a new `Session` column: the App Server can
hold the sticky mode for subsequent turns.

Reliable toggle behavior and a persistent UI indicator do require TwiCC to know
the effective mode. A process-local boolean alone is insufficient:

- a cold resume creates a new `CodexAgent`;
- the thread may already have been left in Plan mode by another client;
- a backend restart loses process-local state;
- the server emits effective settings through `thread/settings/updated`.

Possible follow-up approaches:

1. Consume `thread/settings/updated` and mirror collaboration mode into live
   process/session state.
2. Derive it from persisted Codex `turn_context.collaboration_mode` during
   compute, accepting that it reflects the latest started turn rather than a
   mode change that opened no turn.
3. Add a dedicated mutable `Session` field and synchronize it explicitly.

Do not overload `Session.permission_mode`. If a new provider-specific setting
is persisted, follow the repository's closed agent-settings bundle rules or
document why collaboration mode is mutable session state instead.

The first agent implementing this should ask the user to decide whether
`/plan` must toggle back to Default now, or whether enter-only behavior is
acceptable for the first increment.

## Model and effort construction

`CollaborationMode.settings.model` must use the SDK model id, not TwiCC's
compact alias. Resolve it through:

```python
get_provider_helpers(Provider.CODEX).resolve_sdk_model(
    self.agent_settings.selected_model,
)
```

The `AgentSettings` passed to a running agent should already be resolved to
concrete defaults, but retain a defensive fallback if the resolver returns
`None`; the wire object cannot omit `model`.

Do not assume the ordinary `AgentSettings.effort` is the correct Plan effort.
Before finalizing the payload:

- inspect `collaborationMode/list` on the bundled runtime;
- verify how the native TUI combines current model, the Plan preset mask, and
  `plan_mode_reasoning_effort`;
- either reproduce that behavior or deliberately leave
  `reasoning_effort: null` so the built-in mode/config determines it.

## Transcript and optimistic-message behavior

Hardcoded commands bypass a normal user turn. Existing commands have explicit
transcript handling where needed:

- `/compact` injects a durable user-message marker before compacting;
- `/goal` has its own goal-update/continuation behavior.

Decide deliberately whether bare `/plan` should produce a durable transcript
item. Native mode switches are settings operations, so no transcript item is
the simplest behavior. In that case, verify that the optimistic `/plan` bubble
is retired after the delivery acknowledgement and does not remain stuck.

For `/plan <prompt>`, the durable user message should be `<prompt>`, not the
literal command string, unless product design explicitly wants the command
shown. Ensure the optimistic item converges with the eventual Codex JSONL user
message rather than showing both.

## Suggested implementation sequence

1. Add focused tests for parsing:
   - `/plan`
   - surrounding whitespace
   - `/plan <prompt>`
   - `/planning` remains ordinary text
2. Extend `TwiccAsyncThread.update_settings_with_policy()` (or rename/generalize
   it carefully) to accept a `CollaborationMode`.
3. Add wrapper tests asserting the exact raw
   `thread/settings/update.collaborationMode` payload, including explicit
   `developer_instructions: null`.
4. Add `plan` to `KNOWN_COMMANDS`.
5. Add the agent dispatch and Plan-mode action.
6. Wire inline arguments to a subsequent normal turn if `/plan <prompt>` is in
   scope.
7. Add `plan` to the frontend `BUILTIN_COMMANDS`.
8. Add manager/agent tests for:
   - live idle thread;
   - cold existing thread;
   - brand-new draft whose first input is `/plan`;
   - busy-turn refusal;
   - inline prompt scheduling;
   - server RPC failure propagation and state recovery.
9. Verify manually against the bundled Codex runtime and inspect the resulting
   JSONL `turn_context.collaboration_mode.mode`.

Likely test locations:

- `tests/test_codex_sdk_wrappers.py`
- a new focused hardcoded-command test module, since current coverage is
  sparse;
- the existing manager/agent test patterns under `tests/test_codex_*.py`.

## Acceptance criteria for the first increment

- `/plan` appears in Codex `/` autocomplete.
- Sending `/plan` never reaches the model as literal user text.
- It works on a live idle session, a cold existing session, and as the first
  input of a new draft.
- The next real turn records
  `turn_context.collaboration_mode.mode == "plan"` in Codex JSONL.
- Plan mode remains independent from TwiCC `permission_mode`; changing one does
  not overwrite the other.
- `/plan` is refused while a real turn is active.
- App Server errors are surfaced to the user and do not leave the agent stuck
  in `STARTING` or `ASSISTANT_TURN`.
- The implementation uses `src/twicc/providers/codex/sdk_wrappers.py` and does
  not edit the vendored generated schema for this feature.
- Existing `/compact` and `/goal` behavior remains unchanged.

## Out of scope unless separately requested

- A Codex collaboration-mode field in the global/session settings picker.
- Starting every new Codex session in Plan mode from a preset.
- A composer badge or mode switch.
- A Shift+Tab shortcut.
- Persisting collaboration mode in the Django `Session` model.
- Mapping Codex collaboration mode onto Claude Code permission modes.
- Changing `update_plan` task rendering.
- Regenerating or re-vendoring the whole Codex Python SDK.

## Working-tree note

At the time this hand-off was written, the repository already contained these
unrelated user-owned changes:

```text
M  frontend/src/components/browser/BrowserPane.vue
?? docs/plans/2026-07-16-session-forking-design.md
?? docs/plans/2026-07-16-session-forking-implementation-plan.md
?? docs/plans/2026-07-16-session-forking-research.md
```

They are unrelated to Plan mode. Preserve them.
