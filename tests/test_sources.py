"""Source-list API tests."""

from starlette.testclient import TestClient

from radar.api import app
from radar.models import ResearchCase, Source


def test_list_sources_for_case(db_session_factory, golden_case, monkeypatch):
    """The source endpoint returns stored golden-case literature."""
    import radar.api as api_module
    import radar.db as db_module

    engine = db_session_factory.kw["bind"]
    monkeypatch.setattr(api_module, "SessionLocal", db_session_factory)
    monkeypatch.setattr(db_module, "SessionLocal", db_session_factory)
    monkeypatch.setattr(db_module, "engine", engine)

    with TestClient(app) as client:
        response = client.get(f"/api/cases/{golden_case}/sources")

    assert response.status_code == 200, response.text
    sources = response.json()
    assert isinstance(sources, list)
    assert sources
    assert {source["id"] for source in sources} == {
        f"source-{index:02d}" for index in range(1, 8)
    }
    assert {source["snapshot_count"] for source in sources} == {1}


def test_list_sources_supports_filter_and_pagination(
    db_session_factory, golden_case, monkeypatch
):
    import radar.api as api_module
    import radar.db as db_module

    engine = db_session_factory.kw["bind"]
    monkeypatch.setattr(api_module, "SessionLocal", db_session_factory)
    monkeypatch.setattr(db_module, "SessionLocal", db_session_factory)
    monkeypatch.setattr(db_module, "engine", engine)

    with db_session_factory() as session:
        for source_id in ("source-01", "source-02", "source-03"):
            session.get(Source, source_id).ccf_rank = "A"
        session.get(Source, "source-04").ccf_rank = "B"
        session.commit()

    with TestClient(app) as client:
        filtered = client.get(
            f"/api/cases/{golden_case}/sources",
            params={"ccf_rank": "A"},
        )
        paginated = client.get(
            f"/api/cases/{golden_case}/sources",
            params={"ccf_rank": "A", "limit": 2, "offset": 1},
        )

    assert filtered.status_code == 200, filtered.text
    assert paginated.status_code == 200, paginated.text
    filtered_sources = filtered.json()
    paginated_sources = paginated.json()
    assert filtered_sources
    assert {source["id"] for source in filtered_sources} == {
        "source-01",
        "source-02",
        "source-03",
    }
    assert {source["ccf_rank"] for source in filtered_sources} == {"A"}
    assert paginated_sources == filtered_sources[1:3]


def test_get_source_for_case_returns_detail(db_session_factory, golden_case, monkeypatch):
    import radar.api as api_module
    import radar.db as db_module

    engine = db_session_factory.kw["bind"]
    monkeypatch.setattr(api_module, "SessionLocal", db_session_factory)
    monkeypatch.setattr(db_module, "SessionLocal", db_session_factory)
    monkeypatch.setattr(db_module, "engine", engine)

    with TestClient(app) as client:
        response = client.get(f"/api/cases/{golden_case}/sources/source-01")

    assert response.status_code == 200, response.text
    source = response.json()
    assert source["id"] == "source-01"
    assert isinstance(source["snapshots"], list)


def test_get_source_for_case_rejects_unreferenced_source(
    db_session_factory, golden_case, monkeypatch
):
    import radar.api as api_module
    import radar.db as db_module

    engine = db_session_factory.kw["bind"]
    monkeypatch.setattr(api_module, "SessionLocal", db_session_factory)
    monkeypatch.setattr(db_module, "SessionLocal", db_session_factory)
    monkeypatch.setattr(db_module, "engine", engine)

    with TestClient(app) as client:
        response = client.get(f"/api/cases/{golden_case}/sources/source-08")

    assert response.status_code == 404


def test_set_source_tags_returns_and_lists_tags(
    db_session_factory, golden_case, monkeypatch
):
    import radar.api as api_module
    import radar.db as db_module

    engine = db_session_factory.kw["bind"]
    monkeypatch.setattr(api_module, "SessionLocal", db_session_factory)
    monkeypatch.setattr(db_module, "SessionLocal", db_session_factory)
    monkeypatch.setattr(db_module, "engine", engine)

    tags = ["must-read", "benchmark"]
    with TestClient(app) as client:
        response = client.put(
            f"/api/cases/{golden_case}/sources/source-01/tags",
            json={"tags": tags},
        )
        listed = client.get(f"/api/cases/{golden_case}/sources")

    assert response.status_code == 200, response.text
    assert response.json() == {"source_id": "source-01", "tags": tags}
    assert listed.status_code == 200, listed.text
    source = next(item for item in listed.json() if item["id"] == "source-01")
    assert source["tags"] == tags
    with db_session_factory() as session:
        case = session.get(ResearchCase, golden_case)
        assert case.settings_json["source_tags"]["source-01"] == tags


def test_list_sources_filters_by_case_level_tag(
    db_session_factory, golden_case, monkeypatch
):
    import radar.api as api_module
    import radar.db as db_module

    engine = db_session_factory.kw["bind"]
    monkeypatch.setattr(api_module, "SessionLocal", db_session_factory)
    monkeypatch.setattr(db_module, "SessionLocal", db_session_factory)
    monkeypatch.setattr(db_module, "engine", engine)

    with TestClient(app) as client:
        tagged = client.put(
            f"/api/cases/{golden_case}/sources/source-01/tags",
            json={"tags": ["must-read"]},
        )
        filtered = client.get(
            f"/api/cases/{golden_case}/sources",
            params={"tag": "must-read"},
        )

    assert tagged.status_code == 200, tagged.text
    assert filtered.status_code == 200, filtered.text
    assert [source["id"] for source in filtered.json()] == ["source-01"]


def test_set_source_bulk_tags_updates_all_referenced_sources(
    db_session_factory, golden_case, monkeypatch
):
    import radar.api as api_module
    import radar.db as db_module

    engine = db_session_factory.kw["bind"]
    monkeypatch.setattr(api_module, "SessionLocal", db_session_factory)
    monkeypatch.setattr(db_module, "SessionLocal", db_session_factory)
    monkeypatch.setattr(db_module, "engine", engine)

    tags = ["must-read", "benchmark"]
    with TestClient(app) as client:
        response = client.post(
            f"/api/cases/{golden_case}/sources/tags",
            json={"source_ids": ["source-01", "source-02"], "tags": tags},
        )
        listed = client.get(f"/api/cases/{golden_case}/sources")

    assert response.status_code == 200, response.text
    assert response.json() == {
        "updated_source_ids": ["source-01", "source-02"]
    }
    assert listed.status_code == 200, listed.text
    listed_by_id = {source["id"]: source for source in listed.json()}
    assert listed_by_id["source-01"]["tags"] == tags
    assert listed_by_id["source-02"]["tags"] == tags
    with db_session_factory() as session:
        case = session.get(ResearchCase, golden_case)
        assert case.settings_json["source_tags"]["source-01"] == tags
        assert case.settings_json["source_tags"]["source-02"] == tags


def test_set_source_bulk_tags_rejects_empty_or_unreferenced_sources(
    db_session_factory, golden_case, monkeypatch
):
    import radar.api as api_module
    import radar.db as db_module

    engine = db_session_factory.kw["bind"]
    monkeypatch.setattr(api_module, "SessionLocal", db_session_factory)
    monkeypatch.setattr(db_module, "SessionLocal", db_session_factory)
    monkeypatch.setattr(db_module, "engine", engine)

    with TestClient(app) as client:
        empty = client.post(
            f"/api/cases/{golden_case}/sources/tags",
            json={"source_ids": [], "tags": ["review"]},
        )
        unreferenced = client.post(
            f"/api/cases/{golden_case}/sources/tags",
            json={
                "source_ids": ["source-01", "source-08"],
                "tags": ["review"],
            },
        )

    assert empty.status_code == 400, empty.text
    assert unreferenced.status_code == 404, unreferenced.text
    with db_session_factory() as session:
        case = session.get(ResearchCase, golden_case)
        assert "source-01" not in (case.settings_json or {}).get("source_tags", {})


def test_remove_sources_from_case_library(
    db_session_factory, golden_case, monkeypatch
):
    import radar.api as api_module
    import radar.db as db_module

    engine = db_session_factory.kw["bind"]
    monkeypatch.setattr(api_module, "SessionLocal", db_session_factory)
    monkeypatch.setattr(db_module, "SessionLocal", db_session_factory)
    monkeypatch.setattr(db_module, "engine", engine)

    with TestClient(app) as client:
        response = client.post(
            f"/api/cases/{golden_case}/sources/remove",
            json={"source_ids": ["source-01"]},
        )
        remaining = client.get(f"/api/cases/{golden_case}/sources")

    assert response.status_code == 200, response.text
    assert remaining.status_code == 200, remaining.text
    assert response.json()["removed_source_count"] > 0
    assert "source-01" not in {source["id"] for source in remaining.json()}
    with db_session_factory() as session:
        assert session.get(Source, "source-01") is not None
