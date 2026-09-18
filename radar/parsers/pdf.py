"""Primary PDF parser: PyMuPDF4LLM Markdown extraction with a pypdf fallback.

Markdown output preserves headings and emphasis, which is friendlier to the
downstream LLM stages than the flat multi-column text pypdf produces. Any
extraction failure (import error, unreadable file, empty text) degrades to
the text-only PdfFallbackParser instead of aborting the import.
"""

from pathlib import Path

from radar.parsers.base import sanitize_text
from radar.parsers.markdown import parse_markdown_document
from radar.parsers.pdf_fallback import PdfFallbackParser
from radar.schemas import ParsedDocument

try:
    import pymupdf4llm
except ImportError:  # pragma: no cover - depends on the environment
    pymupdf4llm = None


class PdfParser:
    def parse(self, path: Path) -> ParsedDocument:
        markdown = self._extract_markdown(path)
        if markdown is not None:
            return parse_markdown_document(path, markdown)
        return PdfFallbackParser().parse(path)

    @staticmethod
    def _extract_markdown(path: Path) -> str | None:
        """Return sanitized Markdown text, or None when extraction fails."""

        if pymupdf4llm is None:
            return None
        try:
            text = pymupdf4llm.to_markdown(str(path))
        except Exception:
            return None
        text = sanitize_text(text or "")
        return text if text.strip() else None
