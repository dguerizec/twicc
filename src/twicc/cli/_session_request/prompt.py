"""Resolve the positional ``PROMPT`` argument.

If the value points to an existing file (absolute or relative), read its
UTF-8 content. Otherwise treat the value as the prompt text.
"""

from __future__ import annotations

import os


class PromptError(Exception):
    pass


def resolve_prompt(prompt_arg: str) -> str:
    if os.path.isfile(prompt_arg):
        try:
            with open(prompt_arg, "r", encoding="utf-8") as f:
                text = f.read()
        except UnicodeDecodeError as e:
            raise PromptError(f"prompt: file {prompt_arg!r} is not valid UTF-8: {e}")
        if not text.strip():
            raise PromptError(f"prompt: file {prompt_arg!r} is empty")
        return text
    if not prompt_arg.strip():
        raise PromptError("prompt is empty")
    return prompt_arg
