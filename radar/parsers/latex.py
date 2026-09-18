"""Conservative LaTeX parser preserving exact source spans."""

import hashlib
import re
from pathlib import Path

from radar.parsers.base import sanitize_text, slugify
from radar.schemas import ParsedBlock, ParsedDocument, ParsedSection


SECTION_PATTERN = re.compile(r"\\(?:section|subsection|subsubsection)\*?\{([^{}]+)\}")


class LatexParser:
    def parse(self, path: Path) -> ParsedDocument:
        full_text = sanitize_text(path.read_text(encoding="utf-8"))
        matches = list(SECTION_PATTERN.finditer(full_text))
        sections: list[ParsedSection] = []
        if not matches:
            sections.append(ParsedSection(title="Document", locator="sec:document", text=full_text))
        else:
            for index, match in enumerate(matches):
                end = matches[index + 1].start() if index + 1 < len(matches) else len(full_text)
                sections.append(
                    ParsedSection(
                        title=match.group(1),
                        locator=f"sec:{slugify(match.group(1))}",
                        text=full_text[match.end():end].strip(),
                    )
                )

        paragraphs: list[ParsedBlock] = []
        sentences: list[ParsedBlock] = []
        for section in sections:
            without_comments = "\n".join(line.split("%", 1)[0] for line in section.text.splitlines())
            for paragraph_no, paragraph in enumerate(re.split(r"\n\s*\n", without_comments), start=1):
                paragraph = paragraph.strip()
                if not paragraph or paragraph.startswith("\\") and "{" not in paragraph:
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

