"""Unit tests for LaTeX citation styling, BibTeX generation, unified diff, and apply local."""

from datetime import datetime, timezone
import uuid
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import select

from radar.api import app
from radar.db import session_scope
from radar.models import (
    AuditEvent,
    ClaimRevision,
    ImpactCandidate,
    ManuscriptVersion,
    PatchProposal,
    ResearchCase,
    Source,
)
from radar.services.patch_service import PatchService
from radar.services.review_service import ReviewService


def test_detect_citation_style():
    # natbib package in preamble
    tex_natbib = r"\documentclass{article}\usepackage{natbib}\begin{document}Hello\end{document}"
    assert PatchService.detect_citation_style(tex_natbib) == "natbib_citep"

    # natbib macros used
    tex_macros = r"As shown by \citet{smith2024} and previous findings \citep{jones2023}."
    assert PatchService.detect_citation_style(tex_macros) == "natbib_citep"

    # standard cite
    tex_standard = r"Prior work \cite{ref1} shows improvement."
    assert PatchService.detect_citation_style(tex_standard) == "cite"

    # empty content
    assert PatchService.detect_citation_style("") == "cite"


def test_generate_bibtex_key_and_entry():
    source = Source(
        id=f"src-{uuid.uuid4().hex[:6]}",
        title="DomainQA: A Benchmark for Open-Domain Question Answering",
        source_kind="arxiv",
        external_id="arxiv:2601.0001",
        url="https://arxiv.org/abs/2601.0001",
        arxiv_id="2601.0001",
        authors_json=["Alice Wang", "Bob Smith"],
        published_at=datetime(2026, 4, 15, tzinfo=timezone.utc),
        venue="ICLR 2026",
        doi="10.1145/example.doi",
    )
    key = PatchService.generate_bibtex_key(source)
    assert key == "wang2026domainqa"

    entry = PatchService.generate_bibtex_entry(source, key)
    assert f"@article{{{key}," in entry
    assert "author = {Alice Wang and Bob Smith}" in entry
    assert "journal = {ICLR 2026}" in entry
    assert "doi = {10.1145/example.doi}" in entry
    assert "eprint = {2601.0001}" in entry


def test_append_bibtex_entries_deduplication_and_collision():
    existing_bib = """@article{wang2026domainqa,
  title = {DomainQA: A Benchmark for Open-Domain Question Answering},
  author = {Alice Wang},
  year = {2026}
}"""
    # 1. Appending existing key with duplicate title skips duplicate
    entries_dup = [("wang2026domainqa", existing_bib)]
    updated, added = PatchService.append_bibtex_entries(existing_bib, entries_dup)
    assert len(added) == 0
    assert updated == existing_bib

    # 2. Collision with a DIFFERENT paper having same key -> gets suffix a
    entries_collision = [
        ("wang2026domainqa", "@article{wang2026domainqa,\n  title = {Different Paper}\n}")
    ]
    updated_col, added_col = PatchService.append_bibtex_entries(existing_bib, entries_collision)
    assert len(added_col) == 1
    assert added_col[0] == "wang2026domainqaa"
    assert "@article{wang2026domainqaa," in updated_col


def test_resolve_patch_citations():
    service = PatchService(None)
    source = Source(
        id=f"src-{uuid.uuid4().hex[:6]}",
        title="DomainQA Benchmark",
        source_kind="arxiv",
        external_id="arxiv:2601.0002",
        url="https://arxiv.org/abs/2601.0002",
        authors_json=["John Miller"],
        published_at=datetime(2026, 3, 1, tzinfo=timezone.utc),
    )
    after_text = "Our method outperforms previous architectures [CITATION]."

    # Natbib mode
    resolved_natbib, entries = service.resolve_patch_citations(
        after_text, [source], r"\usepackage{natbib}"
    )
    assert r"\citep{miller2026domainqa}" in resolved_natbib
    assert len(entries) == 1

    # Standard cite mode
    resolved_cite, _ = service.resolve_patch_citations(
        after_text, [source], "Standard LaTeX without natbib"
    )
    assert r"\cite{miller2026domainqa}" in resolved_cite


def test_export_case_unified_diff_and_apply_local(db_session_factory, golden_case):
    ReviewService(db_session_factory).confirm_impact("impact-01")
    patch_service = PatchService(db_session_factory)
    patch = patch_service.generate_patch("impact-01")
    patch_service.approve_patch(patch.id)

    # 1. Export unified diff
    export_data = patch_service.export_case_unified_diff(golden_case)
    assert "filename" in export_data
    assert export_data["filename"].endswith(".patch")
    assert "diff" in export_data
    assert export_data["patch_count"] >= 1
    assert "--- a/" in export_data["diff"]
    assert "+++ b/" in export_data["diff"]

    # 2. Apply to local file / manuscript in DB
    apply_result = patch_service.apply_case_patches_to_local(golden_case)
    assert apply_result["success"] is True
    assert apply_result["applied_count"] >= 1

    # Verify manuscript content in DB is updated
    with session_scope(db_session_factory) as session:
        manuscript = session.scalar(
            select(ManuscriptVersion).where(
                ManuscriptVersion.case_id == golden_case,
                ManuscriptVersion.is_current.is_(True),
            )
        )
        assert apply_result["applied_count"] == 1
        assert "A concurrent reproduction reports" in manuscript.content_text

        # Verify audit event
        audit = session.scalar(
            select(AuditEvent).where(
                AuditEvent.case_id == golden_case,
                AuditEvent.event_type == "patches_applied_local",
            )
        )
        assert audit is not None
        assert audit.payload_json["applied_count"] >= 1


def test_api_endpoints_unified_diff_and_apply_local(db_session_factory, golden_case, monkeypatch):
    import radar.api as api_module
    import radar.db as db_module
    import radar.routes.deps as deps_module
    monkeypatch.setattr(db_module, "SessionLocal", db_session_factory)
    monkeypatch.setattr(api_module, "SessionLocal", db_session_factory)
    monkeypatch.setattr(deps_module, "get_session_factory", lambda: db_session_factory)

    ReviewService(db_session_factory).confirm_impact("impact-01")
    patch_service = PatchService(db_session_factory)
    patch = patch_service.generate_patch("impact-01")
    patch_service.approve_patch(patch.id)

    client = TestClient(app)

    # GET /api/cases/{case_id}/patches/unified-diff
    res_diff = client.get(f"/api/cases/{golden_case}/patches/unified-diff")
    assert res_diff.status_code == 200
    data_diff = res_diff.json()
    assert "diff" in data_diff
    assert "filename" in data_diff
    assert "patch_count" in data_diff
    assert data_diff["patch_count"] >= 1

    # POST /api/cases/{case_id}/patches/apply-local
    res_apply = client.post(f"/api/cases/{golden_case}/patches/apply-local")
    assert res_apply.status_code == 200
    data_apply = res_apply.json()
    assert data_apply["success"] is True
    assert data_apply["applied_count"] >= 1
