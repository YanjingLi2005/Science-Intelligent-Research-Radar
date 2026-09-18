"""Literature source management, BibTeX export, tags, and cost-by-source API routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import exists, func, or_, select

from radar.api_schemas import (
    RetrievalReceiptOut,
    SourceBulkTagsRequest,
    SourceImportRequest,
    SourceRemoveRequest,
    SourceTagsRequest,
)
from radar.db import get_session_factory, session_scope
from radar.models import (
    ActionItem,
    Claim,
    ClaimRevision,
    ClaimSourceLink,
    ImpactCandidate,
    ModelRun,
    PatchProposal,
    ResearchCase,
    ReviewDecision,
    ScanRun,
    Source,
    SourceSnapshot,
)
from radar.services.case_service import CaseService
from radar.services.source_import_service import SourceImportService
from radar.routes.builders import _build_retrieval_receipts
from radar.routes.deps import _service, _set_case_source_tags, _source_tags

router = APIRouter(tags=["sources"])


def _bibtex_escape(value: str) -> str:
    """Escape the small set of BibTeX special characters used by sources."""
    return (
        value.replace("&", r"\&")
        .replace("%", r"\%")
        .replace("_", r"\_")
        .replace("#", r"\#")
    )


@router.get("/api/cases/{case_id}/sources")
def list_sources(
    case_id: str,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    source_kind: str | None = Query(default=None),
    ccf_rank: str | None = Query(default=None),
    tag: str | None = Query(default=None),
) -> list[dict[str, Any]]:
    """Return case-referenced literature sources and their snapshot counts."""
    case = _service(CaseService).get_case(case_id)
    if case is None:
        raise HTTPException(404, f"case not found: {case_id}")

    with session_scope() as session:
        impact_source_for_case = exists(
            select(1)
            .select_from(SourceSnapshot)
            .join(
                ImpactCandidate,
                ImpactCandidate.source_snapshot_id == SourceSnapshot.id,
            )
            .join(ScanRun, ScanRun.id == ImpactCandidate.scan_run_id)
            .where(
                SourceSnapshot.source_id == Source.id,
                ScanRun.case_id == case_id,
            )
        )
        claim_source_for_case = exists(
            select(1)
            .select_from(SourceSnapshot)
            .join(
                ClaimSourceLink,
                ClaimSourceLink.source_id == SourceSnapshot.source_id,
            )
            .join(
                ClaimRevision,
                ClaimRevision.id == ClaimSourceLink.claim_revision_id,
            )
            .join(Claim, Claim.id == ClaimRevision.claim_id)
            .where(
                SourceSnapshot.source_id == Source.id,
                Claim.case_id == case_id,
            )
        )
        query = (
            select(Source, func.count(SourceSnapshot.id).label("snapshot_count"))
            .outerjoin(SourceSnapshot, SourceSnapshot.source_id == Source.id)
            .where(or_(impact_source_for_case, claim_source_for_case))
        )
        if source_kind is not None:
            query = query.where(Source.source_kind == source_kind)
        if ccf_rank is not None:
            query = query.where(Source.ccf_rank == ccf_rank)

        ordered_query = query.group_by(Source.id).order_by(Source.created_at.desc())
        if tag is None:
            rows = session.execute(ordered_query.offset(offset).limit(limit)).all()
        else:
            rows = session.execute(ordered_query).all()
            rows = [
                row for row in rows if tag in _source_tags(case, row[0].id)
            ][offset : offset + limit]

        return [
            {
                "id": source.id,
                "title": source.title,
                "authors": list(source.authors_json or []),
                "source_kind": source.source_kind,
                "venue": source.venue,
                "doi": source.doi,
                "arxiv_id": source.arxiv_id,
                "ccf_rank": source.ccf_rank,
                "fields_of_study": list(source.fields_of_study_json or []),
                "tags": _source_tags(case, source.id),
                "snapshot_count": int(snapshot_count),
                "created_at": source.created_at.isoformat() if source.created_at else None,
            }
            for source, snapshot_count in rows
        ]


@router.get("/api/cases/{case_id}/sources/bibtex")
def export_sources_bibtex(case_id: str) -> dict[str, str]:
    """Export every source referenced by a case as a simple BibTeX file."""
    case = _service(CaseService).get_case(case_id)
    if case is None:
        raise HTTPException(404, f"case not found: {case_id}")

    with session_scope() as session:
        impact_source_for_case = exists(
            select(1)
            .select_from(SourceSnapshot)
            .join(ImpactCandidate, ImpactCandidate.source_snapshot_id == SourceSnapshot.id)
            .join(ScanRun, ScanRun.id == ImpactCandidate.scan_run_id)
            .where(
                SourceSnapshot.source_id == Source.id,
                ScanRun.case_id == case_id,
            )
        )
        claim_source_for_case = exists(
            select(1)
            .select_from(SourceSnapshot)
            .join(ClaimSourceLink, ClaimSourceLink.source_id == SourceSnapshot.source_id)
            .join(ClaimRevision, ClaimRevision.id == ClaimSourceLink.claim_revision_id)
            .join(Claim, Claim.id == ClaimRevision.claim_id)
            .where(
                SourceSnapshot.source_id == Source.id,
                Claim.case_id == case_id,
            )
        )
        rows = session.execute(
            select(Source)
            .where(or_(impact_source_for_case, claim_source_for_case))
            .order_by(Source.created_at.desc())
            .limit(1000)
        ).scalars().all()

    entries: list[str] = []
    for source in rows:
        publication_type = (source.publication_type or "").lower()
        entry_type = (
            "article"
            if publication_type in {"article", "journal_article", "conference_paper", "review"}
            else "misc"
        )
        authors = [str(author) for author in (source.authors_json or []) if author]
        author_value = " and ".join(_bibtex_escape(author) for author in authors)
        fields = [
            f"  author = {{{author_value}}}",
            f"  title = {{{_bibtex_escape(source.title or '')}}}",
        ]
        if source.venue:
            fields.append(f"  journal = {{{_bibtex_escape(source.venue)}}}")
        if source.published_at:
            fields.append(f"  year = {{{source.published_at.year}}}")
        if source.doi:
            fields.append(f"  doi = {{{_bibtex_escape(source.doi)}}}")
        if source.url:
            fields.append(f"  url = {{{_bibtex_escape(source.url)}}}")
        if source.arxiv_id:
            fields.extend(
                [
                    f"  eprint = {{{_bibtex_escape(source.arxiv_id)}}}",
                    "  archivePrefix = {arXiv}",
                ]
            )
        entries.append(f"@{entry_type}{{{source.id},\n" + ",\n".join(fields) + "\n}")

    return {
        "filename": f"sources-{case_id}.bib",
        "content": "\n\n".join(entries) + ("\n" if entries else ""),
    }


@router.get("/api/cases/{case_id}/cost-by-source")
def cost_by_source(case_id: str) -> dict[str, Any]:
    """Return LLM cost grouped by the source kind in each run's inputs."""
    with session_scope(get_session_factory()) as session:
        if session.get(ResearchCase, case_id) is None:
            raise HTTPException(404, f"case not found: {case_id}")

        runs = list(
            session.scalars(
                select(ModelRun)
                .where(ModelRun.case_id == case_id)
                .order_by(ModelRun.created_at.asc())
            )
        )

        snapshot_refs_by_run: list[list[str]] = []
        snapshot_ids: set[str] = set()
        for run in runs:
            refs = [
                ref
                for ref in (run.input_refs_json or [])
                if isinstance(ref, str)
            ]
            snapshot_refs_by_run.append(refs)
            snapshot_ids.update(refs)

        snapshot_source_kinds: dict[str, str] = {}
        if snapshot_ids:
            snapshot_rows = session.execute(
                select(SourceSnapshot.id, Source.source_kind)
                .join(Source, Source.id == SourceSnapshot.source_id)
                .where(SourceSnapshot.id.in_(snapshot_ids))
            )
            snapshot_source_kinds = {
                snapshot_id: str(source_kind or "unknown")
                for snapshot_id, source_kind in snapshot_rows
            }

        grouped: dict[str, dict[str, float | int]] = {}
        total_cost = 0.0
        for run, refs in zip(runs, snapshot_refs_by_run):
            run_cost = float(run.estimated_cost or 0.0)
            total_cost += run_cost
            snapshot_refs = [
                ref for ref in refs if ref in snapshot_source_kinds
            ]
            if not snapshot_refs:
                bucket = grouped.setdefault(
                    "unattributed", {"cost_usd": 0.0, "run_count": 0}
                )
                bucket["cost_usd"] += run_cost
                bucket["run_count"] += 1
                continue

            cost_per_snapshot = run_cost / len(snapshot_refs)
            source_kinds = {
                snapshot_source_kinds[ref] for ref in snapshot_refs
            }
            for source_kind in source_kinds:
                bucket = grouped.setdefault(
                    source_kind, {"cost_usd": 0.0, "run_count": 0}
                )
                bucket["cost_usd"] += sum(
                    cost_per_snapshot
                    for ref in snapshot_refs
                    if snapshot_source_kinds[ref] == source_kind
                )
                bucket["run_count"] += 1

        return {
            "items": [
                {
                    "source_kind": source_kind,
                    "cost_usd": float(values["cost_usd"]),
                    "run_count": int(values["run_count"]),
                }
                for source_kind, values in sorted(grouped.items())
            ],
            "total_cost_usd": float(total_cost),
        }


@router.get("/api/cases/{case_id}/sources/{source_id}")
def get_source(case_id: str, source_id: str) -> dict[str, Any]:
    """Return one case-referenced source and its related evidence."""
    case = _service(CaseService).get_case(case_id)
    if case is None:
        raise HTTPException(404, f"case not found: {case_id}")

    with session_scope() as session:
        impact_source_for_case = exists(
            select(1)
            .select_from(SourceSnapshot)
            .join(
                ImpactCandidate,
                ImpactCandidate.source_snapshot_id == SourceSnapshot.id,
            )
            .join(ScanRun, ScanRun.id == ImpactCandidate.scan_run_id)
            .where(
                SourceSnapshot.source_id == Source.id,
                ScanRun.case_id == case_id,
            )
        )
        claim_source_for_case = exists(
            select(1)
            .select_from(SourceSnapshot)
            .join(
                ClaimSourceLink,
                ClaimSourceLink.source_id == SourceSnapshot.source_id,
            )
            .join(
                ClaimRevision,
                ClaimRevision.id == ClaimSourceLink.claim_revision_id,
            )
            .join(Claim, Claim.id == ClaimRevision.claim_id)
            .where(
                SourceSnapshot.source_id == Source.id,
                Claim.case_id == case_id,
            )
        )
        source = session.scalar(
            select(Source).where(
                Source.id == source_id,
                or_(impact_source_for_case, claim_source_for_case),
            )
        )
        if source is None:
            raise HTTPException(404, f"source not found: {source_id}")

        snapshots = list(
            session.scalars(
                select(SourceSnapshot)
                .where(SourceSnapshot.source_id == source_id)
                .order_by(SourceSnapshot.observed_at.desc(), SourceSnapshot.id.desc())
            )
        )
        related_impact_ids = list(
            session.scalars(
                select(ImpactCandidate.id)
                .join(
                    SourceSnapshot,
                    ImpactCandidate.source_snapshot_id == SourceSnapshot.id,
                )
                .join(ScanRun, ScanRun.id == ImpactCandidate.scan_run_id)
                .where(
                    SourceSnapshot.source_id == source_id,
                    ScanRun.case_id == case_id,
                )
                .order_by(ImpactCandidate.created_at, ImpactCandidate.id)
            )
        )
        related_claim_ids = list(
            session.scalars(
                select(Claim.id)
                .join(ClaimRevision, ClaimRevision.claim_id == Claim.id)
                .join(
                    ClaimSourceLink,
                    ClaimSourceLink.claim_revision_id == ClaimRevision.id,
                )
                .where(
                    Claim.case_id == case_id,
                    ClaimSourceLink.source_id == source_id,
                )
                .distinct()
                .order_by(Claim.id)
            )
        )

        return {
            "id": source.id,
            "title": source.title,
            "authors": list(source.authors_json or []),
            "source_kind": source.source_kind,
            "venue": source.venue,
            "doi": source.doi,
            "arxiv_id": source.arxiv_id,
            "arxiv_primary_category": source.arxiv_primary_category,
            "fields_of_study": list(source.fields_of_study_json or []),
            "ccf_rank": source.ccf_rank,
            "tags": _source_tags(case, source.id),
            "pdf_url": source.pdf_url,
            "cited_by_count": source.cited_by_count,
            "integrity_state": source.integrity_state,
            "created_at": source.created_at.isoformat() if source.created_at else None,
            "snapshots": [
                {
                    "id": snapshot.id,
                    "version_label": snapshot.version_label,
                    "title": snapshot.title,
                    "abstract": snapshot.abstract,
                    "content_hash": snapshot.content_hash,
                    "observed_at": (
                        snapshot.observed_at.isoformat()
                        if snapshot.observed_at
                        else None
                    ),
                }
                for snapshot in snapshots
            ],
            "related_impact_ids": related_impact_ids,
            "related_claim_ids": related_claim_ids,
        }


@router.put("/api/cases/{case_id}/sources/{source_id}/tags")
def set_source_tags(
    case_id: str, source_id: str, body: SourceTagsRequest
) -> dict[str, Any]:
    """Set the tags for one source within a case."""
    with session_scope(get_session_factory()) as session:
        case = session.get(ResearchCase, case_id)
        if case is None:
            raise HTTPException(404, f"case not found: {case_id}")

        impact_source_for_case = exists(
            select(1)
            .select_from(SourceSnapshot)
            .join(
                ImpactCandidate,
                ImpactCandidate.source_snapshot_id == SourceSnapshot.id,
            )
            .join(ScanRun, ScanRun.id == ImpactCandidate.scan_run_id)
            .where(
                SourceSnapshot.source_id == Source.id,
                ScanRun.case_id == case_id,
            )
        )
        claim_source_for_case = exists(
            select(1)
            .select_from(SourceSnapshot)
            .join(
                ClaimSourceLink,
                ClaimSourceLink.source_id == SourceSnapshot.source_id,
            )
            .join(
                ClaimRevision,
                ClaimRevision.id == ClaimSourceLink.claim_revision_id,
            )
            .join(Claim, Claim.id == ClaimRevision.claim_id)
            .where(
                SourceSnapshot.source_id == Source.id,
                Claim.case_id == case_id,
            )
        )
        source = session.scalar(
            select(Source).where(
                Source.id == source_id,
                or_(impact_source_for_case, claim_source_for_case),
            )
        )
        if source is None:
            raise HTTPException(404, f"source not found: {source_id}")

        tags = _set_case_source_tags(case, [source_id], body.tags)
        return {"source_id": source_id, "tags": tags}


@router.post("/api/cases/{case_id}/sources/tags")
def set_source_bulk_tags(
    case_id: str, body: SourceBulkTagsRequest
) -> dict[str, list[str]]:
    """Set the same tags for multiple sources referenced by a case."""
    with session_scope(get_session_factory()) as session:
        case = session.get(ResearchCase, case_id)
        if case is None:
            raise HTTPException(404, f"case not found: {case_id}")
        if not body.source_ids:
            raise HTTPException(400, "source_ids must not be empty")

        source_ids = list(body.source_ids)
        impact_source_for_case = exists(
            select(1)
            .select_from(SourceSnapshot)
            .join(
                ImpactCandidate,
                ImpactCandidate.source_snapshot_id == SourceSnapshot.id,
            )
            .join(ScanRun, ScanRun.id == ImpactCandidate.scan_run_id)
            .where(
                SourceSnapshot.source_id == Source.id,
                ScanRun.case_id == case_id,
            )
        )
        claim_source_for_case = exists(
            select(1)
            .select_from(SourceSnapshot)
            .join(
                ClaimSourceLink,
                ClaimSourceLink.source_id == SourceSnapshot.source_id,
            )
            .join(
                ClaimRevision,
                ClaimRevision.id == ClaimSourceLink.claim_revision_id,
            )
            .join(Claim, Claim.id == ClaimRevision.claim_id)
            .where(
                SourceSnapshot.source_id == Source.id,
                Claim.case_id == case_id,
            )
        )
        referenced_source_ids = set(
            session.scalars(
                select(Source.id).where(
                    Source.id.in_(source_ids),
                    or_(impact_source_for_case, claim_source_for_case),
                )
            )
        )
        missing_source_ids = [
            source_id
            for source_id in source_ids
            if source_id not in referenced_source_ids
        ]
        if missing_source_ids:
            raise HTTPException(
                404, f"source not found: {', '.join(missing_source_ids)}"
            )

        _set_case_source_tags(case, source_ids, body.tags)
        return {"updated_source_ids": source_ids}


@router.post("/api/cases/{case_id}/sources/remove")
def remove_sources(
    case_id: str, body: SourceRemoveRequest
) -> dict[str, int]:
    """Remove source references from a case without deleting global sources."""
    case = _service(CaseService).get_case(case_id)
    if case is None:
        raise HTTPException(404, f"case not found: {case_id}")
    if not body.source_ids:
        raise HTTPException(400, "source_ids must not be empty")

    from sqlalchemy import delete

    source_ids = set(body.source_ids)
    with session_scope() as session:
        impact_source_ids = set(
            session.scalars(
                select(SourceSnapshot.source_id)
                .join(
                    ImpactCandidate,
                    ImpactCandidate.source_snapshot_id == SourceSnapshot.id,
                )
                .join(ScanRun, ScanRun.id == ImpactCandidate.scan_run_id)
                .where(
                    SourceSnapshot.source_id.in_(source_ids),
                    ScanRun.case_id == case_id,
                )
                .distinct()
            )
        )
        claim_source_ids = set(
            session.scalars(
                select(ClaimSourceLink.source_id)
                .join(
                    ClaimRevision,
                    ClaimRevision.id == ClaimSourceLink.claim_revision_id,
                )
                .join(Claim, Claim.id == ClaimRevision.claim_id)
                .where(
                    ClaimSourceLink.source_id.in_(source_ids),
                    Claim.case_id == case_id,
                )
                .distinct()
            )
        )

        impact_ids = select(ImpactCandidate.id).where(
            ImpactCandidate.source_snapshot_id.in_(
                select(SourceSnapshot.id).where(
                    SourceSnapshot.source_id.in_(source_ids)
                )
            ),
            ImpactCandidate.scan_run_id.in_(
                select(ScanRun.id).where(ScanRun.case_id == case_id)
            ),
        )
        session.execute(
            delete(ActionItem).where(ActionItem.impact_candidate_id.in_(impact_ids))
        )
        session.execute(
            delete(ReviewDecision).where(
                ReviewDecision.impact_candidate_id.in_(impact_ids)
            )
        )
        session.execute(
            delete(PatchProposal).where(
                PatchProposal.impact_candidate_id.in_(impact_ids)
            )
        )
        session.execute(
            delete(ImpactCandidate).where(ImpactCandidate.id.in_(impact_ids))
        )

        case_revision_ids = (
            select(ClaimRevision.id)
            .join(Claim, Claim.id == ClaimRevision.claim_id)
            .where(Claim.case_id == case_id)
        )
        session.execute(
            delete(ClaimSourceLink).where(
                ClaimSourceLink.source_id.in_(source_ids),
                ClaimSourceLink.claim_revision_id.in_(case_revision_ids),
            )
        )

    return {"removed_source_count": len(impact_source_ids | claim_source_ids)}


@router.get(
    "/api/cases/{case_id}/retrieval-receipts",
    response_model=list[RetrievalReceiptOut],
)
def list_retrieval_receipts(case_id: str) -> list[dict[str, Any]]:
    """Return the latest bounded external-search receipts for a case."""
    case = _service(CaseService).get_case(case_id)
    if case is None:
        raise HTTPException(404, f"case not found: {case_id}")
    return _build_retrieval_receipts(case_id)


@router.post("/api/cases/{case_id}/sources/import")
def import_source(case_id: str, body: SourceImportRequest) -> dict[str, Any]:
    """Import one external paper (arXiv URL / DOI) into a case."""
    try:
        return _service(SourceImportService).import_source(
            case_id, url=body.url, doi=body.doi
        )
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
