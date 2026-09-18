"""Citation health checks for manuscript references and indexed sources."""

from radar.models import ManuscriptVersion, ResearchCase, Source
from radar.services.citation_service import CitationService


def test_citation_health_reports_unresolved_keys_and_integrity(db_session_factory):
    manuscript = r"""
\section{Introduction}
We build on prior work \cite{known,missing}.

\section{References}
\begin{thebibliography}{9}
\bibitem{known} Known paper. doi:10.1234/known
\bibitem{unused} Unused paper. arXiv:2401.12345
\end{thebibliography}
"""
    with db_session_factory() as session:
        session.add(ResearchCase(id="citation-case", title="Citation", research_question="Q"))
        session.flush()
        session.add(
            ManuscriptVersion(
                id="manuscript-1",
                case_id="citation-case",
                version_no=1,
                file_name="paper.tex",
                source_type="tex",
                content_text=manuscript,
                content_hash="hash",
                is_current=True,
            )
        )
        session.add(
            Source(
                id="source-1",
                external_id="doi:10.1234/known",
                source_kind="crossref",
                title="Known paper",
                authors_json=[],
                url="https://doi.org/10.1234/known",
                doi="10.1234/known",
                integrity_state="retracted",
            )
        )
        session.commit()

    health = CitationService(db_session_factory).analyze_case("citation-case")

    assert health["reference_entries"] == 2
    assert health["identifier_count"] == 2
    assert health["resolved_count"] == 1
    assert health["metadata_coverage"] == 0.5
    assert health["unresolved_keys"] == ["missing"]
    assert health["unused_keys"] == ["unused"]
    assert health["integrity"]["retracted"] == 1
    assert any(issue["kind"] == "source_integrity" for issue in health["issues"])
    assert any(issue["kind"] == "unresolved_citation_key" for issue in health["issues"])
