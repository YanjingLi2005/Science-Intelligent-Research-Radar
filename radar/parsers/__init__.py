"""Document parsers selected by file extension."""

from pathlib import Path

from radar.config import get_settings
from radar.parsers.base import DocumentParser
from radar.parsers.latex import LatexParser
from radar.parsers.markdown import MarkdownParser
from radar.parsers.pdf import PdfParser
from radar.parsers.pdf_docling import DoclingPdfParser
from radar.parsers.pdf_fallback import PdfFallbackParser


def parser_for(path: Path) -> DocumentParser:
    suffix = path.suffix.lower()
    if suffix == ".tex":
        return LatexParser()
    if suffix in {".md", ".markdown"}:
        return MarkdownParser()
    if suffix == ".pdf":
        # Docling is an optional extra; when it is not installed the backend
        # itself warns once and degrades to the pymupdf parser chain.
        if get_settings().pdf_parser_backend == "docling":
            return DoclingPdfParser()
        return PdfParser()
    raise ValueError(f"unsupported manuscript type: {suffix or 'unknown'}")


__all__ = [
    "DoclingPdfParser",
    "DocumentParser",
    "LatexParser",
    "MarkdownParser",
    "PdfFallbackParser",
    "PdfParser",
    "parser_for",
]
