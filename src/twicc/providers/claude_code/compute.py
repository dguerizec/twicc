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
from datetime import datetime, timezone
from decimal import Decimal
from typing import ClassVar, NamedTuple

import xmltodict
from django.conf import settings
from django.core.exceptions import MultipleObjectsReturned
from django.db.models import Q

from twicc.core.enums import ItemDisplayLevel, ItemKind, Provider
from twicc.core.models import AgentLink, Session, SessionItem, SessionType, ToolResultLink
from twicc.git import read_head_branch, resolve_git_from_path
from twicc.pricing import calculate_line_context_usage
from twicc.projects import (
    ensure_project_directory,
    ensure_project_git_root,
    get_project_directory,
    get_project_git_root,
    update_project_metadata,
)
from twicc.providers.compute_base import (
    AGENTS_LINKS_DONE_CACHE,
    AGENTS_PROMPT_CACHE,
    AgentLinkUpdate,
    AgentStoppedUpdate,
    BaseSessionCompute,
    ContentAnalysis,
    GroupState,
    ItemGroupInfo,
    ToolResultInfo,
    ToolResultUpdate,
    cache_agent_prompt,
    get_cached_agent_prompt,
    is_agent_link_done,
    mark_agent_link_done,
    parse_timestamp_to_datetime,
    strip_markdown,
    uncache_agent_prompt,
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


# =============================================================================
# Batch Compute — single-pass analysis + full-session metadata
# =============================================================================


# Shared empty constants to avoid allocating new empty collections for every item
# that doesn't have the relevant content. MUST NOT be mutated.
_EMPTY_TOOL_USE_ENTRIES: dict[str, str] = {}
_EMPTY_TASK_TOOL_USES: list[tuple[str, bool]] = []
_EMPTY_FILE_PATHS: list[str] = []

_EMPTY_ANALYSIS = ContentAnalysis(
    has_visible_content=False,
    text_content=None,
    is_system_xml=False,
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


def analyze_content(parsed_json: dict) -> ContentAnalysis:
    """
    Single-pass content analysis for batch computation.

    Extracts all information from parsed_json's message.content in one traversal,
    replacing multiple function calls that each traverse the content array separately.

    Consolidates the work of: _has_visible_content, extract_text_from_content,
    _is_system_xml_content, is_tool_result_item, get_tool_result_id,
    get_tool_result_error, get_tool_use_entries, get_task_tool_uses,
    extract_paths_from_tool_uses, _has_collapsible_prefix/_suffix,
    and get_tool_result_agent_info.

    Args:
        parsed_json: Parsed JSONL line dict

    Returns:
        ContentAnalysis with all extracted fields
    """
    # Get message.content
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


def compute_session_metadata(session_id: str, result_queue) -> None:
    """
    Compute metadata for all items in a session.

    Sends all results via result_queue as a single message per session.
    Does NOT write to the database directly.
    The caller is responsible for consuming the queue and applying changes.

    Uses single-pass content analysis (analyze_content) to avoid redundant
    content array traversals. The following calls are replaced by analysis fields:
    - get_tool_use_entries    -> analysis.tool_use_entries
    - get_task_tool_uses      -> analysis.task_tool_uses
    - get_tool_result_id      -> analysis.tool_result_id
    - get_tool_result_error   -> analysis.tool_result_error
    - get_tool_result_agent_info -> analysis.tool_result_agent_info
    - _detect_prefix_suffix   -> analysis.has_prefix/has_suffix

    Args:
        session_id: The session ID
        result_queue: Queue to send results (multiprocessing.Queue or queue.Queue)
    """
    from django.db import connection

    # Ensure this process/thread has its own database connection
    connection.close()

    try:
        session = Session.objects.get(id=session_id)
    except Session.DoesNotExist:
        logger.error(f"Session {session_id} not found for metadata computation")
        result_queue.put(orjson.dumps({
            'type': 'error',
            'session_id': session_id,
            'error': 'Session not found',
        }))
        return

    queryset = SessionItem.objects.filter(session=session).order_by('line_num')

    state = GroupState()
    items_to_update: list[SessionItem] = []
    all_item_updates: list[dict] = []
    all_tool_result_links: dict[tuple[str, int], dict] = {}  # (tool_use_id, tool_result_line_num) -> serialized
    all_agent_links: dict[tuple[str, str], dict] = {}  # (agent_id, tool_use_id) -> serialized
    content_overrides: list[dict] = []
    batch_size = 500

    def serialize_item(item: SessionItem) -> dict:
        return {
            'id': item.id,
            'display_level': item.display_level,
            'group_head': item.group_head,
            'group_tail': item.group_tail,
            'kind': item.kind,
            'message_id': item.message_id,
            'cost': str(item.cost) if item.cost is not None else None,
            'context_usage': item.context_usage,
            'timestamp': item.timestamp.isoformat() if item.timestamp else None,
            'git_directory': item.git_directory,
            'git_branch': item.git_branch,
        }

    def flush_items(items: list[SessionItem]) -> None:
        for item in items:
            serialized = serialize_item(item)
            if serialized != original_serialized.get(item.id):
                all_item_updates.append(serialized)

    def serialize_tool_result_link(link: ToolResultLink) -> dict:
        return {
            'session_id': link.session_id,
            'tool_use_line_num': link.tool_use_line_num,
            'tool_result_line_num': link.tool_result_line_num,
            'tool_use_id': link.tool_use_id,
            'tool_name': link.tool_name,
            'tool_result_at': link.tool_result_at.isoformat() if link.tool_result_at else None,
            'extra': link.extra,
            'error': link.error,
        }

    def serialize_agent_link(link: AgentLink) -> dict:
        return {
            'session_id': link.session_id,
            'tool_use_line_num': link.tool_use_line_num,
            'tool_use_id': link.tool_use_id,
            'agent_id': link.agent_id,
            'is_background': link.is_background,
            'started_at': link.started_at.isoformat() if link.started_at else None,
        }

    tool_use_map: dict[str, tuple[int, str]] = {}
    task_tool_use_map: dict[str, tuple[int, bool, datetime]] = {}
    initial_title_set = False
    session_titles: dict[str, str] = {}
    user_message_count = 0
    affected_days: set[str] = set()
    seen_message_ids: set[str] = set()
    last_context_usage: int | None = None
    first_timestamp: datetime | None = None
    last_started_at: datetime | None = None
    last_updated_at: datetime | None = None
    first_cwd: str | None = None
    last_cwd: str | None = None
    last_cwd_git_branch: str | None = None
    last_model: str | None = None
    last_slug: str | None = None
    last_resolved_git_directory: str | None = None
    last_resolved_git_branch: str | None = None
    found_compact_summary = False
    agent_tool_result_counts: dict[str, tuple[int, datetime | None]] = {}
    agent_stopped_list: list[dict] = []
    original_serialized: dict[int, dict] = {}

    # Load existing links for change detection
    original_tool_result_links: dict[tuple[str, int], dict] = {}
    original_tool_result_links_ids: dict[tuple[str, int], int] = {}
    for link in ToolResultLink.objects.filter(session_id=session_id):
        key = (link.tool_use_id, link.tool_result_line_num)
        original_tool_result_links[key] = serialize_tool_result_link(link)
        original_tool_result_links_ids[key] = link.id

    original_agent_links: dict[tuple[str, str], dict] = {}
    original_agent_links_ids: dict[tuple[str, str], int] = {}
    for link in AgentLink.objects.filter(session_id=session_id):
        key = (link.agent_id, link.tool_use_id)
        original_agent_links[key] = serialize_agent_link(link)
        original_agent_links_ids[key] = link.id

    for item in queryset.iterator(chunk_size=batch_size):
        # Snapshot original state before any computation, for change detection
        original_serialized[item.id] = serialize_item(item)

        try:
            parsed = orjson.loads(item.content)
        except orjson.JSONDecodeError:
            logger.warning(f"Invalid JSON in item {item.session_id}:{item.line_num}")
            parsed = {}

        # Transform task-notification XML into standard tool_result format
        new_content = transform_task_notification(parsed)
        if new_content is None:
            new_content = transform_local_command_output(parsed)
        if new_content is not None and new_content != item.content:
            item.content = new_content
            content_overrides.append({'id': item.id, 'content': new_content})

        # Single-pass content analysis (replaces multiple individual content traversals)
        analysis = analyze_content(parsed)

        # Compute display_level and kind
        metadata = compute_item_metadata(parsed)
        item.display_level = metadata['display_level']
        item.kind = metadata['kind']

        # Track compact summary for session.compacted flag
        if item.kind == ItemKind.COMPACT_SUMMARY:
            found_compact_summary = True

        # Extract timestamp
        item.timestamp = extract_item_timestamp(parsed)
        if first_timestamp is None and item.timestamp is not None:
            first_timestamp = item.timestamp
            last_started_at = first_timestamp
            affected_days.add(first_timestamp.date().isoformat())
        if item.timestamp is not None:
            last_updated_at = item.timestamp
        if (
            item.timestamp is not None
            and parsed.get('type') == 'progress'
            and isinstance(parsed.get('data'), dict)
            and parsed['data'].get('hookEvent') == 'SessionStart'
        ):
            last_started_at = item.timestamp

        # Compute cost and context usage
        compute_item_cost_and_usage(item, parsed, seen_message_ids)
        if item.context_usage is not None:
            last_context_usage = item.context_usage

        # Extract runtime environment fields
        if cwd := parsed.get('cwd'):
            if first_cwd is None:
                first_cwd = cwd
            last_cwd = cwd
        if cwd_git_branch := parsed.get('gitBranch'):
            last_cwd_git_branch = cwd_git_branch
        if (message := parsed.get('message')) and isinstance(message, dict):
            if model := message.get('model'):
                last_model = model
        if item_slug := parsed.get('slug'):
            last_slug = item_slug

        # Resolve git directory/branch from tool_use paths
        if item.git_directory is not None:
            last_resolved_git_directory = item.git_directory
            last_resolved_git_branch = item.git_branch
        else:
            git_resolution = resolve_git_for_item(parsed)
            if git_resolution is not None:
                item.git_directory, item.git_branch = git_resolution
                last_resolved_git_directory = item.git_directory
                last_resolved_git_branch = item.git_branch

        # Handle title extraction
        if item.kind == ItemKind.USER_MESSAGE and not initial_title_set:
            title = extract_title_from_user_message(parsed)
            if title:
                session_titles[session_id] = title
                initial_title_set = True
        if item.kind == ItemKind.SYSTEM and parsed.get('type') == 'custom-title':
            custom_title = parsed.get('customTitle')
            target_session_id = parsed.get('sessionId', session_id)
            if custom_title and isinstance(custom_title, str):
                session_titles[target_session_id] = custom_title
        if item.kind == ItemKind.USER_MESSAGE:
            user_message_count += 1
        if item.timestamp and (item.kind == ItemKind.USER_MESSAGE or item.cost):
            affected_days.add(item.timestamp.date().isoformat())

        # Use analysis fields instead of individual function calls
        for tu_id, tu_name in analysis.tool_use_entries.items():
            tool_use_map[tu_id] = (item.line_num, tu_name)
        for tu_id, is_background in analysis.task_tool_uses:
            task_tool_use_map[tu_id] = (item.line_num, is_background, item.timestamp)
        tool_result_ref = analysis.tool_result_id
        if tool_result_ref and tool_result_ref in tool_use_map:
            tu_line_num, tu_name = tool_use_map[tool_result_ref]
            extra = compute_file_change_stats(parsed) if tu_name in ('Edit', 'Write') else None
            error = analysis.tool_result_error
            all_tool_result_links[(tool_result_ref, item.line_num)] = serialize_tool_result_link(ToolResultLink(
                session_id=session_id,
                tool_use_line_num=tu_line_num,
                tool_result_line_num=item.line_num,
                tool_use_id=tool_result_ref,
                tool_name=tu_name,
                tool_result_at=item.timestamp,
                extra=extra,
                error=error,
            ))
            if tu_name in AGENT_TOOL_NAMES:
                prev_count, _ = agent_tool_result_counts.get(tool_result_ref, (0, None))
                agent_tool_result_counts[tool_result_ref] = (prev_count + 1, item.timestamp)
        if analysis.tool_result_agent_info:
            tu_id, agent_id = analysis.tool_result_agent_info
            if tu_id in task_tool_use_map:
                line_num, is_background, started_at = task_tool_use_map[tu_id]
                all_agent_links[(agent_id, tu_id)] = serialize_agent_link(AgentLink(
                    session_id=session_id,
                    tool_use_line_num=line_num,
                    tool_use_id=tu_id,
                    agent_id=agent_id,
                    is_background=is_background,
                    started_at=started_at,
                ))
                del task_tool_use_map[tu_id]

        # Prefix/suffix for group state machine
        has_prefix, has_suffix = False, False
        if item.display_level == ItemDisplayLevel.ALWAYS and item.kind in (ItemKind.USER_MESSAGE, ItemKind.ASSISTANT_MESSAGE):
            has_prefix, has_suffix = analysis.has_prefix, analysis.has_suffix
        info = state.process_item(
            line_num=item.line_num,
            display_level=item.display_level,
            has_prefix=has_prefix,
            has_suffix=has_suffix,
            item_ref=item,
        )
        item.group_head = info.group_head
        items_to_update.extend(info.closed_items)
        if item.display_level == ItemDisplayLevel.DEBUG_ONLY:
            items_to_update.append(item)
        elif item.display_level == ItemDisplayLevel.ALWAYS and not has_suffix:
            items_to_update.append(item)

        # Flush batches
        if len(items_to_update) >= batch_size:
            flush_items(items_to_update)
            items_to_update = []

    # Finalize pending groups
    finalized = state.finalize()
    items_to_update.extend(finalized)

    flush_items(items_to_update)

    # Diff tool result links: create / update / delete
    trl_to_create: list[dict] = []
    trl_to_update: list[dict] = []
    for key, serialized in all_tool_result_links.items():
        original = original_tool_result_links.get(key)
        if original is None:
            trl_to_create.append(serialized)
        elif serialized != original:
            serialized['id'] = original_tool_result_links_ids[key]
            trl_to_update.append(serialized)
    trl_to_delete: list[int] = [
        pk for key, pk in original_tool_result_links_ids.items() if key not in all_tool_result_links
    ]

    # Diff agent links: create / update / delete
    agent_links_to_create: list[dict] = []
    agent_links_to_update: list[dict] = []
    for key, serialized in all_agent_links.items():
        original = original_agent_links.get(key)
        if original is None:
            agent_links_to_create.append(serialized)
        elif serialized != original:
            # Carry the existing PK so apply_session_complete can bulk_update
            serialized['id'] = original_agent_links_ids[key]
            agent_links_to_update.append(serialized)
    agent_links_to_delete: list[int] = [
        pk for key, pk in original_agent_links_ids.items() if key not in all_agent_links
    ]

    # Determine which agents have stopped (using all_agent_links dict)
    for tu_id, (result_count, last_ts) in agent_tool_result_counts.items():
        if last_ts is None:
            continue
        for link in all_agent_links.values():
            if link['tool_use_id'] == tu_id:
                required = 2 if link.get('is_background') else 1
                if result_count >= required:
                    agent_stopped_list.append({
                        'agent_session_id': link['agent_id'],
                        'stopped_at': last_ts.isoformat(),
                    })
                break

    project_directory = first_cwd if first_cwd and session.type == SessionType.SESSION else None

    if not last_resolved_git_directory and last_cwd:
        cwd_git = resolve_git_from_path(last_cwd)
        if cwd_git:
            last_resolved_git_directory, last_resolved_git_branch = cwd_git

    result_queue.put(orjson.dumps({
        'type': 'session_complete',
        'session_id': session_id,
        'project_id': session.project_id,
        'item_updates': all_item_updates,
        'item_fields': ['display_level', 'group_head', 'group_tail', 'kind', 'message_id', 'cost', 'context_usage', 'timestamp', 'git_directory', 'git_branch'],
        'content_overrides': content_overrides,
        'tool_result_links_to_create': trl_to_create,
        'tool_result_links_to_update': trl_to_update,
        'tool_result_links_to_delete': trl_to_delete,
        'agent_links_to_create': agent_links_to_create,
        'agent_links_to_update': agent_links_to_update,
        'agent_links_to_delete': agent_links_to_delete,
        'session_fields': {
            'compute_version': settings.CLAUDE_CODE_COMPUTE_VERSION,
            'user_message_count': user_message_count,
            'context_usage': last_context_usage,
            'cwd': last_cwd,
            'cwd_git_branch': last_cwd_git_branch,
            'git_directory': last_resolved_git_directory,
            'git_branch': last_resolved_git_branch,
            'model': last_model,
            'slug': last_slug,
            'compacted': found_compact_summary,
            'created_at': first_timestamp.isoformat() if first_timestamp else None,
            'last_started_at': last_started_at.isoformat() if last_started_at else None,
            'last_updated_at': datetime.fromtimestamp(session.mtime, tz=timezone.utc).isoformat() if session.mtime else (last_updated_at.isoformat() if last_updated_at else None),
            'last_stopped_at': datetime.fromtimestamp(session.mtime, tz=timezone.utc).isoformat() if session.mtime else None,
        },
        'titles': session_titles,
        'project_directory': project_directory,
        'affected_days': sorted(affected_days) if affected_days else None,
        'agent_stopped': agent_stopped_list or None,
    }))

    connection.close()


def apply_session_complete(msg: dict) -> None:
    """
    Apply all results for a session in one go.

    This handles the 'session_complete' message type that contains
    all updates for a session in a single message.

    Runs in the main process (called via sync_to_async from consume_compute_results).
    """
    session_id = msg['session_id']

    # 1. Apply item updates (only items that changed)
    item_updates = msg.get('item_updates', [])
    item_fields = msg.get('item_fields', [])
    if item_updates and item_fields:
        items = [
            SessionItem(id=upd['id'], **{
                field: Decimal(value) if (value := upd.get(field)) is not None and field == 'cost' else value
                for field in item_fields
            })
            for upd in item_updates
        ]
        SessionItem.objects.bulk_update(items, item_fields, 50)

    # 2. Apply content overrides (rare: only transformed task-notification items)
    content_overrides = msg.get('content_overrides', [])
    if content_overrides:
        items = [
            SessionItem(id=ovr['id'], content=ovr['content'])
            for ovr in content_overrides
        ]
        SessionItem.objects.bulk_update(items, ['content'], 50)

    # 3. Sync tool result links (diff-based: create/update/delete)
    trl_to_create = msg.get('tool_result_links_to_create', [])
    if trl_to_create:
        links = [
            ToolResultLink(
                session_id=d['session_id'],
                tool_use_line_num=d['tool_use_line_num'],
                tool_result_line_num=d['tool_result_line_num'],
                tool_use_id=d['tool_use_id'],
                tool_name=d['tool_name'],
                tool_result_at=datetime.fromisoformat(d['tool_result_at']) if d.get('tool_result_at') else None,
                extra=d.get('extra'),
                error=d.get('error'),
            )
            for d in trl_to_create
        ]
        ToolResultLink.objects.bulk_create(links, ignore_conflicts=True)

    trl_to_update = msg.get('tool_result_links_to_update', [])
    if trl_to_update:
        trl_update_fields = ['tool_use_line_num', 'tool_name', 'tool_result_at', 'extra', 'error']
        links = [
            ToolResultLink(
                id=d['id'],
                session_id=d['session_id'],
                tool_use_line_num=d['tool_use_line_num'],
                tool_result_line_num=d['tool_result_line_num'],
                tool_use_id=d['tool_use_id'],
                tool_name=d['tool_name'],
                tool_result_at=datetime.fromisoformat(d['tool_result_at']) if d.get('tool_result_at') else None,
                extra=d.get('extra'),
                error=d.get('error'),
            )
            for d in trl_to_update
        ]
        ToolResultLink.objects.bulk_update(links, trl_update_fields, 50)

    trl_to_delete = msg.get('tool_result_links_to_delete', [])
    if trl_to_delete:
        ToolResultLink.objects.filter(id__in=trl_to_delete).delete()

    # 4. Sync agent links (diff-based: create/update/delete)
    agent_links_to_create = msg.get('agent_links_to_create', [])
    if agent_links_to_create:
        links = [
            AgentLink(
                session_id=d['session_id'],
                tool_use_line_num=d['tool_use_line_num'],
                tool_use_id=d['tool_use_id'],
                agent_id=d['agent_id'],
                is_background=d['is_background'],
                started_at=datetime.fromisoformat(d['started_at']) if d.get('started_at') else None,
            )
            for d in agent_links_to_create
        ]
        AgentLink.objects.bulk_create(links, ignore_conflicts=True)

    agent_links_to_update = msg.get('agent_links_to_update', [])
    if agent_links_to_update:
        agent_link_fields = ['tool_use_line_num', 'is_background', 'started_at']
        links = [
            AgentLink(
                id=d['id'],
                session_id=d['session_id'],
                tool_use_line_num=d['tool_use_line_num'],
                tool_use_id=d['tool_use_id'],
                agent_id=d['agent_id'],
                is_background=d['is_background'],
                started_at=datetime.fromisoformat(d['started_at']) if d.get('started_at') else None,
            )
            for d in agent_links_to_update
        ]
        AgentLink.objects.bulk_update(links, agent_link_fields, 50)

    agent_links_to_delete = msg.get('agent_links_to_delete', [])
    if agent_links_to_delete:
        AgentLink.objects.filter(id__in=agent_links_to_delete).delete()

    # 5. Update session fields (always includes compute_version)
    session_fields = msg.get('session_fields', {})
    if session_fields:
        # Handle datetime fields
        for dt_field in ('created_at', 'last_started_at', 'last_updated_at', 'last_stopped_at'):
            if dt_field in session_fields and session_fields[dt_field] is not None:
                session_fields[dt_field] = datetime.fromisoformat(session_fields[dt_field])
        rows = Session.objects.filter(id=session_id).update(**session_fields)
        if rows == 0:
            logger.debug(f"apply_session_complete: session {session_id} not found for update (0 rows affected)")
        else:
            logger.debug(
                f"apply_session_complete: session {session_id} updated"
                f" (compute_version={session_fields.get('compute_version')})"
            )

    # 6. Recalculate session costs from SessionItem data (idempotent, order-independent)
    session = Session.objects.get(id=session_id)
    session.recalculate_costs()
    session.save(update_fields=["self_cost", "subagents_cost", "total_cost"])

    # 7. Recalculate parent session costs if subagent
    if session.parent_session_id:
        parent = Session.objects.get(id=session.parent_session_id)
        parent.recalculate_costs()
        parent.save(update_fields=["self_cost", "subagents_cost", "total_cost"])

    # 8. Update session titles
    titles = msg.get('titles', {})
    for target_id, title in titles.items():
        Session.objects.filter(id=target_id).update(title=title)

    # 9. Update project directory
    project_id = msg.get('project_id')
    project_directory = msg.get('project_directory')
    if project_id and project_directory:
        ensure_project_directory(project_id, project_directory)

    # 10. Resolve project git_root if session has git info but project doesn't
    session_git_dir = session_fields.get('git_directory') if session_fields else None
    if session_git_dir and project_id and get_project_git_root(project_id) is None:
        ensure_project_git_root(project_id)

    # 11. Update last_stopped_at for subagents that finished naturally
    agent_stopped = msg.get('agent_stopped')
    if agent_stopped:
        for entry in agent_stopped:
            stopped_at = datetime.fromisoformat(entry['stopped_at'])
            Session.objects.filter(id=entry['agent_session_id']).update(
                last_stopped_at=stopped_at, last_updated_at=stopped_at
            )

    # 12. Update project metadata (sessions_count, mtime, total_cost)
    if project_id:
        update_project_metadata(project_id)


# =============================================================================
# Live Sync — watcher entry point
# =============================================================================


def _inject_cached_original_file(parsed: dict, session_id: str, line_num: int) -> str | None:
    """Inject a cached originalFile into a tool_result that lacks one.

    If the tool_result has a toolUseResult with no originalFile and we have a
    cached copy from the PreToolUse hook, inject it and return the re-serialized
    JSON string.  Otherwise return None (no change needed).
    """
    tool_use_result = parsed.get('toolUseResult')
    if not isinstance(tool_use_result, dict):
        return None

    tool_use_id = get_tool_result_id(parsed)
    if not tool_use_id:
        return None

    # Always pop from cache (consume the entry whether we use it or not)
    cached = _pop_cached_original_file(session_id, tool_use_id)

    if cached is None:
        return None

    # Already has originalFile — no injection needed
    if tool_use_result.get('originalFile') is not None:
        return None

    tool_use_result['originalFile'] = cached
    logger.debug(
        "Injected cached originalFile into tool_result (session=%s, line=%d, tool_use_id=%s, size=%d)",
        session_id, line_num, tool_use_id, len(cached),
    )
    return orjson.dumps(parsed).decode('utf-8')


def _update_parent_session_costs(parent_session_id: str) -> None:
    """
    Recalculate the parent session's costs from SessionItem data.

    Called when a subagent's cost changes. Uses recalculate_costs()
    which sums SessionItem.cost for both the session and its subagents.
    """
    try:
        parent = Session.objects.get(id=parent_session_id)
    except Session.DoesNotExist:
        return
    parent.recalculate_costs()
    parent.save(update_fields=["self_cost", "subagents_cost", "total_cost"])


def sync_session_items_from_file(
    session: "Session", file_path
) -> tuple[list[int], list[int], list[AgentLinkUpdate], list[ToolResultUpdate], list[AgentStoppedUpdate]]:
    """
    Synchronize session items from a JSONL file.

    Reads new lines from the file starting at last_offset.
    The session must already be saved to the database.

    Also handles session title updates:
    - First USER_MESSAGE sets initial title if not already set
    - SYSTEM items of type 'custom-title' update the title of their target session

    Returns:
        A tuple of:
        - List of line_nums of new items added (sorted)
        - List of line_nums of pre-existing items whose metadata was updated (sorted)
        - List of AgentLinkUpdate for agent state changes to broadcast
        - List of ToolResultUpdate for tool completion state changes to broadcast
        - List of AgentStoppedUpdate for subagents that naturally finished
    """
    if not file_path.exists():
        return [], [], [], [], []

    stat = file_path.stat()
    file_mtime = stat.st_mtime

    # If mtime hasn't changed and no new data appended, nothing to do.
    # Check file size too: mtime has ~1s resolution, so two writes within the same second
    # share the same mtime. Without the size check, the second write would be silently skipped.
    if session.mtime == file_mtime and session.last_offset >= stat.st_size:
        return [], [], [], [], []

    with open(file_path, "r", encoding="utf-8") as f:
        # Seek to last known position
        f.seek(session.last_offset)

        # Read remaining content
        new_content = f.read()
        if not new_content:
            # Update mtime even if no new content (file may have been touched)
            session.mtime = file_mtime
            session.save(update_fields=["mtime"])
            return [], [], [], [], []

        # Split into lines (filter out empty lines)
        lines = [line for line in new_content.split("\n") if line.strip()]

        # Capture file position and save offset+mtime immediately to release the file
        new_offset = f.tell()

    session.last_offset = new_offset
    session.mtime = file_mtime

    if not lines:
        session.save(update_fields=["last_offset", "mtime"])
        return [], [], [], [], []

    # Create SessionItem objects for bulk insert
    items_to_create: list[tuple[SessionItem, dict]] = []
    current_line_num = session.last_line

    # Track title updates (session_id -> title)
    session_title_updates: dict[str, str] = {}
    # Track if we've already set initial title for this session (from first user message ever)
    initial_title_needs_set = session.title is None

    # Track first timestamp in this batch (for session.created_at)
    first_timestamp: datetime | None = None

    # Track lifecycle timestamps for this batch
    last_started_at_update: datetime | None = None  # Set if a SessionStart hookEvent is found
    last_updated_at: datetime | None = None  # Last item timestamp in this batch
    last_new_content_at: datetime | None = None  # Last assistant message timestamp in this batch

    # Track last seen values for runtime environment fields
    first_cwd: str | None = None  # First cwd in this batch
    last_cwd: str | None = None
    last_cwd_git_branch: str | None = None
    last_model: str | None = None
    last_slug: str | None = None

    # Track agent link updates to broadcast after processing
    agent_link_updates: list[AgentLinkUpdate] = []
    # Track tool result updates to broadcast after processing
    tool_result_updates: list[ToolResultUpdate] = []
    # Track subagents that naturally finished
    agent_stopped_updates: list[AgentStoppedUpdate] = []

    # Track if a compact_summary item was found in this batch
    found_compact_summary = False

    # For subagents: track if we need to create the link between the agent and the parent session tool use
    subagent_needs_link = (
        session.type == SessionType.SUBAGENT
        and session.parent_session_id
        and not is_agent_link_done(session.parent_session_id, session.id)
    )

    # Load existing message_ids for deduplication of cost computation
    seen_message_ids: set[str] = set(
        SessionItem.objects.filter(
            session_id=session.id,
            message_id__isnull=False,
        ).values_list('message_id', flat=True)
    )

    for line in lines:
        line = line.strip()
        if not line:
            line = "{}"
        current_line_num += 1
        item = SessionItem(
            session=session,
            line_num=current_line_num,
            content=line,
        )
        try:
            parsed = orjson.loads(line)
        except orjson.JSONDecodeError:
            parsed = {}

        # Transform task-notification XML into standard tool_result format
        new_content = transform_task_notification(parsed)
        if new_content is None:
            # Transform local-command-stdout into assistant_message format
            new_content = transform_local_command_output(parsed)

        if new_content is not None:
            item.content = new_content

        # Inject cached originalFile into Edit/Write tool_results that lack it.
        # The PreToolUse hook captures file contents before the tool modifies them;
        # here we inject that cached content so the frontend gets full-file diffs.
        if is_tool_result_item(parsed):
            try:
                injected_content = _inject_cached_original_file(parsed, session.id, current_line_num)
                if injected_content is not None:
                    item.content = injected_content
            except Exception:
                logger.exception("Failed to inject cached originalFile (session=%s, line=%d)", session.id, current_line_num)

        # Pre-compute display_level (no group info yet)
        metadata = compute_item_metadata(parsed)
        item.display_level = metadata['display_level']
        item.kind = metadata['kind']

        # Extract timestamp
        item.timestamp = extract_item_timestamp(parsed)
        if first_timestamp is None and item.timestamp is not None:
            first_timestamp = item.timestamp

        # Track compact summary for session.compacted flag
        if item.kind == ItemKind.COMPACT_SUMMARY:
            found_compact_summary = True

        # Track lifecycle timestamps
        if item.timestamp is not None:
            last_updated_at = item.timestamp
        if item.timestamp is not None and item.kind == ItemKind.ASSISTANT_MESSAGE:
            last_new_content_at = item.timestamp
        # Detect SessionStart hookEvent to update last_started_at
        if (
            item.timestamp is not None
            and parsed.get('type') == 'progress'
            and isinstance(parsed.get('data'), dict)
            and parsed['data'].get('hookEvent') == 'SessionStart'
        ):
            last_started_at_update = item.timestamp

        # Compute cost and context usage (with deduplication)
        compute_item_cost_and_usage(item, parsed, seen_message_ids)

        items_to_create.append((item, parsed))

        # Extract runtime environment fields (keep last non-null value)
        if cwd := parsed.get('cwd'):
            if first_cwd is None:
                first_cwd = cwd
            last_cwd = cwd
        if cwd_git_branch := parsed.get('gitBranch'):
            last_cwd_git_branch = cwd_git_branch
        if (message := parsed.get('message')) and isinstance(message, dict):
            if model := message.get('model'):
                last_model = model
        if item_slug := parsed.get('slug'):
            last_slug = item_slug

        # Handle title extraction
        if item.kind == ItemKind.USER_MESSAGE and initial_title_needs_set:
            # First user message in this batch: set initial title
            title = extract_title_from_user_message(parsed)
            if title:
                session_title_updates[session.id] = title
                initial_title_needs_set = False

        # For subagents: create agent link from first user_message
        if subagent_needs_link and (agent_id := parsed.get('agentId')):
            prompt = get_cached_agent_prompt(session.parent_session_id, agent_id)
            if not prompt:
                # try to get from db
                if (first_user_message := session.items.filter(kind=ItemKind.USER_MESSAGE).first()) is not None:
                    try:
                        first_user_message_parsed = orjson.loads(first_user_message.content)
                    except orjson.JSONDecodeError:
                        pass
                    else:
                        prompt = extract_text_from_content(get_message_content(first_user_message_parsed))
                if not prompt:
                    # not in db so we may be the first one
                    if item.kind == ItemKind.USER_MESSAGE:
                        content = get_message_content(parsed)
                        prompt = extract_text_from_content(content)

                if prompt:
                    cache_agent_prompt(session.parent_session_id, agent_id, prompt)
                    agent_update = create_agent_link_from_subagent(
                        parent_session_id=session.parent_session_id,
                        agent_id=agent_id,
                        agent_prompt=prompt,
                    )
                    if agent_update:
                        agent_link_updates.append(agent_update)
                        subagent_needs_link = False

        if item.kind == ItemKind.SYSTEM and parsed.get('type') == 'custom-title':
            # Custom title: update the target session's title
            custom_title = parsed.get('customTitle')
            target_session_id = parsed.get('sessionId', session.id)
            if custom_title and isinstance(custom_title, str):
                session_title_updates[target_session_id] = custom_title

    # Bulk create all items
    items_only = [item for item, _ in items_to_create]
    SessionItem.objects.bulk_create(items_only, ignore_conflicts=True)

    # Track line_nums of new and updated items
    new_line_nums: set[int] = {item.line_num for item in items_only}
    modified_line_nums: set[int] = set()

    # Second pass: compute group membership, tool_result links, and update cost/usage/timestamp fields
    for item, parsed in items_to_create:
        # Build the update dict for this item (includes cost/usage/timestamp fields)
        update_fields = {
            'message_id': item.message_id,
            'cost': item.cost,
            'context_usage': item.context_usage,
            'timestamp': item.timestamp,
        }

        # Group membership for COLLAPSIBLE and ALWAYS items
        if item.display_level in (ItemDisplayLevel.COLLAPSIBLE, ItemDisplayLevel.ALWAYS):
            item_modified_lines = compute_item_metadata_live(session.id, item, parsed)
            modified_line_nums.update(item_modified_lines)
            update_fields['group_head'] = item.group_head
            update_fields['group_tail'] = item.group_tail
            update_fields['git_directory'] = item.git_directory
            update_fields['git_branch'] = item.git_branch

        # Update the item in DB with all computed fields
        SessionItem.objects.filter(
            session=session,
            line_num=item.line_num
        ).update(**update_fields)

        # Tool result links (tool_result items are DEBUG_ONLY)
        if is_tool_result_item(parsed):
            tool_result_update = create_tool_result_link_live(session.id, item, parsed)
            if tool_result_update:
                tool_result_updates.append(tool_result_update)
                # Check if this completes a subagent naturally
                if stopped := check_agent_naturally_stopped(session.id, tool_result_update):
                    agent_stopped_updates.append(stopped)
            # Also check for agent links (Task tool_result with agentId)
            if update := create_agent_link_from_tool_result(session.id, item, parsed):
                agent_link_updates.append(update)

        # For parent sessions: check if this assistant message contains Task tool_use(s)
        # and try to link them to existing subagents (handles the race condition where
        # the subagent was synced before this Task tool_use existed).
        # Note: Task tool_uses are often in CONTENT_ITEMS lines (streaming splits
        # the text and tool_use into separate lines, and tool_use-only lines have
        # no visible content so they're classified as CONTENT_ITEMS, not ASSISTANT_MESSAGE).
        if session.type == SessionType.SESSION and item.kind in (ItemKind.ASSISTANT_MESSAGE, ItemKind.CONTENT_ITEMS):
            agent_link_updates.extend(create_agent_link_from_tool_use(session.id, item, parsed))

    # Check if project needs git_root resolution
    # (a session item resolved git info but project has no git_root yet)
    if any(item.git_directory for item, _ in items_to_create) and get_project_git_root(session.project_id) is None:
        ensure_project_git_root(session.project_id)

    # Apply title updates (with protection against CLI stale re-appends)
    from .titles import check_protected_title, rename_session_in_jsonl

    for target_session_id, title in session_title_updates.items():
        result = check_protected_title(target_session_id, title)
        if result.should_apply:
            Session.objects.filter(id=target_session_id).update(title=title)
            if target_session_id == session.id:
                session.title = title
        elif result.correction:
            # CLI wrote a stale title — re-write the correct one.
            # This places the correct title at the end of the JSONL,
            # so the CLI's next tail-scan will absorb it.
            try:
                rename_session_in_jsonl(target_session_id, result.correction)
            except Exception:
                pass  # Will retry on next stale entry

    # Update session tracking fields
    session.last_line = current_line_num

    # Recompute user_message_count using the optimized index
    session.user_message_count = SessionItem.objects.filter(
        session=session,
        kind=ItemKind.USER_MESSAGE
    ).count()

    # Update session cost and context usage from the new items
    # Find last context_usage among new items (most recent non-null value)
    for item, _ in reversed(items_to_create):
        if item.context_usage is not None:
            session.context_usage = item.context_usage
            break

    # Recalculate costs from DB (idempotent)
    session.recalculate_costs()

    # Update runtime environment fields if changed
    if last_cwd and last_cwd != session.cwd:
        # Update project directory only on first sync (when session.cwd was None)
        # The first cwd of a session is the project directory (where Claude Code was launched)
        # Only for real sessions, not subagents (which may be launched from a different directory)
        if session.cwd is None and first_cwd and session.type == SessionType.SESSION:
            ensure_project_directory(session.project_id, first_cwd)
        session.cwd = last_cwd
    if last_cwd_git_branch and last_cwd_git_branch != session.cwd_git_branch:
        session.cwd_git_branch = last_cwd_git_branch
    if last_model and last_model != session.model:
        session.model = last_model
    if last_slug and last_slug != session.slug:
        session.slug = last_slug

    # Update resolved git directory/branch from the latest item that has one
    # (items are processed in order, so the last one wins)
    for item, _ in reversed(items_to_create):
        if item.git_directory:
            if item.git_directory != session.git_directory or item.git_branch != session.git_branch:
                session.git_directory = item.git_directory
                session.git_branch = item.git_branch
            break

    # Fallback: if no item provided git info, try resolving from the session's cwd.
    # This handles sessions where the agent only uses Bash (no tool_use with file paths),
    # so resolve_git_for_item has nothing to work with.
    if not session.git_directory and session.cwd:
        cwd_git = resolve_git_from_path(session.cwd, use_cache=False)
        if cwd_git:
            session.git_directory, session.git_branch = cwd_git

    # Validate git state: verify git_directory still exists on disk and refresh branch.
    # This catches Bash commands that modify git state (git checkout, worktree deletion, etc.).
    if session.git_directory:
        if os.path.isdir(session.git_directory):
            # Directory exists: refresh branch from HEAD in case of git checkout
            head_path = os.path.join(session.git_directory, '.git', 'HEAD')
            if not os.path.isfile(head_path):
                # Worktree: .git is a file, read gitdir path to find HEAD
                git_file = os.path.join(session.git_directory, '.git')
                if os.path.isfile(git_file):
                    try:
                        with open(git_file, 'r') as f:
                            content = f.read().strip()
                        if content.startswith('gitdir: '):
                            head_path = os.path.join(content[len('gitdir: '):], 'HEAD')
                    except OSError:
                        head_path = None
                else:
                    head_path = None
            if head_path:
                branch = read_head_branch(head_path)
                if branch and branch != session.git_branch:
                    session.git_branch = branch
        else:
            # git_directory no longer exists: re-resolve through fallback chain
            resolved = None
            if session.cwd and os.path.isdir(session.cwd):
                resolved = resolve_git_from_path(session.cwd, use_cache=False)
            if not resolved:
                project_git_root = get_project_git_root(session.project_id)
                if project_git_root and os.path.isdir(project_git_root):
                    # Already a resolved git root, re-read branch from it
                    head_path = os.path.join(project_git_root, '.git', 'HEAD')
                    branch = read_head_branch(head_path)
                    if branch:
                        resolved = (project_git_root, branch)
            if not resolved:
                project_directory = get_project_directory(session.project_id)
                if project_directory and os.path.isdir(project_directory):
                    resolved = resolve_git_from_path(project_directory, use_cache=False)
            if resolved:
                session.git_directory, session.git_branch = resolved
            else:
                session.git_directory = None
                session.git_branch = None

    is_new_session = session.created_at is None and first_timestamp is not None
    if is_new_session:
        session.created_at = first_timestamp

    # Update lifecycle timestamps
    if last_started_at_update is not None:
        session.last_started_at = last_started_at_update
    elif is_new_session:
        # First sync: initialize last_started_at to created_at
        session.last_started_at = first_timestamp
    if last_updated_at is not None:
        session.last_updated_at = last_updated_at
    if last_new_content_at is not None:
        session.last_new_content_at = last_new_content_at

    # Mark session as compacted if a compact_summary item was found
    if found_compact_summary and not session.compacted:
        session.compacted = True

    # Recalculate activity counters for affected days (only items that contribute)
    affected_days = {
        item.timestamp.date()
        for item, _ in items_to_create
        if item.timestamp and (item.kind == ItemKind.USER_MESSAGE or item.cost)
    }
    if is_new_session and session.type == SessionType.SESSION and first_timestamp:
        affected_days.add(first_timestamp.date())

    session.save(update_fields=["last_offset", "last_line", "mtime", "user_message_count", "context_usage", "self_cost", "subagents_cost", "total_cost", "cwd", "cwd_git_branch", "git_directory", "git_branch", "model", "slug", "created_at", "last_started_at", "last_updated_at", "last_new_content_at", "compacted"])

    # Recalculate activities after session.save (needs created_at in DB for session_count)
    from twicc.core.models import PeriodicActivity
    PeriodicActivity.recalculate_for_days(
        session.project_id, affected_days, provider=Provider.CLAUDE_CODE,
    )

    # If this is a subagent, propagate cost to parent session
    if session.type == SessionType.SUBAGENT and session.parent_session_id:
        _update_parent_session_costs(session.parent_session_id)

    # Exclude new items from modified_line_nums
    return sorted(new_line_nums), sorted(modified_line_nums - new_line_nums), agent_link_updates, tool_result_updates, agent_stopped_updates


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
        new_content = transform_task_notification(parsed_json)
        if new_content is None:
            new_content = transform_local_command_output(parsed_json)
        return new_content

    def compute_item_kind(self, parsed_json: dict) -> ItemKind | None:
        return compute_item_kind(parsed_json)

    def compute_item_display_level(
        self, parsed_json: dict, kind: ItemKind | None
    ) -> int:
        return compute_item_display_level(parsed_json, kind)

    def compute_item_metadata(self, parsed_json: dict) -> dict:
        return compute_item_metadata(parsed_json)

    def extract_item_timestamp(self, parsed_json: dict) -> datetime | None:
        return extract_item_timestamp(parsed_json)

    def extract_title_from_user_message(self, parsed_json: dict) -> str | None:
        return extract_title_from_user_message(parsed_json)

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
    ) -> None:
        compute_item_cost_and_usage(item, parsed_json, seen_message_ids)

    def is_tool_result_item(self, parsed_json: dict) -> bool:
        return is_tool_result_item(parsed_json)

    def extract_tool_use_entries(self, parsed_json: dict) -> dict[str, str]:
        return get_tool_use_entries(parsed_json)

    def extract_tool_result_info(self, parsed_json: dict) -> ToolResultInfo | None:
        tool_use_id = get_tool_result_id(parsed_json)
        if not tool_use_id:
            return None
        error_text = get_tool_result_error(parsed_json)
        return ToolResultInfo(
            tool_use_id=tool_use_id,
            is_error=error_text is not None,
            error_text=error_text,
        )

    def extract_agent_info_from_tool_result(
        self, parsed_json: dict
    ) -> tuple[str, str] | None:
        return get_tool_result_agent_info(parsed_json)

    def extract_task_tool_uses(self, parsed_json: dict) -> list[tuple[str, bool]]:
        return get_task_tool_uses(parsed_json)

    def extract_task_tool_use_prompts(
        self, parsed_json: dict
    ) -> list[tuple[str, str, bool]]:
        content = get_message_content_list(parsed_json, "assistant")
        if content is None:
            return []
        return _extract_task_tool_use_prompts(content)

    def extract_paths_from_tool_uses(self, parsed_json: dict) -> list[str]:
        return extract_paths_from_tool_uses(parsed_json)

    def compute_file_change_stats(self, parsed_json: dict) -> str | None:
        return compute_file_change_stats(parsed_json)

    def detect_prefix_suffix(
        self, parsed_json: dict, kind: ItemKind | None
    ) -> tuple[bool, bool]:
        return _detect_prefix_suffix(parsed_json, kind)

    def resolve_git_for_item(
        self, parsed_json: dict, *, use_cache: bool = True
    ) -> tuple[str, str] | None:
        return resolve_git_for_item(parsed_json, use_cache=use_cache)

    # ------------------------------------------------------------------
    # Live (watcher) machinery
    # ------------------------------------------------------------------

    def find_open_group_head(
        self, session_id: str, before_line_num: int
    ) -> int | None:
        return _find_open_group_head(session_id, before_line_num)

    def compute_item_metadata_live(
        self, session_id: str, item: SessionItem, parsed_json: dict
    ) -> set[int]:
        return compute_item_metadata_live(session_id, item, parsed_json)

    def create_tool_result_link_live(
        self, session_id: str, item: SessionItem, parsed_json: dict
    ) -> ToolResultUpdate | None:
        return create_tool_result_link_live(session_id, item, parsed_json)

    def check_agent_naturally_stopped(
        self, session_id: str, tool_result_update: ToolResultUpdate
    ) -> AgentStoppedUpdate | None:
        return check_agent_naturally_stopped(session_id, tool_result_update)

    def create_agent_link_from_tool_result(
        self, session_id: str, item: SessionItem, parsed_json: dict
    ) -> AgentLinkUpdate | None:
        return create_agent_link_from_tool_result(session_id, item, parsed_json)

    def create_agent_link_from_subagent(
        self,
        parent_session_id: str,
        agent_id: str,
        agent_prompt: str,
    ) -> AgentLinkUpdate | None:
        return create_agent_link_from_subagent(
            parent_session_id, agent_id, agent_prompt,
        )

    def create_agent_link_from_tool_use(
        self,
        session_id: str,
        item: SessionItem,
        parsed_json: dict,
    ) -> list[AgentLinkUpdate]:
        return create_agent_link_from_tool_use(session_id, item, parsed_json)

    # ------------------------------------------------------------------
    # Batch compute
    # ------------------------------------------------------------------

    def analyze_content(self, parsed_json: dict) -> ContentAnalysis:
        return analyze_content(parsed_json)

    def compute_session_metadata(self, session_id: str, result_queue) -> None:
        compute_session_metadata(session_id, result_queue)

    def apply_session_complete(self, msg: dict) -> None:
        apply_session_complete(msg)

    # ------------------------------------------------------------------
    # Watcher live sync
    # ------------------------------------------------------------------

    def sync_session_items_from_file(
        self,
        session: Session,
        file_path,
    ) -> tuple[
        list[int],
        list[int],
        list[AgentLinkUpdate],
        list[ToolResultUpdate],
        list[AgentStoppedUpdate],
    ]:
        return sync_session_items_from_file(session, file_path)


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
