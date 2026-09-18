"""Unit tests for academic reputation, influence enrichment, and urgency boost."""

import httpx
import pytest

from radar.api_schemas import PaperOut
from radar.services.impact_service import ImpactService
from radar.services.retrieval_service import TOP_TIER_INSTITUTIONS, RetrievalService


def _service() -> RetrievalService:
    # The enrichment methods are pure enough to bypass DB-backed __init__.
    return object.__new__(RetrievalService)


class _FakeResponse:
    def __init__(self, payload, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


_S2_PAYLOAD = {
    "authors": [
        {"name": "Jane Doe", "hIndex": 42, "affiliations": ["Stanford University"]},
        {"name": "John Smith", "hIndex": 55, "affiliations": ["Google DeepMind"]},
    ],
    "citationCount": 100,
    "citationVelocity": 12.5,
}


class _FakeS2Client:
    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def get(self, url, params=None):
        return _FakeResponse(_S2_PAYLOAD)


def test_enrich_reputation_and_influence_with_semantic_scholar(monkeypatch):
    monkeypatch.setattr(httpx, "Client", _FakeS2Client)
    result = _service().enrich_reputation_and_influence(
        authors=["Jane Doe", "John Smith"],
        title="A Robust Retrieval Study",
        doi="10.1000/test",
        cited_by_count=100,
        published_at="2026-01-01",
    )

    assert result["author_profile"]["first_author"] == "Jane Doe"
    assert result["author_profile"]["corresponding_author"] == "John Smith"
    assert result["author_profile"]["top_h_index"] == 55
    assert result["author_profile"]["is_top_tier"] is True
    assert set(result["author_profile"]["affiliations"]) >= {
        "Stanford University",
        "Google DeepMind",
    }
    assert result["influence_metrics"]["citation_velocity"] == 12.5
    assert result["influence_metrics"]["top_h_index"] == 55
    assert result["influence_metrics"]["is_top_tier_lab"] is True
    assert len(result["lab_timeline"]) == 3


_OPENALEX_PAYLOAD = {
    "results": [
        {
            "id": "https://openalex.org/W123",
            "title": "A Robust Retrieval Study",
            "authorships": [
                {
                    "author": {"display_name": "Jane Doe"},
                    "institutions": [{"display_name": "Stanford University"}],
                    "raw_affiliation_strings": ["Stanford University"],
                }
            ],
            "cited_by_count": 5000,
        }
    ]
}


class _RoutingClient:
    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def get(self, url, params=None):
        if "semanticscholar.org" in url:
            return _FakeResponse({}, status_code=404)
        if "openalex.org" in url:
            return _FakeResponse(_OPENALEX_PAYLOAD)
        return _FakeResponse({}, status_code=404)


def test_enrich_reputation_and_influence_with_openalex_fallback(monkeypatch):
    monkeypatch.setattr(httpx, "Client", _RoutingClient)
    result = _service().enrich_reputation_and_influence(
        authors=["Jane Doe"],
        title="A Robust Retrieval Study",
        doi="10.1000/test",
        published_at="2026-01-01",
    )

    assert result["influence_metrics"]["citation_count"] == 5000
    assert "Stanford University" in result["author_profile"]["affiliations"]
    assert result["author_profile"]["is_top_tier"] is True
    assert len(result["lab_timeline"]) == 3


def test_enrich_reputation_and_influence_local_fallback():
    result = _service().enrich_reputation_and_influence(
        authors=["Alice"],
        title="No External Identifier Paper",
        cited_by_count=0,
    )

    assert result["author_profile"]["first_author"] == "Alice"
    assert result["author_profile"]["top_h_index"] >= 24
    assert result["author_profile"]["affiliations"] == ["Academic Research Institute"]
    assert result["influence_metrics"]["citation_count"] == 0
    assert result["influence_metrics"]["citation_velocity"] == 0.0
    assert result["influence_metrics"]["is_top_tier_lab"] is False
    assert len(result["lab_timeline"]) == 3


def test_top_tier_institution_detection():
    assert RetrievalService.is_top_tier_institution("Stanford University") is True
    assert RetrievalService.is_top_tier_institution("MIT CSAIL") is True
    assert RetrievalService.is_top_tier_institution("Google DeepMind") is True
    assert RetrievalService.is_top_tier_institution("OpenAI") is True
    assert RetrievalService.is_top_tier_institution("Microsoft Research") is True
    assert RetrievalService.is_top_tier_institution("State Community College") is False
    assert RetrievalService.is_top_tier_institution(None) is False
    assert {"stanford", "mit", "openai", "google deepmind", "microsoft research"} <= TOP_TIER_INSTITUTIONS


def test_calculate_citation_velocity():
    svc = _service()
    assert svc.calculate_citation_velocity(0, None) == 0.0
    assert svc.calculate_citation_velocity(120, None) == 40.0
    assert svc.calculate_citation_velocity(30, "2026-01-01") > 0


def test_severity_urgency_boost():
    base = dict(
        centrality="major",
        stance="challenges",
        comparability="partial",
        impact_mode="boundary_condition",
        change_depth=1,
    )
    assert ImpactService.severity(**base, strategic_flags=[]) == "review"
    assert ImpactService.severity(
        **base, strategic_flags=["TOP_TIER_LAB"]
    ) == "critical"
    assert ImpactService.severity(
        **base,
        strategic_flags=[],
        author_influence={"is_top_tier_lab": True},
    ) == "critical"
    assert ImpactService.severity(
        **base,
        strategic_flags=[],
        author_influence={"top_h_index": 45},
    ) == "critical"

    assert ImpactService.apply_influence_urgency_boost(
        "informative", {"is_top_tier_lab": True}
    ) == "review"
    assert ImpactService.apply_influence_urgency_boost(
        "review", {"top_h_index": 40}
    ) == "critical"
    assert ImpactService.apply_influence_urgency_boost(
        "review", {"citation_velocity": 10.0}
    ) == "critical"
    assert ImpactService.apply_influence_urgency_boost("critical", {}) == "critical"


def test_calculate_severity_compatibility_wrapper():
    assert ImpactService.calculate_severity(
        stance="challenges",
        comparability="partial",
        impact_mode="boundary_condition",
        change_depth=1,
        strategic_flags=[],
        author_influence={"top_h_index": 45},
    ) == "critical"


def test_paper_out_serialization_includes_reputation_fields():
    out = PaperOut(
        id="p1",
        title="Test Paper",
        authors=["A. Author"],
        authorProfile={"first_author": "A. Author", "top_h_index": 50},
        influenceMetrics={"is_top_tier_lab": True, "citation_velocity": 15.0},
        labTimeline=[{"title": "Recent Lab Work", "year": 2026}],
    )
    dumped = out.model_dump()
    assert dumped["authorProfile"]["first_author"] == "A. Author"
    assert dumped["influenceMetrics"]["is_top_tier_lab"] is True
    assert dumped["labTimeline"][0]["year"] == 2026
