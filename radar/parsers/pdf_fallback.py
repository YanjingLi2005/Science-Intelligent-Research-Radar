"""Text-only PDF fallback; OCR is intentionally out of scope."""

import hashlib
import re
from pathlib import Path

from pypdf import PdfReader

from radar.parsers.base import sanitize_text
from radar.schemas import ParsedBlock, ParsedDocument, ParsedSection


class PdfFallbackParser:
    def parse(self, path: Path) -> ParsedDocument:
        reader = PdfReader(path)
        pages = [sanitize_text(page.extract_text() or "").strip() for page in reader.pages]
        full_text = "\n\n".join(pages)
        sections = [
            ParsedSection(title=f"Page {page_no}", locator=f"page:{page_no}", text=text)
            for page_no, text in enumerate(pages, start=1)
            if text
        ]
        paragraphs: list[ParsedBlock] = []
        sentences: list[ParsedBlock] = []
        for section in sections:
            for paragraph_no, paragraph in enumerate(re.split(r"\n\s*\n", section.text), start=1):
                if paragraph.strip():
                    locator = f"{section.locator}:p:{paragraph_no}"
                    paragraphs.append(ParsedBlock(text=paragraph.strip(), locator=locator))
                    for sentence_no, sentence in enumerate(re.split(r"(?<=[.!?])\s+", paragraph), start=1):
                        if sentence.strip():
                            sentences.append(
                                ParsedBlock(text=sentence.strip(), locator=f"{locator}:s:{sentence_no}")
                            )
        return ParsedDocument(
            path=path,
            full_text=full_text,
            sections=sections,
            paragraphs=paragraphs,
            sentences=sentences,
            content_hash=hashlib.sha256(full_text.encode("utf-8")).hexdigest(),
        )

