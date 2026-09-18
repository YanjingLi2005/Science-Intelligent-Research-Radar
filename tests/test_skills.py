"""CCF-A skill layer verification: the action→skill mapping, the registry
resolution, the LLM execution chain and the prompt files must all line up."""

from pathlib import Path

from starlette.testclient import TestClient

from radar.api import app
from radar.db import create_db_engine, init_database
from radar.services.action_service import ACTION_SKILL_MAP

SKILL_PROMPTS_DIR = (
    Path(__file__).resolve().parents[1] / "radar" / "llm" / "prompts" / "skills"
)

# Skill name → prompt file that _skill_prompt(skill_name) resolves to.
SKILL_PROMPT_FILES = {
    "ccf_paper_writer": "paper_writer.txt",
    "ccf_paper_reviewer": "paper_reviewer.txt",
    "ccf_integrity_auditor": "integrity_auditor.txt",
    "ccf_experiment_designer": "experiment_designer.txt",
    "ccf_rebuttal_writer": "rebuttal_writer.txt",
    "ccf_reference_auditor": "reference_auditor.txt",
}


def test_action_skill_map_matches_registered_skills():
    """Every action type must route to a skill that is actually registered."""
    from radar.skills.writer import WriterSkill
    from radar.skills.reviewer import ReviewerSkill
    from radar.skills.auditor import AuditorSkill
    from radar.skills.experiment import ExperimentSkill
    from radar.skills.rebuttal import RebuttalSkill
    from radar.skills.reference_auditor import ReferenceAuditSkill

    registered = {skill.name for skill in (
        WriterSkill(), ReviewerSkill(), AuditorSkill(), ExperimentSkill(),
        RebuttalSkill(), ReferenceAuditSkill(),
    )}
    assert set(ACTION_SKILL_MAP.values()) <= registered
    # The documented seven action types all have a route.
    assert set(ACTION_SKILL_MAP) == {
        "writing", "cite", "experiment", "data", "revalidation",
        "competitor_response", "team_decision",
    }


def test_every_skill_has_its_prompt_file():
    """A missing prompt silently degrades to the bare JSON payload — the five
    CCF-A skills must all ship their instruction files."""
    for skill_name, prompt_file in SKILL_PROMPT_FILES.items():
        assert (SKILL_PROMPTS_DIR / prompt_file).exists(), (
            f"{skill_name} is missing {prompt_file}"
        )


def test_skill_execution_chain_end_to_end(tmp_path, monkeypatch):
    """The full chain must work: execute_skill route → registry → skill → LLM
    → structured output returned to the frontend."""
    from sqlalchemy.orm import sessionmaker

    db_url = f"sqlite:///{tmp_path / 'skills.db'}"
    engine = create_db_engine(db_url)
    init_database(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    import radar.api as api_module
    import radar.db as db_module
    monkeypatch.setattr(api_module, "SessionLocal", factory)
    monkeypatch.setattr(db_module, "SessionLocal", factory)
    monkeypatch.setattr(db_module, "engine", engine)

    _MANUSCRIPT = (
        "# Introduction\n\n"
        "This paper investigates retrieval-augmented generation for open-domain "
        "question answering. We propose a novel framework that combines dense "
        "retrieval with cross-encoder reranking to improve robustness against "
        "query perturbations.\n\n"
        "# Results\n\n"
        "Our method improves exact match by 7.0 points over the BM25 baseline "
        "on the Natural Questions dataset. The reranking component alone "
        "contributes 3.2 points of improvement while adding less than 12 percent "
        "inference latency. We further demonstrate that our approach generalizes "
        "across three diverse evaluation benchmarks including TriviaQA and "
        "HotpotQA, consistently outperforming both sparse and dense retrieval "
        "baselines with statistical significance.\n\n"
        "# Conclusion\n\n"
        "We have shown that retrieval-aware reranking is a practical and "
        "effective strategy for robust open-domain question answering.\n"
    )

    class FakeSkillLLM:
        last_receipt = {
            "raw_response": "{}",
            "usage": {"prompt_tokens": 100, "completion_tokens": 30},
            "latency_ms": 12,
        }

        def generate_structured(self, *, stage, prompt, response_model, max_tokens=None):
            assert stage == "skill_paper_writer"
            from radar.skills.writer import WriterEdit, WriterOutput

            return WriterOutput(
                edits=[
                    WriterEdit(
                        section="results",
                        edit_class="add_citation",
                        before_text="old sentence",
                        after_text="new sentence [CITATION]",
                        reason="adds the matched evidence",
                    )
                ],
                not_adopted=[],
            )

    import radar.skills.registry as registry_module
    from radar.skills.auditor import AuditorSkill
    from radar.skills.experiment import ExperimentSkill
    from radar.skills.rebuttal import RebuttalSkill
    from radar.skills.registry import SkillRegistry
    from radar.skills.reviewer import ReviewerSkill
    from radar.skills.writer import WriterSkill

    registry = SkillRegistry(llm_client=FakeSkillLLM(), session_factory=factory)
    for skill in (
        WriterSkill(), ReviewerSkill(), AuditorSkill(),
        ExperimentSkill(), RebuttalSkill(),
    ):
        registry.register(skill)
    monkeypatch.setattr(registry_module, "get_registry", lambda: registry)

    manuscript_path = tmp_path / "paper.md"
    manuscript_path.write_text(_MANUSCRIPT, encoding="utf-8")
    with TestClient(app) as client:
        with open(manuscript_path, "rb") as f:
            resp = client.post(
                "/api/cases",
                files={
                    "title": (None, "Skill Test"),
                    "research_question": (None, "Does it work?"),
                    "manuscript": ("paper.md", f, "text/markdown"),
                },
            )
        assert resp.status_code == 200, resp.text
        case_id = resp.json()["id"]

        claims = client.get(f"/api/cases/{case_id}/claims").json()
        assert claims, "expected a claim candidate"
        confirmed = client.post(
            f"/api/cases/{case_id}/claims/{claims[0]['id']}/confirm"
        )
        assert confirmed.status_code == 200, confirmed.text

        result = client.post(
            f"/api/cases/{case_id}/skills/execute",
            data={
                "skill_name": "ccf_paper_writer",
                "action_type": "writing",
                "impact_ids": "[]",
            },
        )
        assert result.status_code == 200, result.text
        payload = result.json()
        assert payload["skill"] == "ccf_paper_writer"
        assert payload["artifact_type"] == "manuscript_edits"
        assert payload["content"]["edits"][0]["edit_class"] == "add_citation"
        assert payload["handoff_suggestions"] == ["ccf_integrity_auditor"]
        assert payload["warnings"] == []

    # The execution left its audit trail (ModelRun + AuditEvent).
    with factory() as session:
        from sqlalchemy import select
        from radar.models import AuditEvent, ModelRun

        run = session.scalar(
            select(ModelRun).where(ModelRun.stage == "skill_ccf_paper_writer")
        )
        assert run is not None
        assert run.input_tokens == 100
        assert run.case_id == case_id
        event = session.scalar(
            select(AuditEvent).where(
                AuditEvent.event_type == "skill_ccf_paper_writer_executed"
            )
        )
        assert event is not None
    engine.dispose()


def test_skill_routing_is_server_side(tmp_path, monkeypatch):
    """A skill that does not declare the action type must be rejected (400),
    and an empty skill_name must be resolved by the registry route()."""
    from sqlalchemy.orm import sessionmaker

    db_url = f"sqlite:///{tmp_path / 'skills-routing.db'}"
    engine = create_db_engine(db_url)
    init_database(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    import radar.api as api_module
    import radar.db as db_module
    monkeypatch.setattr(api_module, "SessionLocal", factory)
    monkeypatch.setattr(db_module, "SessionLocal", factory)
    monkeypatch.setattr(db_module, "engine", engine)

    import radar.skills.registry as registry_module
    from radar.skills.auditor import AuditorSkill
    from radar.skills.experiment import ExperimentSkill
    from radar.skills.rebuttal import RebuttalSkill
    from radar.skills.registry import SkillRegistry
    from radar.skills.reviewer import ReviewerSkill
    from radar.skills.writer import WriterSkill

    registry = SkillRegistry()
    for skill in (
        WriterSkill(), ReviewerSkill(), AuditorSkill(),
        ExperimentSkill(), RebuttalSkill(),
    ):
        registry.register(skill)
    monkeypatch.setattr(registry_module, "get_registry", lambda: registry)

    _MANUSCRIPT = (
        "# Results\n\nOur method improves exact match by 7.0 points over BM25. "
        "We study retrieval-augmented question answering in open research "
        "settings. The project context and setup are described before the "
        "measurements."
    )
    manuscript_path = tmp_path / "paper.md"
    manuscript_path.write_text(_MANUSCRIPT, encoding="utf-8")

    with TestClient(app) as client:
        with open(manuscript_path, "rb") as f:
            created = client.post(
                "/api/cases",
                files={
                    "title": (None, "Routing Test"),
                    "research_question": (None, "Q?"),
                    "manuscript": ("paper.md", f, "text/markdown"),
                },
            )
        assert created.status_code == 200, created.text
        case_id = created.json()["id"]

        # Skill that does not handle the action type → 400.
        wrong = client.post(
            f"/api/cases/{case_id}/skills/execute",
            data={
                "skill_name": "ccf_rebuttal_writer",
                "action_type": "writing",
            },
        )
        assert wrong.status_code == 400
        assert "cannot handle action type" in wrong.json()["detail"]

        # Unknown skill name → 404.
        unknown = client.post(
            f"/api/cases/{case_id}/skills/execute",
            data={"skill_name": "ccf_nonexistent", "action_type": "writing"},
        )
        assert unknown.status_code == 404

        # Empty skill name with an unroutable action type → 404.
        unroutable = client.post(
            f"/api/cases/{case_id}/skills/execute",
            data={"skill_name": "", "action_type": "unsupported_action"},
        )
        assert unroutable.status_code == 404
        assert "no skill handles" in unroutable.json()["detail"]

        # Empty skill name routes server-side to the writer for "writing".
        routed = client.post(
            f"/api/cases/{case_id}/skills/execute",
            data={"skill_name": "", "action_type": "writing"},
        )
        assert routed.status_code == 200, routed.text
        assert routed.json()["skill"] == "ccf_paper_writer"
    engine.dispose()


def test_skill_without_llm_degrades_with_warning(tmp_path, monkeypatch):
    """Without an LLM the skill must return an explicit warning, not crash."""
    from sqlalchemy.orm import sessionmaker

    db_url = f"sqlite:///{tmp_path / 'skills-nollm.db'}"
    engine = create_db_engine(db_url)
    init_database(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    import radar.api as api_module
    import radar.db as db_module
    monkeypatch.setattr(api_module, "SessionLocal", factory)
    monkeypatch.setattr(db_module, "SessionLocal", factory)
    monkeypatch.setattr(db_module, "engine", engine)

    import radar.skills.registry as registry_module
    from radar.skills.registry import SkillRegistry

    monkeypatch.setattr(registry_module, "get_registry", lambda: SkillRegistry())

    with TestClient(app) as client:
        result = client.post(
            "/api/cases/does-not-exist/skills/execute",
            data={"skill_name": "ccf_paper_writer", "action_type": "writing"},
        )
        # Case lookup happens first: 404, not a crash.
        assert result.status_code == 404
    engine.dispose()
