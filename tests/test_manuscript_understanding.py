from sqlalchemy import select

from radar.config import Settings
from radar.models import AuditEvent, ModelRun
from radar.schemas import (
    EmpiricalClaimContract,
    ManuscriptClaimProfile,
    ManuscriptUnderstandingOutput,
)
from radar.services.manuscript_understanding_service import (
    ManuscriptUnderstandingService,
)


class ProfileLLM:
    def __init__(self):
        self.prompt = ""
        self.last_receipt = {
            "raw_response": "{}",
            "usage": {"prompt_tokens": 1200, "completion_tokens": 400},
            "latency_ms": 25,
        }

    def generate_structured(self, *, stage, prompt, response_model, max_tokens=None):
        assert stage == "manuscript_understanding"
        self.prompt = prompt
        return ManuscriptUnderstandingOutput(
            title="RadarNet",
            research_problem="Robust retrieval under domain shift",
            central_thesis="RadarNet improves robustness.",
            contributions=["A retrieval method", "A matched evaluation"],
            methods=["RadarNet"],
            datasets=["DomainQA"],
            evaluation_protocol=["Unseen-domain split"],
            key_findings=["Improved exact match"],
            limitations=["One benchmark"],
            terminology=["domain shift"],
            watch_topics=["RAG robustness"],
            claim_profiles=[
                ManuscriptClaimProfile(
                    stable_key=f"C{index}",
                    role="core" if index == 1 else "major",
                    claim_summary=f"Claim {index}",
                    contract=EmpiricalClaimContract(
                        task="open-domain QA",
                        dataset="DomainQA",
                        metric="exact match",
                    ),
                    boundary_conditions=["Unseen domains"],
                    falsification_tests=["Matched reproduction"],
                )
                for index in range(1, 11)
            ],
        )


def _settings():
    return Settings(
        _env_file=None,
        llm_provider="deepseek",
        llm_api_key="sk-test-key-000000000000",
        llm_model="deepseek-v4-pro",
        llm_base_url="https://api.deepseek.com",
    )


def _profile_output(key_count: int) -> ManuscriptUnderstandingOutput:
    return ManuscriptUnderstandingOutput(
        title="RadarNet",
        research_problem="Robust retrieval under domain shift",
        central_thesis="RadarNet improves robustness.",
        contributions=["A retrieval method", "A matched evaluation"],
        methods=["RadarNet"],
        datasets=["DomainQA"],
        evaluation_protocol=["Unseen-domain split"],
        key_findings=["Improved exact match"],
        limitations=["One benchmark"],
        terminology=["domain shift"],
        watch_topics=["RAG robustness"],
        claim_profiles=[
            ManuscriptClaimProfile(
                stable_key=f"C{index}",
                role="core" if index == 1 else "major",
                claim_summary=f"Claim {index}",
                contract=EmpiricalClaimContract(
                    task="open-domain QA",
                    dataset="DomainQA",
                    metric="exact match",
                ),
                boundary_conditions=["Unseen domains"],
                falsification_tests=["Matched reproduction"],
            )
            for index in range(1, key_count + 1)
        ],
    )


class FlakyProfileLLM:
    """First call misses one confirmed claim; the correction retry fixes it."""

    def __init__(self):
        self.prompts: list[str] = []
        self.last_receipt = {
            "raw_response": "{}",
            "usage": {"prompt_tokens": 1200, "completion_tokens": 400},
            "latency_ms": 25,
        }

    def generate_structured(self, *, stage, prompt, response_model, max_tokens=None):
        assert stage == "manuscript_understanding"
        self.prompts.append(prompt)
        key_count = 9 if len(self.prompts) == 1 else 10
        return _profile_output(key_count)


def test_claim_key_mismatch_is_retried_once_with_feedback(
    db_session_factory, golden_case
):
    llm = FlakyProfileLLM()
    run_id, output = ManuscriptUnderstandingService(
        db_session_factory,
        llm_client=llm,
        settings=_settings(),
    ).analyze(golden_case)

    assert len(llm.prompts) == 2
    assert "missing=['C10']" in llm.prompts[1]
    assert "unexpected=[]" in llm.prompts[1]
    assert len(output.claim_profiles) == 10
    with db_session_factory() as session:
        assert session.get(ModelRun, run_id) is not None


def test_full_manuscript_profile_is_stored_and_reusable(
    db_session_factory, golden_case
):
    llm = ProfileLLM()
    run_id, output = ManuscriptUnderstandingService(
        db_session_factory,
        llm_client=llm,
        settings=_settings(),
    ).analyze(golden_case)

    assert "FULL MANUSCRIPT INCLUDED" in llm.prompt
    assert "RadarNet improves exact match" in llm.prompt
    assert len(output.claim_profiles) == 10

    with db_session_factory() as session:
        run = session.get(ModelRun, run_id)
        audit = session.scalar(
            select(AuditEvent).where(
                AuditEvent.event_type == "manuscript_understanding_completed"
            )
        )
        assert run.validation_json["full_manuscript_included"] is True
        assert run.case_id == golden_case
        assert run.input_tokens == 1200
        assert audit.payload_json["confirmed_claims"] == 10

    loaded = ManuscriptUnderstandingService.latest_profile(
        "case-without-profile",
        db_session_factory,
    )
    assert loaded is None

    loaded = ManuscriptUnderstandingService.latest_profile(
        golden_case, db_session_factory
    )
    assert loaded is not None
    assert loaded.central_thesis == "RadarNet improves robustness."
