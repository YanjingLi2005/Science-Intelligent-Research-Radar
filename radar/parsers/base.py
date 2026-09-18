"""Parser protocol and shared stable-locator helpers."""

import re
from pathlib import Path
from typing import Protocol

from radar.schemas import ParsedDocument


class DocumentParser(Protocol):
    def parse(self, path: Path) -> ParsedDocument: ...


# Control characters other than \n and \t. SQLite text columns reject NUL
# bytes and PDF extractors occasionally emit stray control characters, so
# every parser runs its output through sanitize_text before hashing/storing.
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def sanitize_text(text: str) -> str:
    """Strip NUL bytes and control characters, keeping newlines and tabs."""

    return _CONTROL_CHARACTERS.sub("", text)


def slugify(value: str) -> str:
    cleaned = "".join(character.lower() if character.isalnum() else "-" for character in value)
    return "-".join(part for part in cleaned.split("-") if part) or "document"

