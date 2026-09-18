"""Optional Docling PDF backend: dispatch, conversion, and fallback chain."""

import logging
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import radar.parsers as parsers_module
import radar.parsers.pdf_docling as pdf_docling_module
from radar.config import Settings
from radar.parsers import DoclingPdfParser, PdfParser, parser_for
from radar.schemas import ParsedDocument


def _settings(backend):
    return Settings(_env_file=None, pdf_parser_backend=backend)


def _fake_docling(monkeypatch, *, markdown=None, error=None):
    """Inject a fake docling package into sys.modules."""

    converter_module = types.ModuleType("docling.document_converter")

    class DocumentConverter:
        def convert(self, source):
            if error is not None:
                raise error
            document = SimpleNamespace(export_to_markdown=lambda: markdown)
            return SimpleNamespace(document=document)

    converter_module.DocumentConverter = DocumentConverter
    monkeypatch.setitem(sys.modules, "docling", types.ModuleType("docling"))
    monkeypatch.setitem(sys.modules, "docling.document_converter", converter_module)


def _force_docling_absent(monkeypatch):
    # A None entry in sys.modules makes any docling import raise ImportError.
    monkeypatch.setitem(sys.modules, "docling", None)
    monkeypatch.setattr(pdf_docling_module, "_warned_missing", False)


def _fallback_sentinel(monkeypatch):
    called = []

    def parse(self, path):
        called.append(path)
        return ParsedDocument(
            path=path,
            full_text="pymupdf chain output",
            sections=[],
            paragraphs=[],
            sentences=[],
            content_hash="fallback-hash",
        )

    monkeypatch.setattr(PdfParser, "parse", parse)
    return called


def test_pdf_parser_backend_defaults_to_pymupdf():
    assert Settings(_env_file=None).pdf_parser_backend == "pymupdf"


def test_parser_for_selects_pymupdf_by_default(monkeypatch):
    monkeypatch.setattr(
        parsers_module, "get_settings", lambda: _settings("pymupdf")
    )
    assert isinstance(parser_for(Path("paper.pdf")), PdfParser)


def test_parser_for_selects_docling_when_configured(monkeypatch):
    monkeypatch.setattr(parsers_module, "get_settings", lambda: _settings("docling"))
    assert isinstance(parser_for(Path("paper.pdf")), DoclingPdfParser)


def test_docling_converts_and_sanitizes_markdown(monkeypatch, tmp_path):
    _fake_docling(
        monkeypatch, markdown="# Results\n\nClean\x00 text with \x07controls."
    )
    document = DoclingPdfParser().parse(tmp_path / "paper.pdf")
    assert "\x00" not in document.full_text
    assert "\x07" not in document.full_text
    assert "Clean text with controls." in document.full_text
    # Markdown headings survive as structured sections.
    assert any(section.title == "Results" for section in document.sections)


def test_docling_missing_package_falls_back_with_single_warning(
    monkeypatch, tmp_path, caplog
):
    _force_docling_absent(monkeypatch)
    called = _fallback_sentinel(monkeypatch)
    parser = DoclingPdfParser()

    with caplog.at_level(logging.WARNING, logger="radar.parsers.pdf_docling"):
        first = parser.parse(tmp_path / "a.pdf")
        second = parser.parse(tmp_path / "b.pdf")

    assert first.full_text == second.full_text == "pymupdf chain output"
    assert called == [tmp_path / "a.pdf", tmp_path / "b.pdf"]
    warnings = [
        record for record in caplog.records if "docling is not installed" in record.msg
    ]
    assert len(warnings) == 1


def test_docling_conversion_failure_falls_back(monkeypatch, tmp_path):
    _fake_docling(monkeypatch, error=RuntimeError("docling boom"))
    called = _fallback_sentinel(monkeypatch)
    document = DoclingPdfParser().parse(tmp_path / "paper.pdf")
    assert document.full_text == "pymupdf chain output"
    assert called == [tmp_path / "paper.pdf"]


def test_docling_blank_output_falls_back(monkeypatch, tmp_path):
    _fake_docling(monkeypatch, markdown="  \n ")
    called = _fallback_sentinel(monkeypatch)
    document = DoclingPdfParser().parse(tmp_path / "paper.pdf")
    assert document.full_text == "pymupdf chain output"
    assert called == [tmp_path / "paper.pdf"]
