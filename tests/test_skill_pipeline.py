"""CCF-A skill pipeline tests."""

import pytest

from radar.services.skill_pipeline_service import PIPELINES, SkillPipelineService


def test_revision_pipeline_runs_skills_in_order(monkeypatch):
    calls = []

    class FakeExecutor:
        def __init__(self, session_factory):
            self.session_factory = session_factory

        def execute(self, case_id, *, skill_name, action_type, impact_ids):
            calls.append((skill_name, action_type))
            return {
                "skill": skill_name,
                "artifact_type": "x",
                "content": {},
                "warnings": [],
                "handoff_suggestions": [],
            }

    monkeypatch.setattr(
        "radar.services.skill_pipeline_service.SkillExecutionService",
        FakeExecutor,
    )

    result = SkillPipelineService(None).run("case-id", pipeline="revision")
    assert calls == [
        ("ccf_paper_writer", "writing"),
        ("ccf_integrity_auditor", "audit"),
        ("ccf_paper_reviewer", "review"),
    ]
    assert result["skills"] == PIPELINES["revision"]
    assert len(result["results"]) == 3


def test_custom_skill_names_are_supported(monkeypatch):
    calls = []

    class FakeExecutor:
        def __init__(self, session_factory):
            pass

        def execute(self, case_id, *, skill_name, action_type, impact_ids):
            calls.append(skill_name)
            return {"skill": skill_name}

    monkeypatch.setattr(
        "radar.services.skill_pipeline_service.SkillExecutionService",
        FakeExecutor,
    )

    SkillPipelineService(None).run(
        "case-id",
        skill_names=["ccf_reference_auditor", "ccf_paper_reviewer"],
    )
    assert calls == ["ccf_reference_auditor", "ccf_paper_reviewer"]


def test_unknown_pipeline_raises():
    with pytest.raises(ValueError):
        SkillPipelineService(None).run("case-id", pipeline="does-not-exist")


def test_pipeline_records_audit_event(db_session_factory, golden_case, monkeypatch):
    from radar.models import AuditEvent

    class FakeExecutor:
        def __init__(self, session_factory):
            pass

        def execute(self, case_id, *, skill_name, action_type, impact_ids):
            return {"skill": skill_name, "artifact_type": "x", "content": {}}

    monkeypatch.setattr(
        "radar.services.skill_pipeline_service.SkillExecutionService",
        FakeExecutor,
    )

    SkillPipelineService(db_session_factory).run(golden_case, pipeline="review")

    with db_session_factory() as session:
        event = session.query(AuditEvent).filter(
            AuditEvent.event_type == "skill_pipeline_executed"
        ).first()
        assert event is not None
        assert event.payload_json["pipeline"] == "review"
        assert event.payload_json["skills"] == ["ccf_paper_reviewer", "ccf_reference_auditor"]
