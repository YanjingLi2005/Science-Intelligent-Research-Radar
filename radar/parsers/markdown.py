"""Small Markdown parser with deterministic section and block locators."""

import hashlib
import re
from pathlib import Path

from radar.parsers.base import sanitize_text, slugify
from radar.schemas import ParsedBlock, ParsedDocument, ParsedSection


def parse_markdown_document(path: Path, full_text: str) -> ParsedDocument:
    """Build a ParsedDocument from Markdown source text (already sanitized)."""

    sections: list[ParsedSection] = []
    current_title = "Document"
    current_lines: list[str] = []

    def flush() -> None:
        text = "\n".join(current_lines).strip()
        if text:
            sections.append(
                ParsedSection(title=current_title, locator=f"sec:{slugify(current_title)}", text=text)
            )

    for line in full_text.splitlines():
        match = re.match(r"^#{1,6}\s+(.+?)\s*$", line)
        if match:
            flush()
            current_title = match.group(1)
            current_lines = []
        else:
            current_lines.append(line)
    flush()
    if not sections:
        sections = [ParsedSection(title="Document", locator="sec:document", text=full_text)]

    paragraphs: list[ParsedBlock] = []
    sentences: list[ParsedBlock] = []
    for section in sections:
        for paragraph_no, paragraph in enumerate(re.split(r"\n\s*\n", section.text), start=1):
            paragraph = paragraph.strip()
            if not paragraph:
                continue
            paragraph_locator = f"{section.locator}:p:{paragraph_no}"
            paragraphs.append(ParsedBlock(text=paragraph, locator=paragraph_locator))
            for sentence_no, sentence in enumerate(re.split(r"(?<=[.!?])\s+", paragraph), start=1):
                if sentence.strip():
                    sentences.append(
                        ParsedBlock(text=sentence.strip(), locator=f"{paragraph_locator}:s:{sentence_no}")
                    )

    return ParsedDocument(
        path=path,
        full_text=full_text,
        sections=sections,
        paragraphs=paragraphs,
        sentences=sentences,
        content_hash=hashlib.sha256(full_text.encode("utf-8")).hexdigest(),
    )


class MarkdownParser:
    def parse(self, path: Path) -> ParsedDocument:
        full_text = sanitize_text(path.read_text(encoding="utf-8"))
        return parse_markdown_document(path, full_text)

