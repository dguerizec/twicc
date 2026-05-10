"""
Metadata computation for session items.

Provides functions to compute display level and group membership
for session items. Used by both the background task (full session)
and the watcher (single item).
"""

from __future__ import annotations

import re

import orjson
import logging
from datetime import datetime
from typing import ClassVar, NamedTuple

import xmltodict
from django.db.models import Q

from twicc.core.enums import ItemKind, Provider
from twicc.core.models import Session, SessionItem
from twicc.pricing import calculate_line_context_usage
from twicc.providers.compute_base import (
    _EMPTY_ANALYSIS,
    _EMPTY_FILE_PATHS,
    _EMPTY_TASK_TOOL_USES,
    _EMPTY_TOOL_USE_ENTRIES,
    BaseSessionCompute,
    ContentAnalysis,
    ToolResultInfo,
    parse_timestamp_to_datetime,
    strip_markdown,
)
from .agent.original_file_cache import pop_original_file as _pop_cached_original_file
from .pricing import extract_model_info, to_token_usage


# Tool names that spawn subagent sessions (Task is the legacy name, Agent is the new one)
AGENT_TOOL_NAMES = frozenset({'Task', 'Agent'})

# Content types considered user-visible (for display_level and kind computation)
VISIBLE_CONTENT_TYPES = ('text', 'document', 'image')

# XML prefixes for system messages
# These are user messages that should be treated as debug-only
_SYSTEM_XML_PREFIXES = (
    '<local-command-',
    '<twicc-',
)

# Prefix for task notification XML (background agent results)
_TASK_NOTIFICATION_TAG = '<task-notification>'
_TASK_NOTIFICATION_CLOSE_TAG = '</task-notification>'

logger = logging.getLogger(__name__)


# =============================================================================
# Git Directory Resolution
# =============================================================================

# Tools whose input contains file paths for git resolution
_TOOL_PATH_FIELDS: dict[str, str] = {
    'Read': 'file_path',
    'Edit': 'file_path',
    'Write': 'file_path',
    'Grep': 'path',
    'Glob': 'path',
}


# =============================================================================
# Title Extraction from User Messages
# =============================================================================


def extract_text_from_content(content: str | list | None) -> str | None:
    """
    Extract text content from a user message content field.

    Args:
        content: Either a string or a list of content items

    Returns:
        The extracted text, or None if no text found
    """
    if not content:
        return None

    if isinstance(content, str):
        return content.strip()

    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict) and item.get('type') == 'text':
                text = item.get('text')
                if isinstance(text, str):
                    return text.strip()

    return None


class ParsedCommand(NamedTuple):
    name: str
    message: str | None
    args: str | None


def extract_command(text: str) -> ParsedCommand | None:
    if not text.startswith("<command-"):
        return None
    xml_text = f"<root>{text}</root>"
    try:
        parsed = xmltodict.parse(xml_text)
    except Exception:
        return None
    if not (name := (root := parsed["root"]).get("command-name")):
        return None
    return ParsedCommand(
        name=name,
        message=root.get("command-message"),
        args=root.get("command-args"),
    )


_RESULT_OPEN_TAG = '<result>'
_RESULT_CLOSE_TAG = '</result>'
_SUMMARY_OPEN_TAG = '<summary>'
_SUMMARY_CLOSE_TAG = '</summary>'
_RE_TASK_ID = re.compile(r'<task-id>([^<]+)</task-id>')
_RE_TOOL_USE_ID = re.compile(r'<tool-use-id>([^<]+)</tool-use-id>')


def _extract_task_notification_fields(xml_str: str) -> tuple[str | None, str | None, str]:
    """
    Manually extract task-notification fields when xmltodict fails.

    Uses regex for simple single-value tags (task-id, tool-use-id) and
    positional extraction for <result> (opening tag to last closing tag)
    since result content may contain unescaped XML-like text.

    Returns:
        (tool_use_id, task_id, result_text)
    """
    m_tool_use = _RE_TOOL_USE_ID.search(xml_str)
    tool_use_id = m_tool_use.group(1).strip() if m_tool_use else None

    m_task = _RE_TASK_ID.search(xml_str)
    task_id = m_task.group(1).strip() if m_task else None

    result_text = ''
    open_idx = xml_str.find(_RESULT_OPEN_TAG)
    if open_idx != -1:
        close_idx = xml_str.rfind(_RESULT_CLOSE_TAG)
        if close_idx != -1 and close_idx > open_idx:
            result_text = xml_str[open_idx + len(_RESULT_OPEN_TAG):close_idx]

    # Fallback to <summary> if no <result> content
    if not result_text:
        open_idx = xml_str.find(_SUMMARY_OPEN_TAG)
        if open_idx != -1:
            close_idx = xml_str.rfind(_SUMMARY_CLOSE_TAG)
            if close_idx != -1 and close_idx > open_idx:
                result_text = xml_str[open_idx + len(_SUMMARY_OPEN_TAG):close_idx]

    return tool_use_id, task_id, result_text


# Regex to strip ANSI escape codes from local command output
_ANSI_RE = re.compile(r'\x1b\[[0-9;]*m')

# Local command output tags (stdout and stderr)
_LOCAL_COMMAND_TAGS = (
    ('<local-command-stdout>', '</local-command-stdout>'),
    ('<local-command-stderr>', '</local-command-stderr>'),
)

# Prefixes/suffixes that indicate a local command output should be filtered out (not displayed)
_LOCAL_COMMAND_FILTERED_PREFIXES = ('compacted',)
_LOCAL_COMMAND_FILTERED_SUFFIXES = ('dismissed', 'cancelled', 'no content')


def _extract_local_command_text(text: str) -> str | None:
    """
    Extract the text content from a ``<local-command-stdout>`` or
    ``<local-command-stderr>`` tag.

    Uses rfind for the closing tag to avoid issues if the closing tag
    appears inside the content itself.

    Returns the inner text, or ``None`` if no tag is found.
    """
    stripped = text.lstrip()
    for open_tag, close_tag in _LOCAL_COMMAND_TAGS:
        start_idx = stripped.find(open_tag)
        if start_idx == -1:
            continue
        content_start = start_idx + len(open_tag)
        close_idx = stripped.rfind(close_tag)
        if close_idx == -1 or close_idx < content_start:
            continue
        return stripped[content_start:close_idx]
    return None


def get_message_content(parsed_json: dict) -> list | str | None:
    message = parsed_json.get('message', None)
    if not isinstance(message, dict):
        return None
    return message.get('content')


def get_message_content_list(parsed_json: dict, expected_type: str | None = None) -> list | None:
    """
    Extract the content array from a message of the expected type.
    """
    if expected_type is not None and parsed_json.get("type") != expected_type:
        return None
    content = get_message_content(parsed_json)
    if not isinstance(content, list):
        return None
    return content


def _is_system_xml_content(content: str | list | None) -> bool:
    """
    Check if content is a system XML message (command invocation or output).

    Matches user messages whose text starts with a system XML prefix
    (e.g., <local-command-stdout>, <twicc-cron-restart>).

    Handles both string content and list content with a single text entry.

    Args:
        content: Message content (string or list)

    Returns:
        True if the content is a system XML message
    """
    text = None
    if isinstance(content, str):
        text = content
    elif isinstance(content, list) and len(content) == 1:
        item = content[0]
        if isinstance(item, dict) and item.get('type') == 'text':
            text = item.get('text')
    if text is None:
        return False
    stripped = text.lstrip()
    return any(stripped.startswith(prefix) for prefix in _SYSTEM_XML_PREFIXES)


def _has_visible_content(content: str | list | None) -> bool:
    """
    Check if message content contains user-visible content.

    User-visible content types are: text, document, image.

    Args:
        content: Message content (string or list of content items)

    Returns:
        True if content is a string or contains at least one visible content item
    """
    if not content:
        return False

    if isinstance(content, str):
        return True

    if not isinstance(content, list):
        return False

    for item in content:
        if isinstance(item, dict) and item.get('type') in VISIBLE_CONTENT_TYPES:
            return True

    return False


# =============================================================================
# Live Sync — watcher entry point
# =============================================================================


# =============================================================================
# ClaudeCodeSessionCompute — concrete BaseSessionCompute for Claude Code
# =============================================================================


class ClaudeCodeSessionCompute(BaseSessionCompute):
    """
    Concrete compute pipeline for Claude Code sessions.

    The full :class:`BaseSessionCompute` surface — extraction, live
    machinery, batch (analyze_content + compute_session_metadata +
    apply_session_complete), and watcher live sync
    (sync_session_items_from_file) — is wired here. Each method
    delegates to a matching free function defined earlier in this file.
    The class is therefore stateless; :func:`get_compute` returns a
    per-process singleton.
    """

    provider: ClassVar[Provider] = Provider.CLAUDE_CODE

    # ------------------------------------------------------------------
    # Extraction
    # ------------------------------------------------------------------

    def transform_inline(self, parsed_json: dict) -> str | None:
        # Two Claude-Code-specific rewrites: background agent result deliveries
        # carried as XML <task-notification> user messages, and CLI local
        # command outputs wrapped in <local-command-stdout/stderr> tags. Both
        # are normalised in place into the regular tool_result / assistant
        # message formats so the rest of the pipeline doesn't need to care.

        entry_type = parsed_json.get('type')

        # --- task-notification XML (background agent results) ---
        if entry_type == 'user':
            message = parsed_json.get('message')
            if isinstance(message, dict):
                content = message.get('content')
                if isinstance(content, str):
                    stripped = content.lstrip()
                    if stripped.startswith(_TASK_NOTIFICATION_TAG):
                        # Find the LAST closing tag in case </task-notification>
                        # appears inside <result>.
                        close_idx = content.rfind(_TASK_NOTIFICATION_CLOSE_TAG)
                        if close_idx != -1:
                            xml_str = content[:close_idx + len(_TASK_NOTIFICATION_CLOSE_TAG)]
                            try:
                                notification = xmltodict.parse(xml_str)['task-notification']
                                tool_use_id = notification.get('tool-use-id')
                                task_id = notification.get('task-id')
                                result_text = (
                                    notification.get('result', '')
                                    or notification.get('summary', '')
                                )
                            except Exception:
                                # xmltodict can fail when <result> contains unescaped
                                # XML-like text (e.g. "<width>x<height>"). Fall back
                                # to manual extraction.
                                logger.info(
                                    "xmltodict failed for task-notification, "
                                    "falling back to manual extraction"
                                )
                                tool_use_id, task_id, result_text = (
                                    _extract_task_notification_fields(xml_str)
                                )

                            if tool_use_id:
                                # Preserve original content for debugging.
                                parsed_json['twiccOriginalContent'] = content
                                # Rewrite as a standard tool_result content array.
                                message['content'] = [{
                                    'type': 'tool_result',
                                    'tool_use_id': tool_use_id,
                                    'content': result_text,
                                }]
                                # Surface the agent_id so get_tool_result_agent_info-equivalent
                                # logic in the base picks it up.
                                if task_id:
                                    parsed_json['toolUseResult'] = {'agentId': task_id}
                                return orjson.dumps(parsed_json).decode('utf-8')

        # --- local-command-stdout/stderr -> synthetic assistant_message ---
        raw_text: str | None = None
        if entry_type == 'system' and parsed_json.get('subtype') == 'local_command':
            content = parsed_json.get('content', '')
            if isinstance(content, str):
                raw_text = _extract_local_command_text(content)
        elif entry_type == 'user':
            message = parsed_json.get('message')
            if isinstance(message, dict):
                content = message.get('content')
                if isinstance(content, str):
                    raw_text = _extract_local_command_text(content)
                elif isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get('type') == 'text':
                            extracted = _extract_local_command_text(block.get('text', ''))
                            if extracted is not None:
                                raw_text = extracted
                                break

        if raw_text is None:
            return None

        # Strip ANSI escape codes and whitespace.
        text = _ANSI_RE.sub('', raw_text).strip()

        # Filter out empty or non-interesting messages.
        if not text:
            return None
        text_lower = text.lower()
        if any(
            text_lower.startswith(prefix) or text_lower.startswith("(" + prefix)
            for prefix in _LOCAL_COMMAND_FILTERED_PREFIXES
        ):
            return None
        if any(
            text_lower.endswith(suffix) or text_lower.endswith(suffix + ")")
            for suffix in _LOCAL_COMMAND_FILTERED_SUFFIXES
        ):
            return None

        # Preserve original content for debugging.
        if entry_type == 'system':
            parsed_json['twiccOriginalContent'] = parsed_json.get('content')
        else:
            parsed_json['twiccOriginalContent'] = parsed_json.get('message', {}).get('content')

        # Rewrite as a standard assistant message.
        parsed_json['type'] = 'assistant'
        parsed_json.pop('subtype', None)
        parsed_json['message'] = {
            'role': 'assistant',
            'content': [{'type': 'text', 'text': text}],
        }

        return orjson.dumps(parsed_json).decode('utf-8')

    def compute_item_kind(self, parsed_json: dict) -> ItemKind | None:
        # NOTE: any change to this classification MUST bump
        # CLAUDE_CODE_COMPUTE_VERSION so existing sessions are recomputed.

        # "Bastard" API error format: type="assistant" but isApiErrorMessage=true.
        # The error text is serialised in content[0].text as a raw string.
        if parsed_json.get('isApiErrorMessage'):
            return ItemKind.API_ERROR

        entry_type = parsed_json.get('type')

        # Top-level system-ish lines that the CLI sprinkles in (queue ops,
        # progress events, summaries, file snapshots, custom-title, etc.).
        if entry_type in (
            'queue-operation', 'progress', 'summary', 'file-history-snapshot',
            'last-prompt', 'attachment', 'permission-mode', 'custom-title',
        ):
            return ItemKind.SYSTEM

        if entry_type == 'system':
            if parsed_json.get('subtype') == 'api_error':
                return ItemKind.API_ERROR
            return ItemKind.SYSTEM

        if entry_type == 'user':
            # Compact summary: user message with isCompactSummary flag (context compaction).
            if parsed_json.get('isCompactSummary'):
                return ItemKind.COMPACT_SUMMARY

            content = get_message_content(parsed_json)
            text = extract_text_from_content(content)

            # Slash commands surface as user messages, except /clear which is system.
            if text is not None and (command := extract_command(text)):
                if command.name == '/clear':
                    return ItemKind.SYSTEM
                return ItemKind.USER_MESSAGE

            # Meta messages aren't user messages.
            if parsed_json.get('isMeta'):
                return ItemKind.SYSTEM

            # System XML messages (commands, outputs) are SYSTEM.
            if _is_system_xml_content(content):
                return ItemKind.SYSTEM

            # Tool results bundled with text (e.g. "Tool loaded.") are CONTENT_ITEMS.
            if isinstance(content, list) and any(
                isinstance(item, dict) and item.get('type') == 'tool_result'
                for item in content
            ):
                return ItemKind.CONTENT_ITEMS

            # Only user messages with visible content count as USER_MESSAGE.
            if text or _has_visible_content(content):
                return ItemKind.USER_MESSAGE

            # Content array without visible items -> CONTENT_ITEMS.
            if isinstance(content, list):
                return ItemKind.CONTENT_ITEMS

            return None

        if entry_type == 'assistant':
            content = get_message_content(parsed_json)

            # "No response requested." is a system-level message, not a real
            # assistant response.
            if (
                isinstance(content, list)
                and len(content) == 1
                and isinstance(content[0], dict)
                and content[0].get('type') == 'text'
                and content[0].get('text') == 'No response requested.'
            ):
                return ItemKind.SYSTEM

            if _has_visible_content(content):
                return ItemKind.ASSISTANT_MESSAGE

            if isinstance(content, list):
                return ItemKind.CONTENT_ITEMS

            return None

        return None

    # compute_item_display_level + compute_item_metadata: inherited from base
    # (base implementation calls self.is_tool_result_item / self.compute_item_kind).

    def extract_item_timestamp(self, parsed_json: dict) -> datetime | None:
        timestamp_str = parsed_json.get("timestamp")
        if timestamp_str:
            return parse_timestamp_to_datetime(timestamp_str)
        return None

    # extract_title_from_user_message: inherited from base
    # (base assembles raw text + format_command_for_title + truncation).

    def format_command_for_title(self, text: str) -> str | None:
        # Claude Code embeds slash commands as <command-name>/<command-args>
        # XML in the user message text; format them as "name [args]".
        command = extract_command(text)
        if command is None:
            return None
        formatted = command.name
        if command.args:
            formatted += f' {strip_markdown(command.args)}'
        return formatted

    def extract_runtime_fields(self, parsed_json: dict) -> dict:
        # Claude Code carries cwd / gitBranch at the JSONL root, model
        # inside message.model, and slug at the JSONL root.
        fields: dict = {
            'cwd': None,
            'cwd_git_branch': None,
            'model': None,
            'slug': None,
        }
        if cwd := parsed_json.get('cwd'):
            fields['cwd'] = cwd
        if branch := parsed_json.get('gitBranch'):
            fields['cwd_git_branch'] = branch
        if (message := parsed_json.get('message')) and isinstance(message, dict):
            if model := message.get('model'):
                fields['model'] = model
        if slug := parsed_json.get('slug'):
            fields['slug'] = slug
        return fields

    def compute_item_cost_and_usage(
        self,
        item: SessionItem,
        parsed_json: dict,
        seen_message_ids: set[str],
        current_model: str | None,  # noqa: ARG002 (model lives on the line itself)
    ) -> None:
        message = parsed_json.get("message", {})
        if not isinstance(message, dict):
            return
        usage = message.get("usage")
        if not usage:
            return

        # Extract and store message_id for deduplication tracking
        msg_id = message.get("id")
        if msg_id:
            item.message_id = msg_id

        # Context usage: always computed when usage data is present
        token_usage = to_token_usage(usage)
        item.context_usage = calculate_line_context_usage(token_usage)

        # Cost: only computed if message_id not already seen (deduplication;
        # Claude Code writes multiple JSONL lines per API call when streaming).
        if msg_id and msg_id not in seen_message_ids:
            seen_message_ids.add(msg_id)
            model_info = extract_model_info(message.get("model", ""))
            if model_info:
                model_id = f"anthropic/claude-{model_info.family}-{model_info.version}"
                if (timestamp_str := parsed_json.get("timestamp")) and (
                    dt := parse_timestamp_to_datetime(timestamp_str)
                ):
                    from twicc.providers.helpers import get_provider_helpers
                    item.cost = get_provider_helpers(Provider.CLAUDE_CODE).calculate_line_cost(
                        token_usage, model_id, dt.date(),
                    )

    def is_tool_result_item(self, parsed_json: dict) -> bool:
        content = get_message_content_list(parsed_json, "user")
        if content is None:
            return False
        return any(
            isinstance(item, dict) and item.get('type') == 'tool_result'
            for item in content
        )

    def extract_tool_use_entries(
        self,
        parsed_json: dict,
        *,
        session_id: str,  # noqa: ARG002 (kept for signature compatibility)
    ) -> dict[str, str]:
        content = get_message_content_list(parsed_json, "assistant")
        if content is None:
            return {}
        return {
            item['id']: item.get('name', '')
            for item in content
            if isinstance(item, dict) and item.get('type') == 'tool_use' and item.get('id')
        }

    def extract_tool_result_info(
        self,
        parsed_json: dict,
        *,
        session_id: str,  # noqa: ARG002 (kept for signature compatibility)
        tool_use_map: dict | None = None,  # noqa: ARG002
    ) -> ToolResultInfo | None:
        content = get_message_content_list(parsed_json, "user")
        if content is None:
            return None
        # Find the first tool_result entry (may be bundled with text blocks).
        tool_result = next(
            (item for item in content if isinstance(item, dict) and item.get('type') == 'tool_result'),
            None,
        )
        if tool_result is None:
            return None
        tool_use_id = tool_result.get('tool_use_id')
        if not tool_use_id:
            return None

        error_text: str | None = None
        if tool_result.get('is_error'):
            error_content = tool_result.get('content', '')
            if isinstance(error_content, str):
                stripped = error_content.strip()
                if stripped.startswith('<tool_use_error>') and stripped.endswith('</tool_use_error>'):
                    error_text = (
                        stripped[len('<tool_use_error>'):-len('</tool_use_error>')].strip()
                        or 'Unknown error'
                    )
                elif stripped.startswith('Exit code '):
                    error_text = stripped.split('\n', 1)[0]
                else:
                    error_text = stripped or 'Unknown error'
            else:
                error_text = 'Unknown error'

        return ToolResultInfo(
            tool_use_id=tool_use_id,
            is_error=error_text is not None,
            error_text=error_text,
        )

    def extract_agent_info_from_tool_result(
        self, parsed_json: dict
    ) -> tuple[str, str] | None:
        # Need both a tool_result block in the content (for tool_use_id) and
        # an agentId in the root-level toolUseResult.
        content = get_message_content_list(parsed_json, "user")
        if content is None:
            return None
        tool_result = next(
            (item for item in content if isinstance(item, dict) and item.get('type') == 'tool_result'),
            None,
        )
        if tool_result is None:
            return None
        tool_use_id = tool_result.get('tool_use_id')
        if not tool_use_id:
            return None
        tool_use_result = parsed_json.get('toolUseResult')
        if not isinstance(tool_use_result, dict):
            return None
        agent_id = tool_use_result.get('agentId')
        if not agent_id:
            return None
        return tool_use_id, agent_id

    def extract_task_tool_uses(self, parsed_json: dict) -> list[tuple[str, bool]]:
        content = get_message_content_list(parsed_json, "assistant")
        if content is None:
            return []
        results: list[tuple[str, bool]] = []
        for item in content:
            if (
                isinstance(item, dict)
                and item.get('type') == 'tool_use'
                and item.get('name') in AGENT_TOOL_NAMES
                and item.get('id')
            ):
                inputs = item.get('input')
                is_background = bool(isinstance(inputs, dict) and inputs.get('run_in_background'))
                results.append((item['id'], is_background))
        return results

    def extract_task_tool_use_prompts(
        self, parsed_json: dict
    ) -> list[tuple[str, str, bool]]:
        content = get_message_content_list(parsed_json, "assistant")
        if content is None:
            return []
        results: list[tuple[str, str, bool]] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            if item.get('type') != 'tool_use' or item.get('name') not in AGENT_TOOL_NAMES:
                continue
            tu_id = item.get('id')
            inputs = item.get('input', {})
            if isinstance(inputs, dict) and tu_id:
                prompt = inputs.get('prompt')
                if isinstance(prompt, str):
                    is_background = bool(inputs.get('run_in_background'))
                    results.append((tu_id, prompt, is_background))
        return results

    def extract_paths_from_tool_uses(self, parsed_json: dict) -> list[str]:
        # Only Read / Edit / Write / Grep / Glob tool_uses contribute paths
        # (their input field name varies — see _TOOL_PATH_FIELDS).
        content = get_message_content_list(parsed_json, "assistant")
        if content is None:
            return []
        paths: list[str] = []
        for item in content:
            if not isinstance(item, dict) or item.get('type') != 'tool_use':
                continue
            tool_name = item.get('name')
            if tool_name not in _TOOL_PATH_FIELDS:
                continue
            field_name = _TOOL_PATH_FIELDS[tool_name]
            inputs = item.get('input')
            if not isinstance(inputs, dict):
                continue
            path = inputs.get(field_name)
            if isinstance(path, str) and path.startswith('/'):
                paths.append(path)
        return paths

    def compute_link_extra(
        self, parsed_json: dict, tool_name: str
    ) -> str | None:
        """Return the JSON ``ToolResultLink.extra`` payload for this result.

        Claude Code only emits diff stats for ``Edit`` and ``Write``;
        every other tool returns ``None`` and the inherited machinery
        stores ``ToolResultLink.extra = NULL`` for that link. Source
        of truth is the JSONL ``toolUseResult`` block.

        Output JSON shape (``orjson.dumps`` of the dict):

        - ``Write`` create (empty ``structuredPatch``, full new file
          content carried under ``content``)::

              {"lines_added": <int>}
              # ``lines_removed`` omitted — there's nothing to remove.

        - ``Edit`` or ``Write`` update (non-empty ``structuredPatch``)::

              {
                  "lines_added":   <int>,    # always present
                  "lines_removed": <int>,    # always present
                  # ``hunks`` only when more than one hunk was applied.
                  "hunks":         <int>,    # optional
              }

        Counting rules: iterate ``structuredPatch[].lines`` and tally
        ``+`` / ``-`` prefixes; context lines (space prefix) and
        diff metadata lines are ignored.

        The frontend reads ``lines_added`` / ``lines_removed`` for the
        per-tool ``+N -M`` summary badge; ``hunks`` is informational
        and not consumed today.
        """
        if tool_name not in ('Edit', 'Write'):
            return None
        tool_use_result = parsed_json.get('toolUseResult')
        if not isinstance(tool_use_result, dict):
            return None

        structured_patch = tool_use_result.get('structuredPatch')

        # Write creates: structuredPatch is empty, count lines from content.
        if isinstance(structured_patch, list) and not structured_patch:
            content = tool_use_result.get('content')
            if isinstance(content, str):
                lines_added = content.count('\n') + 1 if content else 0
                return orjson.dumps({'lines_added': lines_added}).decode()
            return None

        # Edit and Write updates: count +/- from structuredPatch hunks.
        if not isinstance(structured_patch, list) or not structured_patch:
            return None

        lines_added = 0
        lines_removed = 0
        for hunk in structured_patch:
            if not isinstance(hunk, dict):
                continue
            for line in hunk.get('lines', ()):
                if isinstance(line, str):
                    if line.startswith('+'):
                        lines_added += 1
                    elif line.startswith('-'):
                        lines_removed += 1

        stats: dict = {'lines_added': lines_added, 'lines_removed': lines_removed}
        if len(structured_patch) > 1:
            stats['hunks'] = len(structured_patch)

        return orjson.dumps(stats).decode()

    def detect_prefix_suffix(
        self, parsed_json: dict, kind: ItemKind | None
    ) -> tuple[bool, bool]:
        # Prefix/suffix detection only matters for ALWAYS messages whose
        # content can mix visible blocks with thinking/tool_use blocks.
        if kind not in (ItemKind.USER_MESSAGE, ItemKind.ASSISTANT_MESSAGE):
            return False, False
        content = get_message_content_list(parsed_json)
        if not content:
            return False, False
        first = content[0]
        last = content[-1]
        has_prefix = isinstance(first, dict) and first.get('type') not in VISIBLE_CONTENT_TYPES
        has_suffix = isinstance(last, dict) and last.get('type') not in VISIBLE_CONTENT_TYPES
        return has_prefix, has_suffix

    # resolve_git_for_item: inherited from base
    # (base walks self.extract_paths_from_tool_uses through resolve_git_from_path).

    def extract_user_message_text(self, parsed_json: dict) -> str | None:
        return extract_text_from_content(get_message_content(parsed_json))

    def agent_tool_candidates_query(self, parent_session_id: str):
        # Pre-filter on the textual marker of an agent-spawning tool_use to
        # avoid scanning every item of the parent session.
        return SessionItem.objects.filter(
            Q(content__contains='"name":"Task"') | Q(content__contains='"name":"Agent"'),
            session_id=parent_session_id,
        ).order_by('-line_num')

    def is_session_start_marker(self, parsed_json: dict) -> bool:
        # Claude Code emits a `progress` line whose `data.hookEvent` is
        # `SessionStart` when the CLI re-attaches to a previously stored
        # session.
        if parsed_json.get('type') != 'progress':
            return False
        data = parsed_json.get('data')
        return isinstance(data, dict) and data.get('hookEvent') == 'SessionStart'

    def extract_custom_title(self, parsed_json: dict) -> tuple[str, str] | None:
        if parsed_json.get('type') != 'custom-title':
            return None
        custom_title = parsed_json.get('customTitle')
        if not isinstance(custom_title, str) or not custom_title:
            return None
        # When the line targets another session (the CLI dropped a custom
        # title entry into the wrong file), `sessionId` is set; otherwise
        # the directive applies to the current session — let the base
        # caller default to it.
        target = parsed_json.get('sessionId')
        return (target if isinstance(target, str) else '', custom_title)

    def transform_tool_result_with_cache(
        self, parsed_json: dict, session_id: str, line_num: int
    ) -> str | None:
        # Claude Code's PreToolUse hook captures file contents before
        # Edit/Write modifications; this splices them into the matching
        # tool_result so the front gets full-file diffs.
        tool_use_result = parsed_json.get('toolUseResult')
        if not isinstance(tool_use_result, dict):
            return None

        # Locate the tool_use_id from the first tool_result block.
        content = get_message_content_list(parsed_json, "user")
        tool_use_id = None
        if content is not None:
            for item in content:
                if isinstance(item, dict) and item.get('type') == 'tool_result':
                    tool_use_id = item.get('tool_use_id')
                    break
        if not tool_use_id:
            return None

        # Always pop from the cache (consume the entry whether we use it or not).
        cached = _pop_cached_original_file(session_id, tool_use_id)
        if cached is None:
            return None

        # Already has originalFile — no injection needed.
        if tool_use_result.get('originalFile') is not None:
            return None

        tool_use_result['originalFile'] = cached
        logger.debug(
            "Injected cached originalFile into tool_result (session=%s, line=%d, tool_use_id=%s, size=%d)",
            session_id, line_num, tool_use_id, len(cached),
        )
        return orjson.dumps(parsed_json).decode('utf-8')

    def extract_subagent_marker(self, parsed_json: dict) -> str | None:
        agent_id = parsed_json.get('agentId')
        return agent_id if isinstance(agent_id, str) and agent_id else None

    def apply_session_title(self, target_session_id: str, title: str) -> bool:
        # Claude Code title persistence is gated by anti-stale-write
        # protection: the CLI may re-append the previous title from its
        # tail-scan after we updated it, and we must refuse those.
        from .titles import check_protected_title, rename_session_in_jsonl

        result = check_protected_title(target_session_id, title)
        if result.should_apply:
            Session.objects.filter(id=target_session_id).update(title=title)
            return True
        if result.correction:
            # CLI wrote a stale title — re-write the correct one.
            # This places the correct title at the end of the JSONL,
            # so the CLI's next tail-scan will absorb it.
            try:
                rename_session_in_jsonl(target_session_id, result.correction)
            except Exception:
                pass  # Will retry on next stale entry
        return False

    # ------------------------------------------------------------------
    # Live (watcher) machinery
    # ------------------------------------------------------------------

    # find_open_group_head + compute_item_metadata_live: inherited from base
    # (base implementation calls self.detect_prefix_suffix / self.resolve_git_for_item).
    # create_tool_result_link_live + check_agent_naturally_stopped +
    # create_agent_link_from_{tool_result,subagent,tool_use}: inherited from base
    # (the base algorithms call provider hooks for the parsing-only bits).

    # ------------------------------------------------------------------
    # Batch compute
    # ------------------------------------------------------------------

    def analyze_content(
        self,
        parsed_json: dict,
        *,
        session_id: str,  # noqa: ARG002 (kept for signature compatibility)
        tool_use_map: dict,  # noqa: ARG002
    ) -> ContentAnalysis:
        message = parsed_json.get('message')
        if not isinstance(message, dict):
            return _EMPTY_ANALYSIS

        content = message.get('content')
        entry_type = parsed_json.get('type')

        # --- String content (user messages can have string content) ---
        if isinstance(content, str):
            if not content:
                # Empty string: not visible, no text, not XML
                return _EMPTY_ANALYSIS
            # Non-empty string
            stripped_for_xml = content.lstrip()
            is_system_xml = any(stripped_for_xml.startswith(prefix) for prefix in _SYSTEM_XML_PREFIXES)
            return ContentAnalysis(
                has_visible_content=True,
                text_content=content.strip(),
                is_system_xml=is_system_xml,
                has_tool_result=False,
                tool_result_id=None,
                tool_result_error=None,
                tool_use_entries=_EMPTY_TOOL_USE_ENTRIES,
                task_tool_uses=_EMPTY_TASK_TOOL_USES,
                file_paths=_EMPTY_FILE_PATHS,
                has_prefix=False,
                has_suffix=False,
                tool_result_agent_info=None,
            )

        # --- Not a list or empty list -> nothing to traverse ---
        if not isinstance(content, list) or not content:
            return _EMPTY_ANALYSIS

        # --- List content: single traversal ---

        # Prefix/suffix: check first and last items
        first_item = content[0]
        last_item = content[-1]
        has_prefix = isinstance(first_item, dict) and first_item.get('type') not in VISIBLE_CONTENT_TYPES
        has_suffix = isinstance(last_item, dict) and last_item.get('type') not in VISIBLE_CONTENT_TYPES

        # Common accumulators
        has_visible = False
        text_content: str | None = None

        if entry_type == 'assistant':
            # --- Assistant message: tool_use info + visibility ---
            tool_use_entries: dict[str, str] = {}
            task_tool_uses: list[tuple[str, bool]] = []
            file_paths: list[str] = []

            for item in content:
                if not isinstance(item, dict):
                    continue
                item_type = item.get('type')

                if item_type in VISIBLE_CONTENT_TYPES:
                    has_visible = True
                    # Extract text from first text block
                    if item_type == 'text' and text_content is None:
                        text_val = item.get('text')
                        if isinstance(text_val, str):
                            text_content = text_val.strip()

                elif item_type == 'tool_use':
                    tu_id = item.get('id')
                    tu_name = item.get('name', '')
                    if tu_id:
                        tool_use_entries[tu_id] = tu_name

                        # Task/Agent tool_uses
                        if tu_name in AGENT_TOOL_NAMES:
                            is_bg = bool(isinstance(item.get('input'), dict) and item['input'].get('run_in_background'))
                            task_tool_uses.append((tu_id, is_bg))

                        # File path extraction for git resolution
                        if tu_name in _TOOL_PATH_FIELDS:
                            field_name = _TOOL_PATH_FIELDS[tu_name]
                            inputs = item.get('input')
                            if isinstance(inputs, dict):
                                path = inputs.get(field_name)
                                if isinstance(path, str) and path.startswith('/'):
                                    file_paths.append(path)

            return ContentAnalysis(
                has_visible_content=has_visible,
                text_content=text_content,
                is_system_xml=False,
                has_tool_result=False,
                tool_result_id=None,
                tool_result_error=None,
                tool_use_entries=tool_use_entries or _EMPTY_TOOL_USE_ENTRIES,
                task_tool_uses=task_tool_uses or _EMPTY_TASK_TOOL_USES,
                file_paths=file_paths or _EMPTY_FILE_PATHS,
                has_prefix=has_prefix,
                has_suffix=has_suffix,
                tool_result_agent_info=None,
            )

        if entry_type == 'user':
            # --- User message: tool_result info + visibility + text ---
            # Check for system XML in list content (single text entry starting with a system prefix)
            is_system_xml = False
            if len(content) == 1:
                only_item = content[0]
                if isinstance(only_item, dict) and only_item.get('type') == 'text':
                    text_val = only_item.get('text')
                    if isinstance(text_val, str):
                        stripped_xml = text_val.lstrip()
                        is_system_xml = any(stripped_xml.startswith(prefix) for prefix in _SYSTEM_XML_PREFIXES)

            has_tool_result = False
            first_tool_result_id: str | None = None
            # Sentinel: ... means "first tool_result not found yet"
            first_tool_result_error: str | None | type(...) = ...

            for item in content:
                if not isinstance(item, dict):
                    continue
                item_type = item.get('type')

                if item_type in VISIBLE_CONTENT_TYPES:
                    has_visible = True
                    # Extract text from first text block
                    if item_type == 'text' and text_content is None:
                        text_val = item.get('text')
                        if isinstance(text_val, str):
                            text_content = text_val.strip()

                elif item_type == 'tool_result':
                    if not has_tool_result:
                        # First tool_result: extract id and error
                        has_tool_result = True
                        first_tool_result_id = item.get('tool_use_id')

                        if not item.get('is_error'):
                            first_tool_result_error = None
                        else:
                            error_content = item.get('content', '')
                            if isinstance(error_content, str):
                                stripped = error_content.strip()
                                if stripped.startswith('<tool_use_error>') and stripped.endswith('</tool_use_error>'):
                                    first_tool_result_error = stripped[len('<tool_use_error>'):-len('</tool_use_error>')].strip() or 'Unknown error'
                                elif stripped.startswith('Exit code '):
                                    first_tool_result_error = stripped.split('\n', 1)[0]
                                else:
                                    first_tool_result_error = stripped or 'Unknown error'
                            else:
                                first_tool_result_error = 'Unknown error'

            # Resolve error sentinel
            tool_result_error = None if first_tool_result_error is ... else first_tool_result_error

            # Agent info: requires both tool_result_id and root-level toolUseResult.agentId
            agent_info = None
            if first_tool_result_id:
                tool_use_result = parsed_json.get('toolUseResult')
                if isinstance(tool_use_result, dict):
                    agent_id = tool_use_result.get('agentId')
                    if agent_id:
                        agent_info = (first_tool_result_id, agent_id)

            return ContentAnalysis(
                has_visible_content=has_visible,
                text_content=text_content,
                is_system_xml=is_system_xml,
                has_tool_result=has_tool_result,
                tool_result_id=first_tool_result_id,
                tool_result_error=tool_result_error,
                tool_use_entries=_EMPTY_TOOL_USE_ENTRIES,
                task_tool_uses=_EMPTY_TASK_TOOL_USES,
                file_paths=_EMPTY_FILE_PATHS,
                has_prefix=has_prefix,
                has_suffix=has_suffix,
                tool_result_agent_info=agent_info,
            )

        # --- Other message types: just visibility + text + prefix/suffix ---
        for item in content:
            if not isinstance(item, dict):
                continue
            item_type = item.get('type')
            if item_type in VISIBLE_CONTENT_TYPES:
                has_visible = True
                if item_type == 'text' and text_content is None:
                    text_val = item.get('text')
                    if isinstance(text_val, str):
                        text_content = text_val.strip()

        return ContentAnalysis(
            has_visible_content=has_visible,
            text_content=text_content,
            is_system_xml=False,
            has_tool_result=False,
            tool_result_id=None,
            tool_result_error=None,
            tool_use_entries=_EMPTY_TOOL_USE_ENTRIES,
            task_tool_uses=_EMPTY_TASK_TOOL_USES,
            file_paths=_EMPTY_FILE_PATHS,
            has_prefix=has_prefix,
            has_suffix=has_suffix,
            tool_result_agent_info=None,
        )


    # compute_session_metadata + apply_session_complete: inherited from base
    # (the base orchestrates DB I/O and dispatches parsing through hooks).

    # ------------------------------------------------------------------
    # Watcher live sync
    # ------------------------------------------------------------------

    # sync_session_items_from_file: inherited from base
    # (the base orchestrates the file read, item creation, link wiring and
    # session-level updates; everything provider-specific is dispatched
    # through hooks declared above).


# =============================================================================
# Singleton accessor
# =============================================================================


_compute_instance: ClaudeCodeSessionCompute | None = None


def get_compute() -> ClaudeCodeSessionCompute:
    """
    Return the process-local :class:`ClaudeCodeSessionCompute` singleton.

    The class is stateless (its methods all delegate to module-level
    helpers); the singleton just avoids re-instantiating it on every
    call site. Each multiprocessing worker gets its own instance because
    module globals are not shared across processes — that's exactly the
    behaviour we want for the batch worker.
    """
    global _compute_instance
    if _compute_instance is None:
        _compute_instance = ClaudeCodeSessionCompute()
    return _compute_instance
