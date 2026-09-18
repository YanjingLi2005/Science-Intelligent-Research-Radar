"""Optional Docling PDF backend with the primary parser as fallback.

Docling preserves tables and formulas better than the default pymupdf4llm
path, but it pulls in heavyweight dependencies (torch), so it is an optional
extra: ``pip install '.[docling]'`` plus ``PDF_PARSER_BACKEND=docling``.
Every failure — package missing, conversion error, blank output — degrades
to the primary PdfParser (pymupdf4llm -> pypdf) instead of aborting the
import.
"""

import logging
from pathlib import Path

from radar.parsers.base import sanitize_text
from radar.parsers.markdown import parse_markdown_document
from radar.parsers.pdf import PdfParser
from radar.schemas import ParsedDocument

logger = logging.getLogger(__name__)

# The missing-package warning is emitted once per process, not per file.
_warned_missing = False


def _document_converter_class():
    """Return docling's DocumentConverter, or None when docling is absent."""

    global _warned_missing
    try:
        from docling.document_converter import DocumentConverter
    except ImportError:
        if not _warned_missing:
            logger.warning(
                "PDF_PARSER_BACKEND=docling but docling is not installed; "
                "falling back to pymupdf (pip install '.[docling]')."
            )
            _warned_missing = True
        return None
    return DocumentConverter


class DoclingPdfParser:
    """Convert PDFs to Markdown through Docling, degrading to PdfParser."""

    def parse(self, path: Path) -> ParsedDocument:
        markdown = self._extract_markdown(path)
        if markdown is not None:
            return parse_markdown_document(path, markdown)
        return PdfParser().parse(path)

    @staticmethod
    def _extract_markdown(path: Path) -> str | None:
        """Return sanitized Markdown text, or None when conversion fails."""

        converter_class = _document_converter_class()
        if converter_class is None:
            return None
        try:
            result = converter_class().convert(str(path))
            text = result.document.export_to_markdown()
        except Exception:
            logger.warning(
                "docling conversion failed for %s; falling back to pymupdf", path
            )
            return None
        text = sanitize_text(text or "")
        return text if text.strip() else None
