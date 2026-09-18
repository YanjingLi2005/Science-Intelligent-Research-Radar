"""Scan filter preferences: CCF rank and arXiv category filtering."""

from sqlalchemy import select

from radar.config import Settings
from radar.models import ResearchCase
from radar.schemas import SourceRecord
from radar.services.weekly_radar_service import WeeklyRadarService


def _settings():
    return Settings(_env_file=None)


def _record(external_id, *, ccf_rank=None, arxiv_category=None):
    return SourceRecord(
        external_id=external_id,
        title=external_id,
        authors=[],
        abstract="abstract",
        url="http://example.com",
        ccf_rank=ccf_rank,
        arxiv_primary_category=arxiv_category,
    )


def test_weekly_radar_applies_ccf_rank_filter(db_session_factory):
    case_id = "filter-case"
    with db_session_factory() as session:
        session.add(
            ResearchCase(
                id=case_id,
                title="Filter",
                research_question="?",
                settings_json={"scan_filters": {"ccf_rank": "A", "arxiv_categories": []}},
            )
        )
        session.commit()

    service = WeeklyRadarService(
        db_session_factory,
        search_adapter=None,
        llm_client=None,
        settings=_settings(),
    )
    records = [
        _record("arxiv:a", ccf_rank="A", arxiv_category="cs.AI"),
        _record("arxiv:b", ccf_rank="B", arxiv_category="cs.AI"),
        _record("arxiv:none", ccf_rank=None, arxiv_category="cs.CL"),
    ]
    filtered = service._apply_scan_filters(case_id, records)
    assert [r.external_id for r in filtered] == ["arxiv:a"]


def test_weekly_radar_applies_arxiv_category_filter(db_session_factory):
    case_id = "filter-case-cat"
    with db_session_factory() as session:
        session.add(
            ResearchCase(
                id=case_id,
                title="Filter",
                research_question="?",
                settings_json={
                    "scan_filters": {"ccf_rank": None, "arxiv_categories": ["cs.CL"]}
                },
            )
        )
        session.commit()

    service = WeeklyRadarService(
        db_session_factory,
        search_adapter=None,
        llm_client=None,
        settings=_settings(),
    )
    records = [
        _record("arxiv:a", ccf_rank="A", arxiv_category="cs.AI"),
        _record("arxiv:b", ccf_rank=None, arxiv_category="cs.CL"),
        _record("arxiv:no-cat", ccf_rank=None, arxiv_category=None),
    ]
    filtered = service._apply_scan_filters(case_id, records)
    assert [r.external_id for r in filtered] == ["arxiv:b"]
