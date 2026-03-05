# Reclassify /clear command items from user_message to system kind,
# and update session/project metadata accordingly.

import json
import re
from collections import Counter

from django.db import migrations
from django.db.models import F


TITLE_MAX_LENGTH = 200

_MARKDOWN_PATTERNS = [
    (re.compile(r'^#{1,6}\s+', re.MULTILINE), ''),
    (re.compile(r'\*\*(.+?)\*\*'), r'\1'),
    (re.compile(r'__(.+?)__'), r'\1'),
    (re.compile(r'\*(.+?)\*'), r'\1'),
    (re.compile(r'_(.+?)_'), r'\1'),
    (re.compile(r'~~(.+?)~~'), r'\1'),
    (re.compile(r'`(.+?)`'), r'\1'),
    (re.compile(r'^\s*[-*+]\s+', re.MULTILINE), ''),
    (re.compile(r'^\s*\d+\.\s+', re.MULTILINE), ''),
    (re.compile(r'^\s*>\s*', re.MULTILINE), ''),
    (re.compile(r'\[([^\]]+)\]\([^)]+\)'), r'\1'),
]


def _strip_markdown(text):
    for pattern, replacement in _MARKDOWN_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def _extract_title_from_content(raw_json):
    """Extract a title from a session item's raw JSON content string.

    Mirrors the logic from compute.extract_title_from_user_message.
    """
    try:
        parsed = json.loads(raw_json)
    except (json.JSONDecodeError, TypeError):
        return None

    message = parsed.get('message')
    if not isinstance(message, dict):
        return None

    content = message.get('content')
    if not content:
        return None

    # Extract text from content
    text = None
    if isinstance(content, str):
        text = content.strip()
    elif isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get('type') == 'text':
                t = block.get('text')
                if isinstance(t, str):
                    text = t.strip()
                    break

    if not text:
        return None

    # Handle command messages (e.g., /compact with args)
    if text.startswith('<command-'):
        try:
            import xmltodict

            xml_parsed = xmltodict.parse(f'<root>{text}</root>')
            root = xml_parsed.get('root', {})
            name = root.get('command-name')
            if name:
                cleaned = name
                args = root.get('command-args')
                if args:
                    cleaned += f' {_strip_markdown(args)}'
            else:
                cleaned = _strip_markdown(text).strip()
        except Exception:
            cleaned = _strip_markdown(text).strip()
    else:
        cleaned = _strip_markdown(text).strip()

    # Collapse whitespace
    cleaned = re.sub(r'\s+', ' ', cleaned)

    if not cleaned:
        return None

    if len(cleaned) > TITLE_MAX_LENGTH:
        return cleaned[:TITLE_MAX_LENGTH] + '…'

    return cleaned


def reclassify_clear_commands(apps, schema_editor):
    """Reclassify /clear command items from user_message to system kind."""
    SessionItem = apps.get_model('core', 'SessionItem')
    Session = apps.get_model('core', 'Session')
    Project = apps.get_model('core', 'Project')

    # Find affected items: single content__contains scan, collect IDs and count per session
    affected = SessionItem.objects.filter(
        kind='user_message',
        content__contains='<command-name>/clear</command-name>',
    ).values_list('id', 'session_id')

    item_ids = []
    session_counts = Counter()
    for item_id, session_id in affected:
        item_ids.append(item_id)
        session_counts[session_id] += 1

    if not item_ids:
        return

    # Bulk update kind to system (by IDs, no second content scan)
    SessionItem.objects.filter(id__in=item_ids).update(kind='system')

    # Update each affected session
    for session_id, clear_count in session_counts.items():
        try:
            session = Session.objects.get(id=session_id)
        except Session.DoesNotExist:
            continue

        session.user_message_count = max(0, session.user_message_count - clear_count)

        if session.user_message_count == 0:
            session.title = None
            session.save(update_fields=['user_message_count', 'title'])
            # Decrement project sessions_count
            if session.project_id:
                Project.objects.filter(
                    id=session.project_id, sessions_count__gt=0,
                ).update(sessions_count=F('sessions_count') - 1)

        elif session.title and session.title.startswith('/clear'):
            # Title was from a /clear command — replace with first real user message
            first_user_item = SessionItem.objects.filter(
                session_id=session_id,
                kind='user_message',
            ).order_by('line_num').first()

            if first_user_item:
                session.title = _extract_title_from_content(first_user_item.content)
            else:
                session.title = None
            session.save(update_fields=['user_message_count', 'title'])

        else:
            session.save(update_fields=['user_message_count'])


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0043_project_archived'),
    ]

    operations = [
        migrations.RunPython(reclassify_clear_commands, migrations.RunPython.noop),
    ]
