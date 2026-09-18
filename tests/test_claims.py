from pathlib import Path

import pytest
from sqlalchemy import func, select

from radar.models import (
    AuditEvent,
    Claim,
    ClaimRevision,
    G0Feedback,
    ManuscriptVersion,
    ModelRun,
    ResearchCase,
)
from radar.parsers.latex import LatexParser
from radar.parsers.markdown import MarkdownParser
from radar.schemas import AtomicClaim, AtomicClaimBatch, ClaimSemanticVerification
from radar.services.case_service import CaseService
from radar.services.claim_service import ClaimService, _rank_candidate_spans
from radar.services.trust_service import TrustService


def test_golden_source_quotes_are_exact(db_session_factory, golden_case):
    with db_session_factory() as session:
        manuscript = session.scalar(
            select(ManuscriptVersion).where(ManuscriptVersion.case_id == golden_case)
        )
        revisions = list(session.scalars(select(ClaimRevision)))
    assert len(revisions) == 10
    assert all(revision.source_quote in manuscript.content_text for revision in revisions)


def test_source_quote_failure_blocks_candidate():
    result = TrustService().verify_claim_quote("invented result", "supplied manuscript")
    assert result.state == "blocked"
    assert result.errors == ["span_failed"]


def test_uploaded_markdown_requires_confirmation(db_session_factory, tmp_path):
    manuscript = tmp_path / "paper.md"
    manuscript.write_text(
        "# Results\n\nOur method improves exact match by 7.0 points over BM25.\n\n"
        "We study retrieval-augmented question answering in open research settings. "
        "The project context and setup are described before the measurements.",
        encoding="utf-8",
    )
    case_id = CaseService(db_session_factory).create_case(
        title="Upload test", research_question="Does it improve?", manuscript_path=manuscript
    )
    with db_session_factory() as session:
        candidate = session.scalar(
            select(ClaimRevision).join(ManuscriptVersion).where(ManuscriptVersion.case_id == case_id)
        )
    assert candidate.review_state == "candidate"
    ClaimService(db_session_factory).confirm_candidate(candidate.id)
    with db_session_factory() as session:
        assert session.get(ClaimRevision, candidate.id).review_state == "confirmed"


def test_multiline_pdf_style_result_is_not_reduced_to_a_fragment(db_session_factory, tmp_path):
    manuscript = tmp_path / "paper.md"
    manuscript.write_text(
        "# Results\n\nOur evaluation reveals that the model consistently\n"
        "outperforms BM25 by 7.0 exact-match points across three domains.\n\n"
        "We study retrieval-augmented question answering in open research settings. "
        "The project context and setup are described before the measurements.\n",
        encoding="utf-8",
    )
    case_id = CaseService(db_session_factory).create_case(
        title="Multiline test", research_question="Does the model outperform BM25?",
        manuscript_path=manuscript,
    )
    with db_session_factory() as session:
        candidate = session.scalar(
            select(ClaimRevision).join(ManuscriptVersion).where(ManuscriptVersion.case_id == case_id)
        )
        stored = session.scalar(
            select(ManuscriptVersion).where(ManuscriptVersion.case_id == case_id)
        )
    assert "outperforms BM25" in candidate.statement
    assert "\n" in candidate.source_quote
    assert candidate.source_quote in stored.content_text


def test_tex_and_markdown_parsers_have_stable_locators(golden_dir, tmp_path):
    latex = LatexParser().parse(golden_dir / "own_paper.tex")
    markdown_path = tmp_path / "notes.md"
    markdown_path.write_text("# Results\n\nA result improves by 2 points.", encoding="utf-8")
    markdown = MarkdownParser().parse(markdown_path)
    assert latex.sections[0].locator.startswith("sec:")
    assert markdown.paragraphs[0].locator == "sec:results:p:1"
    assert len(latex.content_hash) == 64


def test_pdf_section_heading_does_not_leak_into_empirical_claim():
    content = (
        "Experiments across RemPlan and three benchmarks demonstrate a 13% accuracy gain "
        "while reducing redundant searches by 37%.\n"
        "1 Introduction\n"
        "Visual question answering systems often require external knowledge.\n"
        "E-Agent Response: This waterfall reaches a height of 979 meters. "
        "User: Is this food suitable?"
    )
    quotes = [item[2] for item in _rank_candidate_spans(content)]
    assert quotes[0].endswith("37%.")
    assert "Introduction" not in quotes[0]
    assert all("E-Agent Response:" not in quote for quote in quotes)


def _make_case_with_candidate(db_session_factory, tmp_path, text: str) -> tuple[str, str]:
    manuscript = tmp_path / "paper.md"
    manuscript.write_text(text, encoding="utf-8")
    case_id = CaseService(db_session_factory).create_case(
        title="Edit test", research_question="Does it hold?", manuscript_path=manuscript
    )
    with db_session_factory() as session:
        candidate = session.scalar(
            select(ClaimRevision).join(ManuscriptVersion).where(ManuscriptVersion.case_id == case_id)
        )
        assert candidate is not None
        return case_id, candidate.id


def test_edit_candidate_requires_verbatim_quote(db_session_factory, tmp_path):
    """Editing must not confirm a revision whose quote is not in the manuscript."""
    _, candidate_id = _make_case_with_candidate(db_session_factory, tmp_path, (
        "# Results\n\nOur method improves exact match by 7.0 points over BM25.\n\n"
        "We study retrieval-augmented question answering in open research settings. "
        "The project context and setup are described before the measurements."
    ))
    with db_session_factory() as session:
        session.get(ClaimRevision, candidate_id).source_quote = (
            "an invented sentence that never appears in the manuscript"
        )
        session.commit()
    with pytest.raises(ValueError, match="span_failed"):
        ClaimService(db_session_factory).edit_candidate(
            candidate_id, statement="changed statement", centrality="high",
            contract={"task": "retrieval"}, falsifiable_condition="number drops",
        )
    with db_session_factory() as session:
        revision = session.get(ClaimRevision, candidate_id)
        assert revision.review_state == "candidate"  # unchanged
        assert session.scalar(select(func.count()).select_from(ClaimRevision)) == 1


def test_edit_candidate_does_not_autoconfirm_pending_claim(db_session_factory, tmp_path):
    """G0 is a separate human action: editing a pending candidate keeps it candidate."""
    _, candidate_id = _make_case_with_candidate(db_session_factory, tmp_path, (
        "# Results\n\nOur method improves exact match by 7.0 points over BM25.\n\n"
        "We study retrieval-augmented question answering in open research settings. "
        "The project context and setup are described before the measurements."
    ))
    service = ClaimService(db_session_factory)
    revised = service.edit_candidate(
        candidate_id, statement="corrected statement", centrality="high",
        contract={"task": "retrieval"}, falsifiable_condition="number drops",
    )
    assert revised.review_state == "candidate"  # edit did NOT auto-confirm
    assert revised.supersedes_id == candidate_id
    with db_session_factory() as session:
        assert session.get(ClaimRevision, candidate_id).review_state == "superseded"
    # the corrected revision still needs an explicit G0 confirm to become confirmed
    service.confirm_candidate(revised.id)
    with db_session_factory() as session:
        assert session.get(ClaimRevision, revised.id).review_state == "confirmed"


def test_edit_confirmed_claim_stays_confirmed(db_session_factory, tmp_path):
    """Correction path: editing an already-confirmed claim keeps it confirmed."""
    _, candidate_id = _make_case_with_candidate(db_session_factory, tmp_path, (
        "# Results\n\nOur method improves exact match by 7.0 points over BM25.\n\n"
        "We study retrieval-augmented question answering in open research settings. "
        "The project context and setup are described before the measurements."
    ))
    service = ClaimService(db_session_factory)
    service.confirm_candidate(candidate_id)
    revised = service.edit_candidate(
        candidate_id, statement="rewritten statement", centrality="high",
        contract={"task": "retrieval"}, falsifiable_condition="number drops",
    )
    assert revised.review_state == "confirmed"


def test_confirm_and_edit_and_split_record_g0_trail(db_session_factory, tmp_path):
    """M10 regression: every human decision on a claim leaves a G0Feedback
    row and an AuditEvent — including the plain confirm/reject buttons,
    edits, and splits."""
    _, candidate_id = _make_case_with_candidate(db_session_factory, tmp_path, (
        "# Results\n\nOur method improves exact match by 7.0 points over BM25.\n\n"
        "We study retrieval-augmented question answering in open research settings. "
        "The project context and setup are described before the measurements."
    ))
    service = ClaimService(db_session_factory)

    service.confirm_candidate(candidate_id)
    revised = service.edit_candidate(
        candidate_id, statement="corrected statement", centrality="high",
        contract={"task": "retrieval"}, falsifiable_condition="number drops",
    )
    children = service.split_candidate(revised.id, ["Part one.", "Part two."])

    with db_session_factory() as session:
        feedback = list(session.scalars(select(G0Feedback).order_by(G0Feedback.created_at)))
        assert [item.decision for item in feedback] == ["confirmed", "edited", "edited"]
        assert feedback[1].edit_fields_json == [
            "statement", "centrality", "contract", "falsifiable_condition",
        ]
        assert feedback[1].before_values_json["statement"] != feedback[1].after_values_json["statement"]
        assert feedback[2].edit_fields_json == ["split"]
        assert len(feedback[2].after_values_json["child_stable_keys"]) == 2

        events = list(
            session.scalars(
                select(AuditEvent).where(AuditEvent.event_type.like("claim_%"))
            )
        )
        event_types = {event.event_type for event in events}
        assert {"claim_confirmed", "claim_edited", "claim_split"} <= event_types
        split_event = next(event for event in events if event.event_type == "claim_split")
        assert set(split_event.payload_json["child_claim_ids"]) == {
            child.claim_id for child in children
        }


def test_delete_case_removes_g0_feedback(db_session_factory, tmp_path):
    """M10 regression: deleting a case must not leak G0Feedback rows."""
    case_id, candidate_id = _make_case_with_candidate(db_session_factory, tmp_path, (
        "# Results\n\nOur method improves exact match by 7.0 points over BM25.\n\n"
        "We study retrieval-augmented question answering in open research settings. "
        "The project context and setup are described before the measurements."
    ))
    ClaimService(db_session_factory).confirm_candidate(candidate_id)
    with db_session_factory() as session:
        assert session.scalar(select(func.count()).select_from(G0Feedback)) == 1

    from radar.services.case_service import CaseService
    CaseService(db_session_factory).delete_case(case_id)

    with db_session_factory() as session:
        assert session.scalar(select(func.count()).select_from(G0Feedback)) == 0
        assert session.scalar(select(func.count()).select_from(Claim)) == 0


def test_decomposition_uses_real_context_and_drops_invented_atom_quotes(
    db_session_factory, tmp_path
):
    """M5 regression: decomposition must receive real manuscript context
    (never the quote doubled) and only return atoms whose quotes are exact
    manuscript spans."""
    claim_text = (
        "Our method improves exact match by 7.0 points over BM25 on DomainQA "
        "while adding less than 12 percent inference latency."
    )
    manuscript_text = (
        "# Results\n\n"
        f"{claim_text}\n\n"
        "We study retrieval-augmented question answering in open research "
        "settings. The project context and setup are described before the "
        "measurements."
    )

    class DecomposeLLM:
        def __init__(self):
            self.prompts = []

        def generate_structured(self, *, stage, prompt, response_model, max_tokens=None):
            self.prompts.append(prompt)
            return AtomicClaimBatch(atomic_claims=[
                AtomicClaim(
                    statement="Exact-match gain of 7.0 points over BM25.",
                    source_quote=(
                        "improves exact match by 7.0 points over BM25 on DomainQA"
                    ),
                    source_locator="sec:results",
                ),
                AtomicClaim(
                    statement="Latency stays under 12 percent.",
                    source_quote="a sentence that does not exist in the manuscript",
                    source_locator="sec:results",
                ),
            ])

    llm = DecomposeLLM()
    service = ClaimService(db_session_factory, llm_client=llm)
    _, candidate_id = _make_case_with_candidate(
        db_session_factory, tmp_path, manuscript_text
    )

    atoms = service.decompose_to_atomic(candidate_id)

    assert len(atoms) == 1  # invented atom dropped
    assert atoms[0]["source_quote"] == (
        "improves exact match by 7.0 points over BM25 on DomainQA"
    )
    assert atoms[0]["source_locator"].startswith("offset:")
    # The prompt carried the real section context, not the quote doubled:
    # surrounding prose the quote*2 trick could never contain.
    assert "MANUSCRIPT CONTEXT" in llm.prompts[0]
    assert "We study retrieval-augmented question answering" in llm.prompts[0]
    with db_session_factory() as session:
        run = session.scalar(
            select(ModelRun).where(ModelRun.stage == "claim_atomic_decomposition")
        )
        assert run is not None
        assert run.validation_json["atoms_dropped_unanchored"] == 1


def test_verification_failure_is_unverified_not_faithful(db_session_factory, tmp_path):
    """M6 regression: when the LLM is unavailable or fails, verification must
    report an explicit unverified state, never faithful: True."""
    _, candidate_id = _make_case_with_candidate(db_session_factory, tmp_path, (
        "# Results\n\nOur method improves exact match by 7.0 points over BM25.\n\n"
        "We study retrieval-augmented question answering in open research settings. "
        "The project context and setup are described before the measurements."
    ))
    service = ClaimService(db_session_factory, llm_client=None)

    result = service.verify_claim_semantics(candidate_id)

    assert result["verified"] == "skipped_no_llm"
    assert result["faithful"] is None
    assert result["claim_stable_key"]  # real stable key, not ""

    class FailingLLM:
        def generate_structured(self, *, stage, prompt, response_model, max_tokens=None):
            raise RuntimeError("boom")

    failed = ClaimService(db_session_factory, llm_client=FailingLLM()).verify_claim_semantics(
        candidate_id
    )
    assert failed["verified"] == "failed_llm_call"
    assert failed["faithful"] is None


def test_manuscript_without_text_layer_is_rejected_upfront(db_session_factory, tmp_path):
    manuscript = tmp_path / "scanned.md"
    manuscript.write_text("# Scanned\n\n  \n", encoding="utf-8")
    with pytest.raises(ValueError, match="扫描件或缺少文本层"):
        CaseService(db_session_factory).create_case(
            title="Scanned upload",
            research_question="Is there a text layer?",
            manuscript_path=manuscript,
        )
    with db_session_factory() as session:
        assert session.scalar(select(ResearchCase)) is None
