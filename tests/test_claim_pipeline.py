"""ClaimPipeline tests: the G0 quote-anchor gate on persisted candidates (M2)
and incremental-extract correctness (M3/M4).

The pipeline had no direct tests (review finding M47); these cover the
persistence path that was persisting LLM-invented quotes and section-level
locators instead of exact spans, plus carry dedup and re-gating.
"""

import hashlib
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from radar.models import Claim, ClaimRevision, ManuscriptVersion
from radar.schemas import ManuscriptOverview
from radar.services.case_service import CaseService
from radar.services.claim_pipeline import (
    AttributionOutput,
    ClaimCandidate,
    ClaimCandidateBatch,
    ClaimPipeline,
    ClassificationOutput,
)
from radar.services.claim_service import ClaimService

VERBATIM_QUOTE = (
    "RadarNet improves exact match by 7.0 points over BM25 on the "
    "DomainQA unseen-domain split."
)
MANUSCRIPT_TEXT = (
    "# Results\n\n"
    f"{VERBATIM_QUOTE}\n\n"
    "We study retrieval-augmented question answering in open research "
    "settings. The project context and setup are described before the "
    "measurements."
)


class PipelineLLM:
    """Scripted double for all four pipeline LLM stages."""

    def __init__(self):
        self.calls = []

    def generate_structured(self, *, stage, prompt, response_model, max_tokens=None):
        self.calls.append(stage)
        if stage == "manuscript_overview":
            return ManuscriptOverview(
                research_problem="retrieval",
                author_terms=["RadarNet"],
                cited_work_markers=["et al."],
            )
        if stage == "claim_candidate_detection":
            return ClaimCandidateBatch(
                candidates=[
                    ClaimCandidate(
                        statement="RadarNet is the best model ever.",
                        source_quote=(
                            "an invented sentence that is not in the manuscript"
                            " anywhere at all"
                        ),
                        source_locator="sec:results",
                        section_id="sec:results",
                    ),
                    ClaimCandidate(
                        statement=VERBATIM_QUOTE,
                        source_quote=VERBATIM_QUOTE,
                        source_locator="sec:results",
                        section_id="sec:results",
                    ),
                ]
            )
        if stage == "claim_attribution":
            return AttributionOutput(
                attribution="current_author", confidence=0.9
            )
        if stage == "claim_classification":
            return ClassificationOutput(
                claim_type="empirical_result", claim_role="core_conclusion"
            )
        raise AssertionError(f"unexpected stage: {stage}")


def _manuscript_id(db_session_factory, tmp_path):
    manuscript = tmp_path / "paper.md"
    manuscript.write_text(MANUSCRIPT_TEXT, encoding="utf-8")
    case_id = CaseService(db_session_factory).create_case(
        title="Pipeline test",
        research_question="Does RadarNet generalize?",
        manuscript_path=manuscript,
    )
    with db_session_factory() as session:
        return session.scalar(
            select(ManuscriptVersion).where(ManuscriptVersion.case_id == case_id)
        ).id


def test_pipeline_drops_invented_quotes_and_anchors_the_rest(
    db_session_factory, tmp_path
):
    """M2 regression: LLM candidates with non-verbatim quotes are never
    persisted; anchored candidates store the exact span, not section ids."""
    llm = PipelineLLM()
    manuscript_id = _manuscript_id(db_session_factory, tmp_path)

    results = ClaimPipeline(db_session_factory, llm_client=llm).run_pipeline(
        manuscript_id
    )

    assert results["claims_extracted"] == 1
    assert len(results["dropped_anchors"]) == 1
    assert "RadarNet is the best model ever" in results["dropped_anchors"][0]
    with db_session_factory() as session:
        # The case creation runs the heuristic extraction too; the pipeline
        # revision is the one classified as core_conclusion.
        revision = session.scalar(
            select(ClaimRevision)
            .join(Claim, Claim.id == ClaimRevision.claim_id)
            .where(Claim.claim_role == "core_conclusion")
        )
        assert revision is not None
        assert revision.source_quote == VERBATIM_QUOTE
        assert revision.source_locator.startswith("offset:")
        assert revision.review_state == "candidate"
        assert revision.statement == VERBATIM_QUOTE
    assert "claim_candidate_detection" in llm.calls


def _make_versioned_case(db_session_factory, tmp_path, text_v1: str):
    """Create a case with v1 + heuristic candidate; return (service, case_id, v1_id)."""
    manuscript = tmp_path / "paper-v1.md"
    manuscript.write_text(text_v1, encoding="utf-8")
    service = CaseService(db_session_factory)
    case_id = service.create_case(
        title="Incremental test",
        research_question="Does it generalize?",
        manuscript_path=manuscript,
    )
    with db_session_factory() as session:
        v1 = session.scalar(
            select(ManuscriptVersion).where(ManuscriptVersion.case_id == case_id)
        )
        return service, case_id, v1.id


def _add_version(db_session_factory, case_id, version_no, text):
    with db_session_factory() as session:
        version = ManuscriptVersion(
            id=str(uuid4()),
            case_id=case_id,
            version_no=version_no,
            file_name=f"paper-v{version_no}.md",
            source_type="markdown",
            content_text=text,
            content_hash=hashlib.sha256(text.encode()).hexdigest(),
            is_current=True,
        )
        session.add(version)
        session.commit()
        return version.id


def test_incremental_extract_carries_latest_revision_only(db_session_factory, tmp_path):
    """M3 regression: an edited claim leaves two revisions on the previous
    version; only the latest must be carried, producing exactly one revision
    on the current version (no duplicates, no broken DAG)."""
    claim_text = (
        "Our evaluation reveals that RadarNet improves exact match by 7.0 "
        "points over BM25 on the DomainQA unseen-domain split."
    )
    v1_text = (
        "# Results\n\n"
        f"{claim_text}\n\n"
        "We study retrieval-augmented question answering in open research "
        "settings. The project context and setup are described before the "
        "measurements."
    )
    _, case_id, v1_id = _make_versioned_case(db_session_factory, tmp_path, v1_text)

    service = ClaimService(db_session_factory)
    with db_session_factory() as session:
        initial = session.scalar(
            select(ClaimRevision)
            .join(Claim, Claim.id == ClaimRevision.claim_id)
            .where(Claim.case_id == case_id)
        )
        initial_id = initial.id
        claim_id = initial.claim_id
        initial_quote = initial.source_quote
    service.confirm_candidate(initial_id)
    edited = service.edit_candidate(
        initial_id, statement="corrected statement", centrality="high",
        contract={"task": "retrieval"}, falsifiable_condition="number drops",
    )
    edited_id = edited.id
    with db_session_factory() as session:
        assert (
            session.scalar(
                select(func.count()).select_from(ClaimRevision).where(
                    ClaimRevision.manuscript_version_id == v1_id
                )
            )
            == 2
        )

    v2_id = _add_version(
        db_session_factory, case_id, 2,
        "# Results\n\n"
        f"{claim_text}\n\n"
        "We study retrieval-augmented question answering in open research "
        "settings. The project context and setup are described before the "
        "measurements.",
    )
    results = ClaimPipeline(db_session_factory, llm_client=None).incremental_extract(
        v1_id, v2_id
    )

    with db_session_factory() as session:
        current_revisions = list(
            session.scalars(
                select(ClaimRevision).where(
                    ClaimRevision.manuscript_version_id == v2_id
                )
            )
        )
        assert len(current_revisions) == 1  # one per claim, not two
        carried = current_revisions[0]
        assert carried.claim_id == claim_id
        assert carried.review_state == "confirmed"  # exact carry keeps state
        assert carried.source_quote == initial_quote
        assert carried.supersedes_id == edited_id
        # every previous revision is superseded — no live sibling remains
        assert session.scalar(
            select(func.count()).select_from(ClaimRevision).where(
                ClaimRevision.manuscript_version_id == v1_id,
                ClaimRevision.review_state != "superseded",
            )
        ) == 0
    assert results["claims_carried"] == 1


def test_incremental_extract_regates_confirmed_claim_in_changed_section(
    db_session_factory, tmp_path
):
    """M3 regression: a confirmed claim whose section changed is carried with
    the claim identity but downgraded to candidate (G0 re-gate) — never
    orphaned, never duplicated as a brand-new claim."""
    claim_text = (
        "Our evaluation reveals that RadarNet improves exact match by 7.0 "
        "points over BM25 on the DomainQA unseen-domain split."
    )
    v1_text = (
        "# Results\n\n"
        f"{claim_text}\n\n"
        "We study retrieval-augmented question answering in open research "
        "settings. The project context and setup are described before the "
        "measurements."
    )
    _, case_id, v1_id = _make_versioned_case(db_session_factory, tmp_path, v1_text)

    service = ClaimService(db_session_factory)
    with db_session_factory() as session:
        initial = session.scalar(
            select(ClaimRevision)
            .join(Claim, Claim.id == ClaimRevision.claim_id)
            .where(Claim.case_id == case_id)
        )
        initial_id = initial.id
        claim_id = initial.claim_id
        initial_quote = initial.source_quote
    service.confirm_candidate(initial_id)

    # v2 rewrites the Results section (the claim's section changed).
    v2_text = (
        "# Results\n\n"
        f"{claim_text}\n\n"
        "NEW PARAGRAPH describing an additional evaluation that changes the "
        "section content beyond the claim's sentence."
    )
    v2_id = _add_version(db_session_factory, case_id, 2, v2_text)

    ClaimPipeline(db_session_factory, llm_client=None).incremental_extract(
        v1_id, v2_id
    )

    with db_session_factory() as session:
        claim = session.get(Claim, claim_id)
        assert claim.track_state == "needs_revalidation"
        current_revisions = list(
            session.scalars(
                select(ClaimRevision).where(
                    ClaimRevision.manuscript_version_id == v2_id
                )
            )
        )
        # The claim is carried (identity kept) with a re-gated candidate
        # revision, and no duplicate claim was minted for the same text.
        assert len(current_revisions) == 1
        carried = current_revisions[0]
        assert carried.claim_id == claim_id
        assert carried.review_state == "candidate"
        assert carried.source_quote == initial_quote


def test_track_changes_compares_latest_revisions(db_session_factory, tmp_path):
    """M1 regression: with multiple revisions per claim (edits), the diff must
    compare the LATEST revision on each version — not the first row."""
    claim_text = (
        "Our evaluation reveals that RadarNet improves exact match by 7.0 "
        "points over BM25 on the DomainQA unseen-domain split."
    )
    v1_text = (
        "# Results\n\n"
        f"{claim_text}\n\n"
        "We study retrieval-augmented question answering in open research "
        "settings. The project context and setup are described before the "
        "measurements."
    )
    _, case_id, v1_id = _make_versioned_case(db_session_factory, tmp_path, v1_text)

    service = ClaimService(db_session_factory)
    with db_session_factory() as session:
        initial = session.scalar(
            select(ClaimRevision)
            .join(Claim, Claim.id == ClaimRevision.claim_id)
            .where(Claim.case_id == case_id)
        )
        initial_id = initial.id
    edited = service.edit_candidate(
        initial_id, statement="edited statement v1.1", centrality="high",
        contract={"task": "retrieval"}, falsifiable_condition="number drops",
    )

    # v2 carries the edited statement unchanged → claim is NOT modified.
    v2_id = _add_version(
        db_session_factory, case_id, 2,
        "# Results\n\n"
        f"{claim_text}\n\n"
        "We study retrieval-augmented question answering in open research "
        "settings. The project context and setup are described before the "
        "measurements.",
    )
    with db_session_factory() as session:
        # Simulate the sync path: carry the latest (edited) revision to v2.
        v2_rev = ClaimRevision(
            id=str(uuid4()),
            claim_id=edited.claim_id,
            manuscript_version_id=v2_id,
            revision_no=2,
            statement=edited.statement,
            claim_type=edited.claim_type,
            centrality=edited.centrality,
            contract_json=edited.contract_json,
            falsifiable_condition=edited.falsifiable_condition,
            source_quote=edited.source_quote,
            source_locator=edited.source_locator,
            review_state="confirmed",
            supersedes_id=edited.id,
        )
        session.add(v2_rev)
        session.commit()

    results = ClaimPipeline(db_session_factory, llm_client=None).track_changes(
        v1_id, v2_id
    )

    # The edited statement carried over unchanged → unchanged, not modified
    # (the old first-row comparison saw the ORIGINAL statement and flagged a
    # phantom modification).
    assert results["unchanged"] == 1
    assert results["modified"] == 0
    assert results["modified_details"] == []


def test_pipeline_heuristic_path_only_persists_verbatim_quotes(
    db_session_factory, tmp_path
):
    """The no-LLM fallback must also persist exact spans with offset locators."""
    manuscript_id = _manuscript_id(db_session_factory, tmp_path)

    results = ClaimPipeline(db_session_factory, llm_client=None).run_pipeline(
        manuscript_id
    )

    assert results["claims_extracted"] >= 1
    with db_session_factory() as session:
        revisions = list(session.scalars(select(ClaimRevision)))
        assert revisions
        for revision in revisions:
            assert revision.source_locator.startswith("offset:")
            start, end = (
                int(part)
                for part in revision.source_locator[len("offset:"):].split("-")
            )
            assert MANUSCRIPT_TEXT[start:end] == revision.source_quote
