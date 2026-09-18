from sqlalchemy import select

from radar.models import Claim, ClaimRevision, ManuscriptVersion
from radar.services.case_service import CaseService
from radar.services.claim_service import ClaimService


def test_new_manuscript_version_carries_exact_claim_and_extracts_new_candidate(
    db_session_factory, tmp_path
):
    first = tmp_path / "paper-v1.md"
    stable_claim = (
        "Our evaluation reveals that RadarNet improves exact match by 7.0 points "
        "over BM25 on the DomainQA unseen-domain split."
    )
    first.write_text(
        f"# Results\n\n{stable_claim}\n\n"
        "We study retrieval-augmented question answering in open research settings. "
        "The project context and setup are described before the measurements.",
        encoding="utf-8",
    )
    service = CaseService(db_session_factory)
    case_id = service.create_case(
        title="Versioned project",
        research_question="Does RadarNet improve unseen-domain retrieval?",
        manuscript_path=first,
    )
    with db_session_factory() as session:
        initial = session.scalar(
            select(ClaimRevision)
            .join(Claim, Claim.id == ClaimRevision.claim_id)
            .where(Claim.case_id == case_id)
        )
    ClaimService(db_session_factory).confirm_candidate(initial.id)

    second = tmp_path / "paper-v2.md"
    new_claim = (
        "Our ablation analysis shows that removing retrieval calibration reduces "
        "exact match by 4.0 points on DomainQA."
    )
    second.write_text(
        f"# Results\n\n{stable_claim}\n\n{new_claim}", encoding="utf-8"
    )
    result = service.add_manuscript_version(case_id, second)

    with db_session_factory() as session:
        versions = list(
            session.scalars(
                select(ManuscriptVersion)
                .where(ManuscriptVersion.case_id == case_id)
                .order_by(ManuscriptVersion.version_no)
            )
        )
        rows = list(
            session.execute(
                select(Claim, ClaimRevision)
                .join(ClaimRevision, ClaimRevision.claim_id == Claim.id)
                .where(Claim.case_id == case_id)
                .order_by(Claim.stable_key, ClaimRevision.revision_no)
            )
        )

    assert [item.version_no for item in versions] == [1, 2]
    assert [item.is_current for item in versions] == [False, True]
    assert result["carried_claims"] == 1
    assert result["new_candidates"] == 1
    c1_revisions = [revision for claim, revision in rows if claim.stable_key == "C1"]
    c2_revisions = [revision for claim, revision in rows if claim.stable_key == "C2"]
    assert [item.review_state for item in c1_revisions] == ["superseded", "confirmed"]
    assert c1_revisions[-1].manuscript_version_id == versions[-1].id
    assert len(c2_revisions) == 1
    assert c2_revisions[0].review_state == "candidate"


def test_fuzzy_carry_downgrades_confirmed_claim_to_candidate(
    db_session_factory, tmp_path
):
    """C4 regression: a claim carried onto materially changed text (a reported
    number edited) must not stay confirmed without a new human gate."""
    first = tmp_path / "paper-v1.md"
    first.write_text(
        "# Results\n\n"
        "Our model achieves 92.1% accuracy on SQuAD in the zero-shot setting.\n\n"
        "We study question answering in open research settings. "
        "The project context and setup are described before the measurements.",
        encoding="utf-8",
    )
    service = CaseService(db_session_factory)
    case_id = service.create_case(
        title="Fuzzy carry project",
        research_question="Does the model generalize?",
        manuscript_path=first,
    )
    with db_session_factory() as session:
        initial = session.scalar(
            select(ClaimRevision)
            .join(Claim, Claim.id == ClaimRevision.claim_id)
            .where(Claim.case_id == case_id)
        )
        initial_statement = initial.statement
    ClaimService(db_session_factory).confirm_candidate(initial.id)

    # v2 rewrites the same sentence with a different number (high similarity,
    # materially changed content).
    second = tmp_path / "paper-v2.md"
    second.write_text(
        "# Results\n\n"
        "Our model achieves 91.2% accuracy on SQuAD in the zero-shot setting.\n\n"
        "We study question answering in open research settings. "
        "The project context and setup are described before the measurements.",
        encoding="utf-8",
    )
    result = service.add_manuscript_version(case_id, second)

    assert result["carried_claims"] == 1
    with db_session_factory() as session:
        claim = session.scalar(
            select(Claim).where(Claim.case_id == case_id, Claim.stable_key == "C1")
        )
        revisions = list(
            session.scalars(
                select(ClaimRevision)
                .where(ClaimRevision.claim_id == claim.id)
                .order_by(ClaimRevision.revision_no)
            )
        )
    # Fuzzy carry: the carried revision is a candidate again (re-gate), and it
    # carries the OLD statement while spanning the NEW text — exactly the
    # mismatch a human must review before it counts as confirmed.
    assert [item.review_state for item in revisions] == ["superseded", "candidate"]
    assert revisions[-1].statement == initial_statement
    # The carried span covers the NEW text (the edited number), not the old one.
    assert "91.2%" in revisions[-1].source_quote
    assert "92.1%" not in revisions[-1].source_quote
    assert revisions[-1].source_quote in (second.read_text(encoding="utf-8"))


def test_uploading_identical_manuscript_does_not_create_duplicate_version(
    db_session_factory, tmp_path
):
    manuscript = tmp_path / "paper.md"
    manuscript.write_text(
        "# Results\n\nOur method improves exact match by 7.0 points over BM25.\n\n"
        "We study retrieval-augmented question answering in open research settings. "
        "The project context and setup are described before the measurements.",
        encoding="utf-8",
    )
    service = CaseService(db_session_factory)
    case_id = service.create_case(
        title="No duplicate versions",
        research_question="Does it improve?",
        manuscript_path=manuscript,
    )
    result = service.add_manuscript_version(case_id, manuscript)

    with db_session_factory() as session:
        versions = list(
            session.scalars(
                select(ManuscriptVersion).where(ManuscriptVersion.case_id == case_id)
            )
        )
    assert result["unchanged"] is True
    assert len(versions) == 1
