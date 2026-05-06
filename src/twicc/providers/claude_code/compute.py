"""
Metadata computation for session items.

Provides functions to compute display level and group membership
for session items. Used by both the background task (full session)
and the watcher (single item).
"""

from __future__ import annotations

import os
import re

import orjson
import logging
from collections import Counter
from datetime import datetime
from typing import NamedTuple

import xmltodict
from django.core.exceptions import MultipleObjectsReturned
from django.db.models import Q

from twicc.core.enums import ItemDisplayLevel, ItemKind, Provider
from twicc.core.models import AgentLink, Session, SessionItem, SessionType, ToolResultLink
from twicc.git import resolve_git_from_path
from twicc.pricing import calculate_line_context_usage
from twicc.providers.compute_base import (
    AGENTS_LINKS_DONE_CACHE,
    AGENTS_PROMPT_CACHE,
    AgentLinkUpdate,
    AgentStoppedUpdate,
    GroupState,
    ItemGroupInfo,
    ToolResultUpdate,
    cache_agent_prompt,
    get_cached_agent_prompt,
    is_agent_link_done,
    mark_agent_link_done,
    parse_timestamp_to_datetime,
    strip_markdown,
    uncache_agent_prompt,
)
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

# Maximum length for extracted titles (before truncation)
TITLE_MAX_LENGTH = 200

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


def extract_paths_from_tool_uses(parsed_json: dict) -> list[str]:
    """
    Extract file/directory paths from tool_use blocks in an assistant message.

    Only considers Read, Edit, Write, Grep, and Glob tools.
    Only returns absolute paths (starting with /).

    Args:
        parsed_json: Parsed JSON content of an assistant message

    Returns:
        List of absolute paths found in tool_use inputs
    """
    content = get_message_content_list(parsed_json, "assistant")
    if content is None:
        return []

    paths = []
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


def resolve_git_for_item(parsed_json: dict, *, use_cache: bool = True) -> tuple[str, str] | None:
    """
    Resolve git directory and branch for a session item.

    Extracts paths from tool_use blocks, resolves each to a git root,
    and returns the most common resolution.

    Args:
        parsed_json: Parsed JSON content of the item
        use_cache: Whether to use the module-level git resolution cache.
                   Set to False for live resolution where fresh results are needed.
                   Passed through to resolve_git_from_path.

    Returns:
        (git_directory, git_branch) tuple, or None if no paths or no git found
    """
    paths = extract_paths_from_tool_uses(parsed_json)
    if not paths:
        return None

    resolutions: list[tuple[str, str]] = []
    for path in paths:
        # Use the directory part of the path (for files)
        dir_path = os.path.dirname(path) if not os.path.isdir(path) else path
        result = resolve_git_from_path(dir_path, use_cache=use_cache)
        if result is not None:
            resolutions.append(result)

    if not resolutions:
        return None

    if len(resolutions) == 1:
        return resolutions[0]

    # Multiple resolutions: use the most frequent git_directory
    counter = Counter(r[0] for r in resolutions)
    most_common_dir = counter.most_common(1)[0][0]
    # Return the first resolution matching the most common directory
    for r in resolutions:
        if r[0] == most_common_dir:
            return r

    return resolutions[0]  # Fallback (shouldn't reach here)


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


def transform_task_notification(parsed_json: dict) -> str | None:
    """
    Transform a task-notification user message into a synthetic tool_result format.

    Background agents deliver their results as user messages with XML content
    like ``<task-notification>...<tool-use-id>...</tool-use-id>...</task-notification>``
    instead of the normal tool_result content array format.

    This function detects such messages, parses the XML, and rewrites
    ``parsed_json`` **in place** so that downstream code sees a standard
    tool_result item (content list, toolUseResult with agentId, etc.).

    Args:
        parsed_json: The parsed JSONL line (mutated in place if transformed).

    Returns:
        The new serialised JSON string to store in DB if a transformation was
        performed, or ``None`` if the item was not a task-notification.
    """
    if parsed_json.get('type') != 'user':
        return None
    message = parsed_json.get('message')
    if not isinstance(message, dict):
        return None
    content = message.get('content')
    if not isinstance(content, str):
        return None
    stripped = content.lstrip()
    if not stripped.startswith(_TASK_NOTIFICATION_TAG):
        return None

    # Find the LAST closing tag to avoid issues if </task-notification> appears inside <result>
    close_idx = content.rfind(_TASK_NOTIFICATION_CLOSE_TAG)
    if close_idx == -1:
        return None
    xml_str = content[:close_idx + len(_TASK_NOTIFICATION_CLOSE_TAG)]

    try:
        notification = xmltodict.parse(xml_str)['task-notification']
        tool_use_id = notification.get('tool-use-id')
        task_id = notification.get('task-id')
        result_text = notification.get('result', '') or notification.get('summary', '')
    except Exception:
        # Fallback: xmltodict can fail when <result> contains unescaped XML-like text
        # (e.g. "<width>x<height>"). Extract fields manually.
        logger.info("xmltodict failed for task-notification, falling back to manual extraction")
        tool_use_id, task_id, result_text = _extract_task_notification_fields(xml_str)

    if not tool_use_id:
        return None

    # Preserve original content for debugging
    parsed_json['twiccOriginalContent'] = content

    # Rewrite message.content as a standard tool_result content array
    message['content'] = [{
        'type': 'tool_result',
        'tool_use_id': tool_use_id,
        'content': result_text,
    }]

    # Add toolUseResult with agentId so that get_tool_result_agent_info() works
    if task_id:
        parsed_json['toolUseResult'] = {'agentId': task_id}

    # Serialise and return the new content for DB storage
    return orjson.dumps(parsed_json).decode('utf-8')


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


def transform_local_command_output(parsed_json: dict) -> str | None:
    """
    Transform a local-command-stdout/stderr message into a synthetic assistant_message.

    Local command outputs appear in two formats in JSONL:
    1. ``type: "system", subtype: "local_command"`` with content containing
       ``<local-command-stdout>...</local-command-stdout>`` (or stderr variant)
    2. ``type: "user"`` with message.content (string or text block) containing
       ``<local-command-stdout>...</local-command-stdout>`` (or stderr variant)

    This function detects such messages, extracts the text from the XML tag,
    strips ANSI escape codes, and rewrites ``parsed_json`` **in place** so that
    downstream code sees a standard assistant_message item.

    Messages whose content is empty, starts with "compacted", or ends with
    "dismissed" or "cancelled" are filtered out (returns ``None``).

    Args:
        parsed_json: The parsed JSONL line (mutated in place if transformed).

    Returns:
        The new serialised JSON string to store in DB if a transformation was
        performed, or ``None`` if the item was not a local-command-stdout/stderr
        or was filtered out.
    """
    entry_type = parsed_json.get('type')
    raw_text = None

    # Format 1: type=system, subtype=local_command
    if entry_type == 'system' and parsed_json.get('subtype') == 'local_command':
        content = parsed_json.get('content', '')
        if isinstance(content, str):
            raw_text = _extract_local_command_text(content)

    # Format 2: type=user, message.content contains the tag
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

    # Strip ANSI escape codes and whitespace
    text = _ANSI_RE.sub('', raw_text).strip()

    # Filter out empty or non-interesting messages
    if not text:
        return None
    text_lower = text.lower()
    if any(text_lower.startswith(prefix) or text_lower.startswith("(" + prefix) for prefix in _LOCAL_COMMAND_FILTERED_PREFIXES):
        return None
    if any(text_lower.endswith(suffix) or text_lower.endswith(suffix + ")") for suffix in _LOCAL_COMMAND_FILTERED_SUFFIXES):
        return None

    # Preserve original content for debugging
    if entry_type == 'system':
        parsed_json['twiccOriginalContent'] = parsed_json.get('content')
    else:
        parsed_json['twiccOriginalContent'] = parsed_json.get('message', {}).get('content')

    # Rewrite as a standard assistant message
    parsed_json['type'] = 'assistant'
    parsed_json.pop('subtype', None)
    parsed_json['message'] = {
        'role': 'assistant',
        'content': [{'type': 'text', 'text': text}],
    }

    # Serialise and return the new content for DB storage
    return orjson.dumps(parsed_json).decode('utf-8')


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


def extract_title_from_user_message(parsed_json: dict) -> str | None:
    """
    Extract a title from a user message JSON.

    Extracts text content, strips markdown and whitespace,
    truncates to TITLE_MAX_LENGTH characters, and adds ellipsis if truncated.

    Args:
        parsed_json: Parsed JSON content of a user message item

    Returns:
        Cleaned title string, or None if no text content found
    """
    if (content := get_message_content(parsed_json)) is None:
        return None

    text = extract_text_from_content(content)
    if not text:
        return None

    if (command := extract_command(text)) is not None:
        # Use command name as title for command invocations
        cleaned = command.name
        if command.args:
            cleaned += f' {strip_markdown(command.args)}'

    else:
        # Strip markdown and whitespace
        cleaned = strip_markdown(text).strip()

    # Collapse multiple whitespace into single space
    cleaned = re.sub(r'\s+', ' ', cleaned)

    if not cleaned:
        return None

    # Truncate if needed
    if len(cleaned) > TITLE_MAX_LENGTH:
        return cleaned[:TITLE_MAX_LENGTH] + '…'

    return cleaned


# =============================================================================
# Helper Functions for Prefix/Suffix Detection
# =============================================================================


def _has_collapsible_prefix(content: list) -> bool:
    """Check if content array starts with a collapsible element."""
    if not content:
        return False
    first = content[0]
    return isinstance(first, dict) and first.get('type') not in VISIBLE_CONTENT_TYPES


def _has_collapsible_suffix(content: list) -> bool:
    """Check if content array ends with a collapsible element."""
    if not content:
        return False
    last = content[-1]
    return isinstance(last, dict) and last.get('type') not in VISIBLE_CONTENT_TYPES


def _detect_prefix_suffix(parsed_json: dict, kind: ItemKind | None) -> tuple[bool, bool]:
    """
    Detect if an ALWAYS item has collapsible prefix/suffix.

    Returns:
        (has_prefix, has_suffix) tuple
    """
    if kind not in (ItemKind.USER_MESSAGE, ItemKind.ASSISTANT_MESSAGE):
        return False, False

    content = get_message_content_list(parsed_json)
    if not content:
        return False, False

    return _has_collapsible_prefix(content), _has_collapsible_suffix(content)


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


def get_tool_use_entries(parsed_json: dict) -> dict[str, str]:
    """
    Extract tool_use ID → name mapping from an assistant or content_items message.

    Returns a dict mapping tool_use_id to tool name (e.g. {"toolu_xxx": "Bash"}).
    """
    content = get_message_content_list(parsed_json, "assistant")
    if content is None:
        return {}
    return {
        item['id']: item.get('name', '')
        for item in content
        if isinstance(item, dict) and item.get('type') == 'tool_use' and item.get('id')
    }


def get_tool_result_id(parsed_json: dict) -> str | None:
    """
    Extract the tool_use_id from a tool_result item.

    Finds the first tool_result entry in the content array (may be bundled
    with other items like text).

    Returns the tool_use_id string, or None if no tool_result found.
    """
    content = get_message_content_list(parsed_json, "user")
    if content is None:
        return None
    for item in content:
        if isinstance(item, dict) and item.get('type') == 'tool_result':
            return item.get('tool_use_id')
    return None


def get_tool_result_error(parsed_json: dict) -> str | None:
    """
    Extract the error message from a tool_result item.

    Looks at the first ``tool_result`` entry in the content array.
    When ``is_error`` is true, returns the error text from the ``content``
    field, handling three formats:

    - ``<tool_use_error>message</tool_use_error>`` — SDK validation errors,
      the XML wrapper is stripped.
    - ``Exit code N\\n...`` — Bash errors, only the first line is kept
      (stdout/stderr output is already visible in the session item).
    - Plain text — returned as-is.

    Returns None when there is no error.
    """
    content = get_message_content_list(parsed_json, "user")
    if content is None:
        return None
    for item in content:
        if isinstance(item, dict) and item.get('type') == 'tool_result':
            if not item.get('is_error'):
                return None
            error_content = item.get('content', '')
            if isinstance(error_content, str):
                stripped = error_content.strip()
                if stripped.startswith('<tool_use_error>') and stripped.endswith('</tool_use_error>'):
                    return stripped[len('<tool_use_error>'):-len('</tool_use_error>')].strip() or 'Unknown error'
                if stripped.startswith('Exit code '):
                    return stripped.split('\n', 1)[0]
                return stripped or 'Unknown error'
            return 'Unknown error'
    return None


def is_tool_result_item(parsed_json: dict) -> bool:
    """
    Check if an item contains a tool_result.

    A tool_result item is a user message whose content array contains
    at least one entry of type "tool_result" (possibly bundled with
    other items like text).
    """
    content = get_message_content_list(parsed_json, "user")
    if content is None:
        return False
    return any(isinstance(item, dict) and item.get('type') == 'tool_result' for item in content)


def compute_file_change_stats(parsed_json: dict) -> str | None:
    """
    Compute diff stats from an Edit or Write tool_result's ``toolUseResult``.

    For **Edit** and **Write updates** (overwriting an existing file), the
    ``structuredPatch`` list of unified-diff hunks is used.  Each hunk has a
    ``lines`` list where entries prefixed with ``"+"`` are additions and
    ``"-"`` are removals.

    For **Write creates** (new file), the ``content`` field is used to count
    the total number of lines added (no removals).

    Returns a JSON string like ``{"lines_added": 5, "lines_removed": 3}``
    (with an extra ``"hunks"`` key when there are multiple hunks), or *None*
    when the data is unavailable (error result, old format).
    """
    tool_use_result = parsed_json.get('toolUseResult')
    if not isinstance(tool_use_result, dict):
        return None

    structured_patch = tool_use_result.get('structuredPatch')

    # Write creates: structuredPatch is empty, count lines from content
    if isinstance(structured_patch, list) and not structured_patch:
        content = tool_use_result.get('content')
        if isinstance(content, str):
            lines_added = content.count('\n') + 1 if content else 0
            return orjson.dumps({'lines_added': lines_added}).decode()
        return None

    # Edit and Write updates: count +/- from structuredPatch hunks
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


def get_task_tool_uses(parsed_json: dict) -> list[tuple[str, bool]]:
    """
    Extract tool_use IDs and background flag from agent tool calls in an assistant message.

    Returns a list of (tool_use_id, is_background) tuples for tool_use items
    where name is "Task" or "Agent".
    """
    content = get_message_content_list(parsed_json, "assistant")
    if content is None:
        return []
    results = []
    for item in content:
        if (
            isinstance(item, dict)
            and item.get('type') == 'tool_use'
            and item.get('name') in AGENT_TOOL_NAMES
            and item.get('id')
        ):
            is_background = bool(isinstance(item.get('input'), dict) and item['input'].get('run_in_background'))
            results.append((item['id'], is_background))
    return results


def get_tool_result_agent_info(parsed_json: dict) -> tuple[str, str] | None:
    """
    Extract (tool_use_id, agent_id) from a tool_result with agentId.

    Checks both the tool_result content and the root-level toolUseResult
    for the agent_id. Returns None if this is not a Task tool result with an agent.

    Args:
        parsed_json: Parsed JSONL line

    Returns:
        Tuple of (tool_use_id, agent_id) if found, None otherwise
    """
    # Must contain a tool_result item
    content = get_message_content_list(parsed_json, "user")
    if content is None:
        return None
    tool_result = None
    for item in content:
        if isinstance(item, dict) and item.get('type') == 'tool_result':
            tool_result = item
            break
    if tool_result is None:
        return None

    tool_use_id = tool_result.get('tool_use_id')
    if not tool_use_id:
        return None

    # Check for agentId in the root-level toolUseResult
    tool_use_result = parsed_json.get('toolUseResult')
    if not isinstance(tool_use_result, dict):
        return None

    agent_id = tool_use_result.get('agentId')
    if not agent_id:
        return None

    return tool_use_id, agent_id


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
# Item Metadata Computation (display_level, kind)
# =============================================================================


def compute_item_display_level(parsed_json: dict, kind: ItemKind | None) -> int:
    """
    Determine the display level for an item based on its JSON content and kind.

    Classification rules:
    - ALWAYS (1): USER_MESSAGE, ASSISTANT_MESSAGE, API_ERROR kinds
    - COLLAPSIBLE (2): Meta messages, thinking/tool_use only,
                       summaries, file snapshots
    - DEBUG_ONLY (3): SYSTEM kind, standalone tool_result items

    Args:
        parsed_json: Parsed JSON content of the item
        kind: The pre-computed ItemKind (or None)

    Returns:
        ItemDisplayLevel enum value (1=ALWAYS, 2=COLLAPSIBLE, 3=DEBUG_ONLY)

    Note:
        Any modification to this function's logic MUST increment
        CLAUDE_CODE_COMPUTE_VERSION in settings.py to trigger recomputation.
    """
    # These kinds are always visible
    if kind in (ItemKind.USER_MESSAGE, ItemKind.ASSISTANT_MESSAGE, ItemKind.API_ERROR, ItemKind.COMPACT_SUMMARY):
        return ItemDisplayLevel.ALWAYS

    # DEBUG_ONLY: SYSTEM kind (system messages, queue-operation, progress, XML commands,
    # custom-title entries written by Claude CLI on every resume — very noisy)
    if kind == ItemKind.SYSTEM:
        return ItemDisplayLevel.DEBUG_ONLY

    # DEBUG_ONLY: Standalone tool_result items (their data is accessed via ToolResultLink)
    if is_tool_result_item(parsed_json):
        return ItemDisplayLevel.DEBUG_ONLY

    # Everything else is collapsible: meta messages, thinking/tool_use,
    # summaries, file snapshots, etc.
    return ItemDisplayLevel.COLLAPSIBLE


def compute_item_kind(parsed_json: dict) -> ItemKind | None:
    """
    Determine the kind/category of an item based on its JSON content.

    Classification rules:
    - USER_MESSAGE: User messages with visible content (text, document, image), not meta
    - ASSISTANT_MESSAGE: Assistant messages with visible content (text, document, image)
    - API_ERROR: System messages with subtype 'api_error', or messages with isApiErrorMessage=true
    - SYSTEM: System messages (except api_error), queue-operation, progress, summary, file-history-snapshot, last-prompt, custom-title

    Args:
        parsed_json: Parsed JSON content of the item

    Returns:
        ItemKind enum value, or None if not a recognized kind

    Note:
        Any modification to this function's logic MUST increment
        CLAUDE_CODE_COMPUTE_VERSION in settings.py to trigger recomputation.
    """
    # "Bastard" API error format: type="assistant" but isApiErrorMessage=true
    # The error text is serialized in content[0].text as a raw string
    if parsed_json.get('isApiErrorMessage'):
        return ItemKind.API_ERROR

    entry_type = parsed_json.get('type')

    # System types: system (except api_error), queue-operation, progress, custom-title, etc.
    if entry_type in ('queue-operation', 'progress', 'summary', 'file-history-snapshot', 'last-prompt', 'attachment', 'permission-mode', 'custom-title'):
        return ItemKind.SYSTEM

    if entry_type == 'system':
        subtype = parsed_json.get('subtype')
        if subtype == 'api_error':
            return ItemKind.API_ERROR
        return ItemKind.SYSTEM

    # User messages
    if entry_type == 'user':

        # Compact summary: user message with isCompactSummary flag (context compaction)
        if parsed_json.get('isCompactSummary'):
            return ItemKind.COMPACT_SUMMARY

        content = get_message_content(parsed_json)
        text = extract_text_from_content(content)

        # Commands are shown as user messages (except /clear which is system)
        if text is not None and (command := extract_command(text)):
            if command.name == '/clear':
                return ItemKind.SYSTEM
            return ItemKind.USER_MESSAGE

        # Meta messages are not user messages
        if parsed_json.get('isMeta'):
            return ItemKind.SYSTEM

        # System XML messages (commands, outputs) are SYSTEM kind
        if _is_system_xml_content(content):
            return ItemKind.SYSTEM

        # Tool results bundled with text (e.g. "Tool loaded.") are CONTENT_ITEMS, not user messages
        if isinstance(content, list) and any(
            isinstance(item, dict) and item.get('type') == 'tool_result' for item in content
        ):
            return ItemKind.CONTENT_ITEMS

        # Only user messages with visible content count as USER_MESSAGE
        if text or _has_visible_content(content):
            return ItemKind.USER_MESSAGE

        # Content array without visible items → CONTENT_ITEMS
        if isinstance(content, list):
            return ItemKind.CONTENT_ITEMS

        return None

    # Assistant messages
    if entry_type == 'assistant':
        content = get_message_content(parsed_json)

        # "No response requested." is a system-level message, not a real assistant response
        if (
            isinstance(content, list)
            and len(content) == 1
            and isinstance(content[0], dict)
            and content[0].get('type') == 'text'
            and content[0].get('text') == 'No response requested.'
        ):
            return ItemKind.SYSTEM

        # Only assistant messages with visible content count as ASSISTANT_MESSAGE
        if _has_visible_content(content):
            return ItemKind.ASSISTANT_MESSAGE

        # Content array without visible items → CONTENT_ITEMS
        if isinstance(content, list):
            return ItemKind.CONTENT_ITEMS

        return None

    return None


def compute_item_metadata(parsed_json: dict) -> dict:
    """
    Compute all metadata fields for a single item.

    Kind is computed first, then used to determine display_level.

    Args:
        parsed_json: Parsed JSON content of the item

    Returns:
        Dict with computed metadata fields:
        - display_level: int (ItemDisplayLevel enum value)
        - kind: str | None (item category)
    """
    kind = compute_item_kind(parsed_json)
    return {
        'display_level': compute_item_display_level(parsed_json, kind),
        'kind': kind,
    }


def extract_item_timestamp(parsed_json: dict) -> datetime | None:
    """
    Extract timestamp from parsed JSON.

    The timestamp is always present at the root level of JSONL lines.

    Args:
        parsed_json: The parsed JSON content of the item

    Returns:
        datetime object (UTC aware), or None if not found or parsing fails.
    """
    timestamp_str = parsed_json.get("timestamp")
    if timestamp_str:
        return parse_timestamp_to_datetime(timestamp_str)
    return None


# =============================================================================
# Cost and Context Usage Computation
# =============================================================================


def compute_item_cost_and_usage(
    item: SessionItem,
    parsed_json: dict,
    seen_message_ids: set[str],
) -> None:
    """
    Compute and assign cost, context_usage, and message_id on a SessionItem.

    This function handles deduplication: cost is only assigned if the message_id
    has not been seen before (Claude Code writes multiple JSONL lines for a single
    API call with the same message.id due to streaming).

    Modifies the item in place. Also modifies the seen_message_ids set.

    Args:
        item: The SessionItem to update (must have content already set)
        parsed_json: The parsed JSON content of the item
        seen_message_ids: Set of already-seen message IDs for deduplication
    """
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

    # Cost: only computed if message_id not already seen (deduplication)
    if msg_id and msg_id not in seen_message_ids:
        seen_message_ids.add(msg_id)

        model_info = extract_model_info(message.get("model", ""))
        if model_info:
            model_id = f"anthropic/claude-{model_info.family}-{model_info.version}"
            if timestamp_str := parsed_json.get("timestamp"):
                if dt := parse_timestamp_to_datetime(timestamp_str):
                    from twicc.providers.helpers import get_provider_helpers
                    item.cost = get_provider_helpers(Provider.CLAUDE_CODE).calculate_line_cost(
                        token_usage, model_id, dt.date(),
                    )


# =============================================================================
# Live Processing: compute_item_metadata_live
# =============================================================================


def _find_open_group_head(session_id: str, before_line_num: int) -> int | None:
    """
    Find the head of any open group before the given line number.

    Skips DEBUG_ONLY items. Returns None if no open group.
    """
    # Look at previous non-DEBUG_ONLY item
    previous = SessionItem.objects.filter(
        session_id=session_id,
        line_num__lt=before_line_num,
    ).exclude(
        display_level=ItemDisplayLevel.DEBUG_ONLY
    ).order_by('-line_num').first()

    if not previous:
        return None

    # COLLAPSIBLE with group_head = group is open
    if previous.display_level == ItemDisplayLevel.COLLAPSIBLE and previous.group_head:
        return previous.group_head

    # ALWAYS with suffix = check if it has collapsible suffix
    if previous.display_level == ItemDisplayLevel.ALWAYS:
        try:
            parsed = orjson.loads(previous.content)
            _, has_suffix = _detect_prefix_suffix(parsed, previous.kind)
            if has_suffix:
                return previous.line_num  # ALWAYS item is the head
        except orjson.JSONDecodeError:
            pass

    return None


def create_tool_result_link_live(
    session_id: str, item: SessionItem, parsed_json: dict
) -> ToolResultUpdate | None:
    """
    Create a ToolResultLink for a tool_result item during live sync.

    Searches the session for the item containing the matching tool_use
    and creates the link entry.

    Returns a ToolResultUpdate if the tool is tracked (Bash/Task/Agent), None otherwise.
    """

    tool_use_id = get_tool_result_id(parsed_json)
    if not tool_use_id:
        return None

    # Find candidates by text search (LIKE), ordered most recent first.
    # The tool_use_id string could appear in text content (e.g. assistant mentioning it),
    # so we iterate candidates and verify each one until we find an actual tool_use match.
    candidates = SessionItem.objects.filter(
        session_id=session_id,
        line_num__lt=item.line_num,
        content__contains=tool_use_id,
    ).order_by('-line_num')

    for candidate in candidates.iterator(chunk_size=10):
        try:
            candidate_parsed = orjson.loads(candidate.content)
        except orjson.JSONDecodeError:
            continue

        tool_use_entries = get_tool_use_entries(candidate_parsed)
        if tool_use_id in tool_use_entries:
            tool_name = tool_use_entries[tool_use_id]
            extra = compute_file_change_stats(parsed_json) if tool_name in ('Edit', 'Write') else None
            error = get_tool_result_error(parsed_json)
            _, created = ToolResultLink.objects.get_or_create(
                session_id=session_id,
                tool_use_line_num=candidate.line_num,
                tool_result_line_num=item.line_num,
                tool_use_id=tool_use_id,
                defaults={'tool_name': tool_name, 'tool_result_at': item.timestamp, 'extra': extra, 'error': error},
            )
            if not created:
                return None

            # Emit ToolResultUpdate for all tools (spinner + error indicator)
            links = ToolResultLink.objects.filter(
                session_id=session_id,
                tool_use_id=tool_use_id,
            )
            result_count = links.count()
            max_timestamp = links.order_by('-tool_result_at').values_list('tool_result_at', flat=True).first()
            return ToolResultUpdate(
                session_id=session_id,
                tool_use_id=tool_use_id,
                result_count=result_count,
                completed_at=max_timestamp,
                extra=extra,
                error=error,
                tool_result_line_num=item.line_num,
            )

    return None


def check_agent_naturally_stopped(
    session_id: str, tool_result_update: ToolResultUpdate
) -> AgentStoppedUpdate | None:
    """Check if a Task/Agent tool_result indicates a subagent has naturally finished.

    For non-background agents, 1 tool_result means done.
    For background agents, 2 tool_results means done.

    If the agent is done, updates its last_stopped_at and last_updated_at.

    Returns an AgentStoppedUpdate if the agent stopped, None otherwise.
    """
    from twicc.core.models import Session

    # Find the AgentLink for this tool_use_id
    agent_link = AgentLink.objects.filter(
        session_id=session_id,
        tool_use_id=tool_result_update.tool_use_id,
    ).first()
    if agent_link is None:
        return None

    required_results = 2 if agent_link.is_background else 1
    if tool_result_update.result_count < required_results:
        return None

    stopped_at = tool_result_update.completed_at
    if stopped_at is None:
        return None

    # Find the agent session and update its lifecycle timestamps
    agent_session_id = agent_link.agent_id
    updated = Session.objects.filter(
        id=agent_session_id,
    ).update(last_stopped_at=stopped_at, last_updated_at=stopped_at)

    if updated:
        return AgentStoppedUpdate(
            agent_session_id=agent_session_id,
            stopped_at=stopped_at,
        )

    return None


def create_agent_link_from_tool_result(session_id: str, item: SessionItem, parsed_json: dict) -> AgentLinkUpdate | None:
    """
    Create an AgentLink for a Task tool_result with agentId during live sync.

    When a tool_result arrives with an agentId in toolUseResult, this function
    finds the corresponding Task tool_use and creates an agent link.

    Returns an AgentLinkUpdate if a link was created, None otherwise.
    """

    agent_info = get_tool_result_agent_info(parsed_json)
    if not agent_info:
        return None

    tool_use_id, agent_id = agent_info

    if is_agent_link_done(session_id, agent_id):
        return None

    # Check if we already have this agent link
    if AgentLink.objects.filter(
        session_id=session_id,
        agent_id=agent_id,
    ).exists():
        mark_agent_link_done(session_id, agent_id)
        return None

    # Find the Task tool_use by searching for the tool_use_id
    candidates = SessionItem.objects.filter(
        session_id=session_id,
        line_num__lt=item.line_num,
        content__contains=tool_use_id,
    ).order_by('-line_num')

    for candidate in candidates.iterator(chunk_size=10):
        try:
            candidate_parsed = orjson.loads(candidate.content)
        except orjson.JSONDecodeError:
            continue

        # Check if this candidate has a Task tool_use with this ID
        for tu_id, is_background in get_task_tool_uses(candidate_parsed):
            if tu_id != tool_use_id:
                continue
            try:
                obj, created = AgentLink.objects.get_or_create(
                    session_id=session_id,
                    tool_use_line_num=candidate.line_num,
                    tool_use_id=tool_use_id,
                    defaults={"agent_id": agent_id, "is_background": is_background, "started_at": candidate.timestamp},
                )
                mark_agent_link_done(session_id, agent_id)
                if created:
                    return AgentLinkUpdate(
                        parent_session_id=session_id,
                        agent_id=agent_id,
                        tool_use_id=tool_use_id,
                        tool_use_line_num=candidate.line_num,
                        is_background=is_background,
                        started_at=candidate.timestamp,
                    )
            except MultipleObjectsReturned:  # defensive mode
                pass
            return None
    return None


def _extract_task_tool_use_prompts(content: list) -> list[tuple[str, str, bool]]:
    """
    Extract (tool_use_id, prompt, is_background) triples from agent tool_use items in content.

    Args:
        content: The content array from an assistant message

    Returns:
        List of (tool_use_id, prompt, is_background) tuples for all agent tool_uses found (Task or Agent)
    """
    results = []
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


def create_agent_link_from_subagent(
    parent_session_id: str,
    agent_id: str,
    agent_prompt: str,
) -> AgentLinkUpdate | None:
    """
    Create an AgentLink for a subagent by matching its prompt to a Task tool_use.

    When a new subagent is detected, this function searches the parent session
    for a Task tool_use with a matching prompt and creates an agent link.

    This allows linking the tool_use to its agent before the tool_result arrives.

    Returns an AgentLinkUpdate if the link was created, None otherwise.
    """

    if is_agent_link_done(parent_session_id, agent_id):
        return None

    # Check if we already have this agent link
    if AgentLink.objects.filter(
        session_id=parent_session_id,
        agent_id=agent_id,
    ).exists():
        mark_agent_link_done(parent_session_id, agent_id)
        return None

    agent_prompt = agent_prompt.strip()

    # Search for agent tool_use items in parent session, most recent first
    # We look for items containing '"name":"Task"' or '"name":"Agent"' to narrow the search
    candidates = SessionItem.objects.filter(
        Q(content__contains='"name":"Task"') | Q(content__contains='"name":"Agent"'),
        session_id=parent_session_id,
    ).order_by('-line_num')

    for index, candidate in enumerate(candidates.iterator(chunk_size=20)):
        try:
            candidate_parsed = orjson.loads(candidate.content)
        except orjson.JSONDecodeError:
            continue

        # Get the content list from assistant message
        content = get_message_content_list(candidate_parsed, "assistant")
        if content is None:
            continue

        # Extract (tool_use_id, prompt, is_background) triples from Task tool_uses
        for tu_id, prompt, is_background in _extract_task_tool_use_prompts(content):
            if prompt.strip() == agent_prompt:
                try:
                    obj, created = AgentLink.objects.get_or_create(
                        session_id=parent_session_id,
                        tool_use_line_num=candidate.line_num,
                        tool_use_id=tu_id,
                        defaults={"agent_id": agent_id, "is_background": is_background, "started_at": candidate.timestamp},
                    )
                    if created:
                        mark_agent_link_done(parent_session_id, agent_id)
                        return AgentLinkUpdate(
                            parent_session_id=parent_session_id,
                            agent_id=agent_id,
                            tool_use_id=tu_id,
                            tool_use_line_num=candidate.line_num,
                            is_background=is_background,
                            started_at=candidate.timestamp,
                        )
                except MultipleObjectsReturned:  # defensive mode
                    continue

    return None


def create_agent_link_from_tool_use(
    session_id: str,
    item: SessionItem,
    parsed_json: dict,
) -> list[AgentLinkUpdate]:
    """
    Create AgentLink(s) for Task tool_use(s) by matching against existing subagents.

    When a new assistant message containing Task tool_use(s) is synced in the parent session,
    this function checks if any matching subagents already exist in the database.
    This handles the race condition where the subagent file is synced before the parent
    session's Task tool_use item exists: when the parent catches up, we create the link here.

    Returns a list of AgentLinkUpdates for each link created.
    """

    # Extract assistant message content
    content = get_message_content_list(parsed_json, "assistant")
    if content is None:
        return []

    # Collect all (tool_use_id, prompt, is_background) triples from agent tool_uses in this message
    task_prompts = _extract_task_tool_use_prompts(content)
    # Normalize prompts for matching
    task_prompts = [(tu_id, prompt.strip(), is_bg) for tu_id, prompt, is_bg in task_prompts]

    if not task_prompts:
        return []

    updates: list[AgentLinkUpdate] = []

    # Get all subagents for this session that don't have a link yet
    subagents = Session.objects.filter(
        parent_session_id=session_id,
        type=SessionType.SUBAGENT,
    )

    # For each subagent, check if its prompt matches one of our Task tool_use prompts
    for subagent in subagents:
        if is_agent_link_done(session_id, subagent.id):
            continue

        # Check if link already exists
        if AgentLink.objects.filter(
            session_id=session_id,
            agent_id=subagent.id,
        ).exists():
            mark_agent_link_done(session_id, subagent.id)
            continue

        # Get the subagent's prompt from cache or DB
        subagent_prompt = get_cached_agent_prompt(session_id, subagent.id)
        if not subagent_prompt:
            # Try to get from the subagent's first user message
            first_user_message = SessionItem.objects.filter(
                session_id=subagent.id,
                kind=ItemKind.USER_MESSAGE,
            ).first()
            if first_user_message is None:
                continue
            try:
                first_parsed = orjson.loads(first_user_message.content)
            except orjson.JSONDecodeError:
                continue
            subagent_prompt = extract_text_from_content(get_message_content(first_parsed))
            if not subagent_prompt:
                continue
            subagent_prompt = subagent_prompt.strip()
            cache_agent_prompt(session_id, subagent.id, subagent_prompt)

        # Check if the subagent's prompt matches any Task tool_use prompt
        for tu_id, prompt, is_background in task_prompts:
            if prompt == subagent_prompt:
                try:
                    _, created = AgentLink.objects.get_or_create(
                        session_id=session_id,
                        tool_use_line_num=item.line_num,
                        tool_use_id=tu_id,
                        defaults={"agent_id": subagent.id, "is_background": is_background, "started_at": item.timestamp},
                    )
                    if created:
                        mark_agent_link_done(session_id, subagent.id)
                        updates.append(AgentLinkUpdate(
                            parent_session_id=session_id,
                            agent_id=subagent.id,
                            tool_use_id=tu_id,
                            tool_use_line_num=item.line_num,
                            is_background=is_background,
                            started_at=item.timestamp,
                        ))
                except MultipleObjectsReturned:
                    pass
                break

    return updates


def compute_item_metadata_live(session_id: str, item: SessionItem, parsed_json: dict) -> set[int]:
    """
    Compute metadata for a single item during live sync.

    Unlike batch processing, this queries the database for context.

    Args:
        session_id: The session ID
        item: The SessionItem object (already has line_num and content set)
        parsed_json: The already-parsed JSON content (possibly transformed)

    Returns:
        Set of line_nums of pre-existing items whose group_tail was updated
    """
    # Resolve git directory/branch from tool_use paths (no cache for live resolution)
    git_resolution = resolve_git_for_item(parsed_json, use_cache=False)
    if git_resolution is not None:
        item.git_directory, item.git_branch = git_resolution

    # Initialize group fields
    item.group_head = None
    item.group_tail = None

    if item.display_level == ItemDisplayLevel.DEBUG_ONLY:
        return set()

    # Track which pre-existing items were modified
    modified_line_nums: set[int] = set()

    # Find if there's an open group before us
    open_group_head = _find_open_group_head(session_id, item.line_num)

    if item.display_level == ItemDisplayLevel.COLLAPSIBLE:
        if open_group_head is not None:
            # Join existing group
            item.group_head = open_group_head
            item.group_tail = item.line_num

            # Get line_nums of pre-existing items that will be updated
            affected_collapsibles = SessionItem.objects.filter(
                session_id=session_id,
                group_head=open_group_head,
                line_num__lt=item.line_num
            ).values_list('line_num', flat=True)
            modified_line_nums.update(affected_collapsibles)

            # Check if ALWAYS started this group
            always_starter = SessionItem.objects.filter(
                session_id=session_id,
                line_num=open_group_head,
                display_level=ItemDisplayLevel.ALWAYS
            ).exists()
            if always_starter:
                modified_line_nums.add(open_group_head)

            # Update all items in group with new tail
            SessionItem.objects.filter(
                session_id=session_id,
                group_head=open_group_head
            ).update(group_tail=item.line_num)

            # Also update ALWAYS item if it started the group (via suffix)
            SessionItem.objects.filter(
                session_id=session_id,
                line_num=open_group_head,
                display_level=ItemDisplayLevel.ALWAYS
            ).update(group_tail=item.line_num)
        else:
            # Start new group
            item.group_head = item.line_num
            item.group_tail = item.line_num

    elif item.display_level == ItemDisplayLevel.ALWAYS:
        has_prefix, has_suffix = _detect_prefix_suffix(parsed_json, item.kind)

        # Handle prefix
        if has_prefix and open_group_head is not None:
            item.group_head = open_group_head

            # Get line_nums of pre-existing items that will be updated
            affected_collapsibles = SessionItem.objects.filter(
                session_id=session_id,
                group_head=open_group_head,
                line_num__lt=item.line_num
            ).values_list('line_num', flat=True)
            modified_line_nums.update(affected_collapsibles)

            # Check if ALWAYS started this group
            always_starter = SessionItem.objects.filter(
                session_id=session_id,
                line_num=open_group_head,
                display_level=ItemDisplayLevel.ALWAYS
            ).exists()
            if always_starter:
                modified_line_nums.add(open_group_head)

            # Update all items in group with new tail (this item)
            SessionItem.objects.filter(
                session_id=session_id,
                group_head=open_group_head
            ).update(group_tail=item.line_num)

            # Also update ALWAYS item if it started the group
            SessionItem.objects.filter(
                session_id=session_id,
                line_num=open_group_head,
                display_level=ItemDisplayLevel.ALWAYS
            ).update(group_tail=item.line_num)

        # Suffix: group_tail stays null until next item arrives and connects
        # (will be updated by next item's compute_item_metadata_live)

    return modified_line_nums

