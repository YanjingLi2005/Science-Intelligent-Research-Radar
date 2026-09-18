"""Parser sanitization, primary/fallback PDF paths, and Markdown quote matching."""

import hashlib
from pathlib import Path

import pytest

from radar.parsers import PdfFallbackParser, PdfParser, parser_for
from radar.parsers.base import sanitize_text
from radar.parsers.latex import LatexParser
from radar.parsers.markdown import MarkdownParser
from radar.schemas import EvidenceSpan
from radar.services.evidence_service import EvidenceService


REAL_PDF = Path(__file__).resolve().parents[1] / "data" / "papers" / "RARE_2506.00789.pdf"


def test_sanitize_text_strips_nul_and_control_characters():
    dirty = "alpha\x00beta\x07gamma\x1fdelta\nepsilon\tzeta\r\neta\x7f"
    assert sanitize_text(dirty) == "alphabetagammadelta\nepsilon\tzeta\neta"


def test_sanitize_text_keeps_plain_text_unchanged():
    text = "Plain English text with punctuation!? 123\n\nSecond paragraph."
    assert sanitize_text(text) == text


@pytest.mark.parametrize("parser", [LatexParser(), MarkdownParser()])
def test_text_parsers_sanitize_control_characters(tmp_path, parser):
    suffix = ".tex" if isinstance(parser, LatexParser) else ".md"
    manuscript = tmp_path / f"manuscript{suffix}"
    manuscript.write_text(
        "A clean claim\x00 with a stray byte.\n\nAnother paragraph follows.",
        encoding="utf-8",
    )
    document = parser.parse(manuscript)
    assert "\x00" not in document.full_text
    assert all("\x00" not in block.text for block in document.paragraphs)
    assert (
        document.content_hash
        == hashlib.sha256(document.full_text.encode("utf-8")).hexdigest()
    )


def test_parser_for_dispatches_pdf_to_primary_parser():
    assert isinstance(parser_for(Path("paper.pdf")), PdfParser)


@pytest.mark.skipif(not REAL_PDF.exists(), reason="demo PDF not available")
def test_pdf_parser_extracts_markdown_from_real_pdf():
    document = PdfParser().parse(REAL_PDF)
    assert len(document.full_text) > 10_000
    assert "\x00" not in document.full_text
    assert all("\x00" not in block.text for block in document.paragraphs)
    # PyMuPDF4LLM preserves document structure as Markdown headings.
    assert "#" in document.full_text
    assert document.sections
    assert document.sentences


@pytest.mark.skipif(not REAL_PDF.exists(), reason="demo PDF not available")
def test_pdf_parser_falls_back_to_pypdf_when_markdown_fails(monkeypatch):
    def broken_markdown(path):
        raise RuntimeError("injected pymupdf4llm failure")

    monkeypatch.setattr("radar.parsers.pdf.pymupdf4llm.to_markdown", broken_markdown)
    document = PdfParser().parse(REAL_PDF)
    # Fallback output uses the pypdf page locators instead of Markdown sections.
    assert document.full_text.strip()
    assert "\x00" not in document.full_text
    assert all(section.locator.startswith("page:") for section in document.sections)


def test_pdf_parser_falls_back_when_markdown_is_empty(monkeypatch, tmp_path):
    monkeypatch.setattr("radar.parsers.pdf.pymupdf4llm.to_markdown", lambda path: "  ")
    original_parse = PdfFallbackParser.parse
    called = []
    monkeypatch.setattr(
        PdfFallbackParser,
        "parse",
        lambda self, path: called.append(path) or original_parse(self, path),
    )
    blank_pdf = tmp_path / "blank.pdf"
    blank_pdf.write_bytes(b"%PDF-1.4\n")
    try:
        PdfParser().parse(blank_pdf)
    except Exception:
        # pypdf may reject the stub file; only the fallback dispatch matters.
        pass
    assert called == [blank_pdf]


def test_resolve_exact_matches_quote_without_markdown_emphasis():
    content = (
        "# Results\n\n"
        "Our method achieves **70.1 exact match** on the unseen-domain split, "
        "outperforming the baseline."
    )
    span = EvidenceSpan(
        quote="Our method achieves 70.1 exact match on the unseen-domain split",
        locator="paragraph:1",
    )
    resolved = EvidenceService.resolve_exact(span, content)
    assert resolved is not None
    assert "70.1 exact match" in resolved.quote
    assert resolved.quote in content
