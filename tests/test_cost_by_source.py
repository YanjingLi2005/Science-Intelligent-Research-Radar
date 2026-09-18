"""Cost attribution by source kind API tests."""

from starlette.testclient import TestClient

from radar.api import app
from radar.models import ModelRun, Source, SourceSnapshot


def test_cost_by_source_attributes_run_to_snapshot_source(
    db_session_factory, golden_case, monkeypatch
):
    import radar.api as api_module
    import radar.db as db_module

    engine = db_session_factory.kw["bind"]
    monkeypatch.setattr(api_module, "SessionLocal", db_session_factory)
    monkeypatch.setattr(db_module, "SessionLocal", db_session_factory)
    monkeypatch.setattr(db_module, "engine", engine)

    snapshot_id = "snapshot-01"
    with db_session_factory() as session:
        snapshot = session.get(SourceSnapshot, snapshot_id)
        source_kind = session.get(Source, snapshot.source_id).source_kind
        session.add(
            ModelRun(
                id="cost-by-source-run",
                stage="test_cost_by_source",
                case_id=golden_case,
                provider="test",
                model="test-model",
                prompt_hash="prompt-hash",
                schema_version="test.v1",
                input_refs_json=[snapshot_id],
                raw_response="{}",
                parsed_output_json={},
                validation_json={},
                estimated_cost=0.25,
            )
        )
        session.commit()

    with TestClient(app) as client:
        response = client.get(f"/api/cases/{golden_case}/cost-by-source")

    assert response.status_code == 200, response.text
    payload = response.json()
    item = next(item for item in payload["items"] if item["source_kind"] == source_kind)
    assert item["cost_usd"] > 0
    assert item["run_count"] == 1
    assert payload["total_cost_usd"] >= item["cost_usd"]
