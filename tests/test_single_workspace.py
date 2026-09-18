from pathlib import Path

from radar.services.case_service import CaseService


def test_primary_case_excludes_synthetic_golden_case(
    db_session_factory, golden_case, tmp_path: Path
):
    service = CaseService(db_session_factory)
    assert service.primary_case() is None

    manuscript = tmp_path / "paper.md"
    manuscript.write_text(
        "# Results\n\nOur method improves exact match from 60% to 70% on DomainQA.\n\n"
        "We study retrieval-augmented question answering in open research settings. "
        "The project context and setup are described before the measurements.",
        encoding="utf-8",
    )
    case_id = service.create_case(
        title="My paper",
        research_question="Does our method improve DomainQA?",
        manuscript_path=manuscript,
    )

    assert service.primary_case().id == case_id
