import hashlib

import pytest
from sqlalchemy import select

from uuid import uuid4

from radar.models import (
    Claim,
    ClaimRevision,
    ImpactCandidate,
    ManuscriptVersion,
    ModelRun,
    PatchProposal,
    ResearchCase,
)
from radar.schemas import PatchProposalOutput
from radar.services.patch_service import PatchService
from radar.services.review_service import ReviewService


def test_dismissed_impact_invalidates_patch_validation(db_session_factory, golden_case):
    """M7 regression: impact_confirmed must be a live re-check — dismissing the
    impact after generation must invalidate the patch."""
    ReviewService(db_session_factory).confirm_impact("impact-01")
    patch = PatchService(db_session_factory).generate_patch("impact-01")
    assert all(patch.validations_json.values())

    ReviewService(db_session_factory).dismiss_impact("impact-01")
    revalidated = PatchService(db_session_factory).validate_patch(patch.id)
    assert revalidated["impact_confirmed"] is False
    with pytest.raises(ValueError, match="patch_validation_failed"):
        PatchService(db_session_factory).approve_patch(patch.id)


def test_approval_rejects_empty_validations(db_session_factory, golden_case):
    """M7 regression: all({}) is True — an empty validation record must not
    pass the G2 approval gate."""
    with db_session_factory() as session:
        impact = session.get(ImpactCandidate, "impact-01")
        revision = session.get(ClaimRevision, impact.claim_revision_id)
        manuscript = session.get(ManuscriptVersion, revision.manuscript_version_id)
        patch = PatchProposal(
            id=str(uuid4()),
            case_id=golden_case,
            manuscript_version_id=manuscript.id,
            impact_candidate_id="impact-01",
            target_locator="sec:main-results:p:1",
            edit_class="add_citation",
            before_text="old sentence",
            after_text="new sentence",
            citations_json=[],
            evidence_refs_json=[],
            validations_json={},  # no checks were ever recorded
            approval_state="candidate",
        )
        session.add(patch)
        session.commit()
        patch_id = patch.id

    with pytest.raises(ValueError, match="patch_validation_failed"):
        PatchService(db_session_factory).approve_patch(patch_id)


def test_patch_generation_uses_manuscript_timeout_setting():
    """M28 regression: patch generation reads the full manuscript, so its
    client must use llm_manuscript_timeout_seconds (240s default), not the
    generic llm_timeout_seconds (120s)."""
    from radar.config import Settings
    from radar.llm.provider import ProviderLLMClient

    settings = Settings(
        _env_file=None,
        llm_provider="deepseek",
        llm_api_key="sk-test-key-000000000000",
        llm_model="deepseek-v4-pro",
        llm_base_url="https://api.deepseek.com",
        llm_timeout_seconds=120.0,
        llm_manuscript_timeout_seconds=500.0,
    )
    service = PatchService(settings=settings)

    assert isinstance(service.llm_client, ProviderLLMClient)
    assert service.llm_client.timeout_seconds == 500.0


def test_patch_generation_without_llm_reports_configured_error(
    db_session_factory, golden_case
):
    """M29 regression: with no LLM configured, generating a patch for a real
    project must raise llm_not_configured (mapped to 400), not a 500."""
    ReviewService(db_session_factory).confirm_impact("impact-01")
    with db_session_factory() as session:
        impact = session.get(ImpactCandidate, "impact-01")
        revision = session.get(ClaimRevision, impact.claim_revision_id)
        claim = session.get(Claim, revision.claim_id)
        research_case = session.get(ResearchCase, claim.case_id)
        research_case.settings_json = {}  # no fixture template → model path
        session.commit()

    no_llm_settings = __import__("radar.config", fromlist=["Settings"]).Settings(
        _env_file=None
    )
    service = PatchService(db_session_factory, settings=no_llm_settings)
    assert service.llm_client is None

    with pytest.raises(ValueError, match="llm_not_configured"):
        service.generate_patch("impact-01")


def test_candidate_cannot_generate_patch(db_session_factory, golden_case):
    with pytest.raises(ValueError, match="candidate_cannot_generate_patch"):
        PatchService(db_session_factory).generate_patch("impact-01")


def test_confirmed_impact_generates_valid_export_only_patch(db_session_factory, golden_case):
    with db_session_factory() as session:
        manuscript = session.scalar(select(ManuscriptVersion))
        original_hash = hashlib.sha256(manuscript.content_text.encode()).hexdigest()
    ReviewService(db_session_factory).confirm_impact("impact-01")
    patch = PatchService(db_session_factory).generate_patch("impact-01")
    assert all(patch.validations_json.values())
    assert "61.2%" in patch.after_text and "68.7%" in patch.after_text
    approved = PatchService(db_session_factory).approve_patch(patch.id)
    assert approved.approval_state == "approved"
    with db_session_factory() as session:
        manuscript = session.scalar(select(ManuscriptVersion))
        assert hashlib.sha256(manuscript.content_text.encode()).hexdigest() == original_hash


class ScriptedPatchLLM:
    def __init__(self, before_text):
        self.before_text = before_text
        self.stages = []
        self.last_receipt = {
            "raw_response": "{}",
            "usage": {"prompt_tokens": 100, "completion_tokens": 40},
            "latency_ms": 12,
        }

    def generate_structured(self, *, stage, prompt, response_model, max_tokens=None):
        self.stages.append(stage)
        return PatchProposalOutput(
            edit_class="add_boundary_discussion",
            target_locator="sec:main-results:p:1",
            before_text=self.before_text,
            after_text=(
                self.before_text
                + " This finding should be interpreted within the matched evaluation conditions."
            ),
            citation_source_ids=[],
            assertions_added=["The interpretation is bounded by matched conditions."],
            assertions_weakened_or_removed=[],
            rationale="Adds the smallest evidence-grounded boundary statement.",
        )


class PriorArtPatchLLM:
    def __init__(self, result_sentence, related_work_sentence):
        self.result_sentence = result_sentence
        self.related_work_sentence = related_work_sentence
        self.calls = 0
        self.last_receipt = {
            "raw_response": "{}",
            "usage": {"prompt_tokens": 100, "completion_tokens": 40},
            "latency_ms": 12,
        }

    def generate_structured(self, *, stage, prompt, response_model, max_tokens=None):
        self.calls += 1
        if self.calls == 1:
            return PatchProposalOutput(
                edit_class="add_citation",
                target_locator="results",
                before_text=self.result_sentence,
                after_text=self.result_sentence + " [28].",
                citation_source_ids=[],
                assertions_added=[],
                assertions_weakened_or_removed=[],
                rationale="Bad first attempt.",
            )
        return PatchProposalOutput(
            edit_class="add_citation",
            target_locator="Related Work",
            before_text=self.related_work_sentence,
            after_text=(
                self.related_work_sentence
                + " A recent taxonomy positions this design within agentic RAG [CITATION]."
            ),
            citation_source_ids=[],
            assertions_added=["The design is positioned within agentic RAG."],
            assertions_weakened_or_removed=[],
            rationale="Adds the citation in the positioning section.",
        )


def test_uploaded_project_uses_structured_llm_patch_generation(
    db_session_factory, golden_case
):
    ReviewService(db_session_factory).confirm_impact("impact-01")
    with db_session_factory() as session:
        impact = session.get(ImpactCandidate, "impact-01")
        revision = session.get(ClaimRevision, impact.claim_revision_id)
        claim = session.get(Claim, revision.claim_id)
        research_case = session.get(ResearchCase, claim.case_id)
        research_case.settings_json = {}
        before_text = revision.source_quote
        session.commit()

    llm = ScriptedPatchLLM(before_text)
    patch = PatchService(
        db_session_factory,
        llm_client=llm,
    ).generate_patch("impact-01")

    assert llm.stages == ["patch_generation"]
    assert patch.edit_class == "add_boundary_discussion"
    assert all(patch.validations_json.values())
    with db_session_factory() as session:
        run = session.scalar(
            select(ModelRun).where(ModelRun.stage == "patch_generation")
        )
        assert run is not None
        assert run.input_tokens == 100


def test_prior_art_patch_retries_wrong_section_and_numeric_citation(
    db_session_factory, golden_case
):
    with db_session_factory() as session:
        impact = session.get(ImpactCandidate, "impact-03")
        impact.review_state = "edited"
        impact.comparability = "unknown"
        revision = session.get(ClaimRevision, impact.claim_revision_id)
        manuscript = session.get(ManuscriptVersion, revision.manuscript_version_id)
        claim = session.get(Claim, revision.claim_id)
        research_case = session.get(ResearchCase, claim.case_id)
        research_case.settings_json = {}
        result_sentence = revision.source_quote
        related_work_sentence = "Retrieval systems increasingly use hierarchical routing."
        exact_related_work_sentence = "Retrieval systems increasingly use hierar-\nchical routing."
        manuscript.content_text = (
            f"Abstract\n{result_sentence}\n\n1 Introduction\nBackground.\n\n"
            f"2 Related Work\n{exact_related_work_sentence}\n"
        )
        session.commit()

    llm = PriorArtPatchLLM(result_sentence, related_work_sentence)
    patch = PatchService(db_session_factory, llm_client=llm).generate_patch("impact-03")

    assert llm.calls == 2
    assert patch.target_locator == "Related Work"
    assert "hierar-\nchical" in patch.before_text
    assert "[CITATION]" in patch.after_text
    assert patch.validations_json["citation_marker_safe"] is True
