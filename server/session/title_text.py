"""Shared conversation/title text cleaning.

Both the sidebar first-question fallback (``session_service``) and the LLM
summarizer (``summary``) used to carry their own copies of the preamble
stripping and truncation rules, which drift apart over time.  This module is
the single source of truth so the two stay consistent.
"""

from __future__ import annotations

import re

# Trim the learning-context preamble so it does not pollute titles/prompts.
LEARNING_CONTEXT_RE = re.compile(r"^<!-- nlp-learning-context:.*? -->\s*", re.S)
LEARNING_SETTING_RE = re.compile(r"^\[学习设置：.*?\]\s*", re.S)
# Everything from the attachment marker on is upload metadata, not conversation.
ATTACHMENT_BLOCK_RE = re.compile(r"\s*---附件---.*$", re.S)

MAX_TITLE_CHARS = 15


def strip_learning_preamble(text: str) -> str:
    """Remove the learning-context comment, setting line and attachment block."""
    text = LEARNING_CONTEXT_RE.sub("", text)
    text = LEARNING_SETTING_RE.sub("", text)
    text = ATTACHMENT_BLOCK_RE.sub("", text)
    return text


def normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def strip_markdown_noise(text: str) -> str:
    """Drop residual HTML comments and light markdown markers."""
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    text = re.sub(r"[#*_`]", "", text)
    return text


def clean_title(
    text: str,
    *,
    max_chars: int = MAX_TITLE_CHARS,
    ellipsis: bool = False,
) -> str:
    """Produce a short, plain title from raw user/LLM text."""
    text = strip_learning_preamble(text)
    text = strip_markdown_noise(text)
    text = text.strip().strip("\"'“”‘’「」")
    text = re.sub(r"^[#*\-\s]+", "", text)
    text = re.sub(r"[#*\-\s]+$", "", text)
    text = normalize_whitespace(text)
    if ellipsis and len(text) > max_chars:
        return f"{text[:max_chars]}…"
    return text[:max_chars]
