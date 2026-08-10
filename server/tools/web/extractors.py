"""Local content extraction for fetched documents: HTML to Markdown, text, JSON."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from bs4 import BeautifulSoup
from markdownify import markdownify

_DROP_TAGS = (
    "script",
    "style",
    "noscript",
    "template",
    "iframe",
    "svg",
    "form",
    "nav",
    "header",
    "footer",
    "aside",
)

_WHITESPACE_RUN = re.compile(r"[ \t]+")
_BLANK_RUN = re.compile(r"\n{3,}")


@dataclass(frozen=True)
class ExtractedBody:
    title: str
    text: str
    extractor: str
    warnings: tuple[str, ...] = ()


def normalize_whitespace(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _WHITESPACE_RUN.sub(" ", text)
    text = _BLANK_RUN.sub("\n\n", text)
    return text.strip()


def extract_html(html: str, *, as_markdown: bool = True) -> ExtractedBody:
    soup = BeautifulSoup(html, "html.parser")
    title = ""
    if soup.title is not None:
        title = soup.title.get_text(strip=True)
    for tag_name in _DROP_TAGS:
        for tag in soup.find_all(tag_name):
            tag.decompose()
    root = soup.find("article") or soup.find("main") or soup.body or soup
    if as_markdown:
        text = markdownify(str(root), heading_style="ATX", strip=["img"])
    else:
        text = root.get_text("\n", strip=True)
    return ExtractedBody(title=title, text=normalize_whitespace(text), extractor="html")


def extract_text(raw: str) -> ExtractedBody:
    return ExtractedBody(title="", text=normalize_whitespace(raw), extractor="text")


def extract_json(raw: str) -> ExtractedBody:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as error:
        body = extract_text(raw)
        return ExtractedBody(
            title="",
            text=body.text,
            extractor="text",
            warnings=(f"JSON 解析失败，已按纯文本返回: {error.msg}",),
        )
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    return ExtractedBody(title="", text=normalize_whitespace(text), extractor="json")
