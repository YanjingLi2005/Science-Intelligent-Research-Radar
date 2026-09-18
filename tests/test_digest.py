"""Weekly radar digest service tests."""

from starlette.testclient import TestClient

from radar.api import app
from radar.services.digest_service import DigestService


def test_digest_generates_markdown_from_case(db_session_factory, golden_case):
    digest = DigestService(db_session_factory).generate_digest(golden_case)

    assert digest["case_id"] == golden_case
    assert "雷达周报" in digest["title"]
    assert digest["markdown"].startswith("# Research Radar 周报")
    assert "研究问题" in digest["markdown"]
    assert isinstance(digest["sections"], list)
    assert digest["sections"], "expected at least one digest section"


def test_digest_filters_sections(db_session_factory, golden_case):
    digest = DigestService(db_session_factory).generate_digest(
        golden_case, include_sections=["scan", "impacts"]
    )
    allowed = {"最新雷达扫描", "新影响"}
    for section in digest["sections"]:
        assert section["heading"] in allowed


def test_digest_api_route_returns_digest(monkeypatch):
    from radar.services.digest_service import DigestService

    def fake_digest(self, case_id, *, include_sections=None):
        return {
            "case_id": case_id,
            "title": "fake digest",
            "generated_at": "now",
            "summary": {},
            "sections": [],
            "markdown": "# fake",
        }

    monkeypatch.setattr(DigestService, "generate_digest", fake_digest)

    with TestClient(app) as client:
        resp = client.get("/api/cases/whatever/digest")
        assert resp.status_code == 200, resp.text
        assert resp.json()["markdown"] == "# fake"
