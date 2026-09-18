"""Source import service tests (arXiv URL / DOI -> stored Source)."""

from radar.schemas import SourceRecord
from radar.services.source_import_service import SourceImportService


def _record():
    return SourceRecord(
        external_id="openalex:W1",
        title="Imported Paper",
        authors=["Alice"],
        abstract="An imported abstract about retrieval.",
        url="https://doi.org/10.1000/xyz",
        doi="10.1000/xyz",
        venue="NeurIPS",
        ccf_rank="A",
    )


def test_import_source_by_doi(db_session_factory, golden_case, monkeypatch):
    from radar.adapters.openalex import OpenAlexSearchAdapter

    monkeypatch.setattr(
        OpenAlexSearchAdapter,
        "get_work_by_doi",
        lambda self, doi: _record(),
    )

    result = SourceImportService(db_session_factory).import_source(
        golden_case, doi="10.1000/xyz"
    )
    assert result["title"] == "Imported Paper"
    assert result["doi"] == "10.1000/xyz"

    # Stored in DB.
    from sqlalchemy import select
    from radar.models import Source

    with db_session_factory() as session:
        source = session.scalar(select(Source).where(Source.external_id == "openalex:W1"))
        assert source is not None
        assert source.ccf_rank == "A"


def test_import_source_extracts_arxiv_id_from_url(db_session_factory, golden_case, monkeypatch):
    from radar.adapters.arxiv import ArxivSearchAdapter

    monkeypatch.setattr(
        ArxivSearchAdapter,
        "lookup",
        lambda self, ids: [_record()],
    )

    result = SourceImportService(db_session_factory).import_source(
        golden_case, url="https://arxiv.org/abs/2501.00001"
    )
    assert result["title"] == "Imported Paper"


def test_import_source_fails_when_unresolvable(db_session_factory, golden_case):
    import pytest

    with pytest.raises(ValueError):
        SourceImportService(db_session_factory).import_source(
            golden_case, url="https://example.com/not-a-paper"
        )
