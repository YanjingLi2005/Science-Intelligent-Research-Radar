"""Citation health, reference validity, citation support verification, and integrity radar API routes."""

from __future__ import annotations

import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, File, HTTPException, Response, UploadFile
from pydantic import BaseModel
from sqlalchemy import func, select

from radar.api_schemas import CitationHealthOut
from radar.db import get_session_factory, session_scope
from radar.models import (
    Claim,
    ClaimRevision,
    ClaimSourceLink,
    ImpactCandidate,
    ScanRun,
    Source,
    SourceSnapshot,
)
from radar.parsers import parser_for
from radar.services.case_service import CaseService
from radar.services.citation_service import CitationService
from radar.services.citation_support_service import (
    CitationSupportInput,
    CitationSupportService,
    ReferenceMatch,
)
from radar.services.reference_parser import parse_references
from radar.services.reference_validity_service import (
    ReferenceEntry,
    ReferenceValidityService,
)
from radar.routes.builders import _build_citation_health
from radar.routes.deps import _service

router = APIRouter(tags=["citations"])


class ReferenceCheckRequest(BaseModel):
    format: Literal["bibtex", "text", "auto"] = "auto"
    references: str = ""
    entries: list[ReferenceEntry] | None = None


class CitationSupportRequest(BaseModel):
    inputs: list[CitationSupportInput] | None = None
    reference: ReferenceMatch | None = None
    citation_sentence: str = ""
    full_text: str | None = None
    abstract: str | None = None


def _get_reference_validity_service():
    try:
        import radar.api as api
        return getattr(api, "ReferenceValidityService", ReferenceValidityService)
    except Exception:
        return ReferenceValidityService


def _get_citation_support_service():
    try:
        import radar.api as api
        return getattr(api, "CitationSupportService", CitationSupportService)
    except Exception:
        return CitationSupportService


@router.get("/api/cases/{case_id}/citation-health", response_model=CitationHealthOut)
def get_citation_health(case_id: str) -> dict[str, Any]:
    """Run deterministic citation/bibliography checks on the current manuscript."""
    case = _service(CaseService).get_case(case_id)
    if case is None:
        raise HTTPException(404, f"case not found: {case_id}")
    return _build_citation_health(case_id)


@router.post("/api/cases/{case_id}/references/check")
def check_references(
    case_id: str, body: ReferenceCheckRequest
) -> dict[str, Any]:
    """Check supplied bibliography entries against public bibliographic sources."""
    case = _service(CaseService).get_case(case_id)
    if case is None:
        raise HTTPException(404, f"case not found: {case_id}")
    entries = (
        body.entries
        if body.entries is not None
        else parse_references(body.references, body.format)
    )
    svc_cls = _get_reference_validity_service()
    return svc_cls(get_session_factory()).check_batch(entries).model_dump()


@router.post("/api/cases/{case_id}/references/check-file")
async def check_references_file(
    case_id: str, file: UploadFile = File(...)
) -> dict[str, Any]:
    """Check references parsed from an uploaded PDF/TeX/Markdown/BibTeX file."""
    case = _service(CaseService).get_case(case_id)
    if case is None:
        raise HTTPException(404, f"case not found: {case_id}")
    suffix = Path(file.filename or "").suffix.lower()
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(await file.read())
        tmp_path = Path(tmp.name)
    try:
        if suffix == ".bib":
            text = tmp_path.read_text(encoding="utf-8", errors="replace")
        else:
            parsed = parser_for(tmp_path).parse(tmp_path)
            text = parsed.full_text or ""
        entries = parse_references(text, "auto")
    finally:
        tmp_path.unlink(missing_ok=True)
    svc_cls = _get_reference_validity_service()
    return svc_cls(get_session_factory()).check_batch(entries).model_dump()


@router.post("/api/cases/{case_id}/citations/support-check")
def check_citation_support(
    case_id: str, body: CitationSupportRequest
) -> dict[str, Any]:
    """Check whether one or more cited sentences are supported by their papers."""
    case = _service(CaseService).get_case(case_id)
    if case is None:
        raise HTTPException(404, f"case not found: {case_id}")
    if body.inputs is not None:
        inputs = body.inputs
    elif body.reference is not None:
        inputs = [
            CitationSupportInput(
                reference=body.reference,
                citation_sentence=body.citation_sentence,
                full_text=body.full_text,
                abstract=body.abstract,
            )
        ]
    else:
        raise HTTPException(422, "request must include inputs or reference")
    svc_cls = _get_citation_support_service()
    return svc_cls(get_session_factory()).check_batch(inputs).model_dump()


@router.get("/api/cases/{case_id}/integrity-radar")
def get_integrity_radar(case_id: str) -> dict[str, Any]:
    """Return the read-only integrity view for sources used by a case."""
    case = _service(CaseService).get_case(case_id)
    if case is None:
        raise HTTPException(404, f"case not found: {case_id}")

    with session_scope() as session:
        claim_rows = session.execute(
            select(ClaimSourceLink.source_id, Claim.id)
            .join(ClaimRevision, ClaimRevision.id == ClaimSourceLink.claim_revision_id)
            .join(Claim, Claim.id == ClaimRevision.claim_id)
            .where(Claim.case_id == case_id)
            .distinct()
        ).all()
        impact_rows = session.execute(
            select(
                SourceSnapshot.source_id,
                ImpactCandidate.id,
            )
            .join(ImpactCandidate, ImpactCandidate.source_snapshot_id == SourceSnapshot.id)
            .join(ScanRun, ScanRun.id == ImpactCandidate.scan_run_id)
            .where(
                ScanRun.case_id == case_id,
                ImpactCandidate.event_type == "retraction",
                ImpactCandidate.impact_mode == "research_integrity",
            )
        ).all()

        source_ids = {row[0] for row in claim_rows} | {row[0] for row in impact_rows}
        sources = list(
            session.scalars(
                select(Source)
                .where(Source.id.in_(source_ids))
                .order_by(Source.created_at.desc(), Source.id)
            )
        ) if source_ids else []
        source_by_id = {source.id: source for source in sources}

        claim_ids_by_source: dict[str, set[str]] = defaultdict(set)
        for source_id, claim_id in claim_rows:
            claim_ids_by_source[source_id].add(claim_id)

        impact_ids_by_source: dict[str, list[str]] = defaultdict(list)
        for source_id, impact_id in impact_rows:
            impact_ids_by_source[source_id].append(impact_id)

        snapshot_counts = dict(
            session.execute(
                select(SourceSnapshot.source_id, func.count(SourceSnapshot.id))
                .where(SourceSnapshot.source_id.in_(source_ids))
                .group_by(SourceSnapshot.source_id)
            ).all()
        ) if source_ids else {}

        counts = {"retracted": 0, "expression_of_concern": 0, "corrected": 0, "normal": 0}
        flagged_sources: list[dict[str, Any]] = []
        for source in sources:
            state = source.integrity_state or "normal"
            if state not in counts:
                state = "normal"
            counts[state] += 1
            if state == "normal":
                continue
            flags = [state]
            if impact_ids_by_source.get(source.id):
                flags.append("retraction_impact")
            flagged_sources.append({
                "source_id": source.id,
                "title": source.title or "",
                "doi": source.doi,
                "url": source.url or "",
                "integrity_state": state,
                "flags": flags,
                "related_claim_ids": sorted(claim_ids_by_source.get(source.id, set())),
                "related_impact_ids": impact_ids_by_source.get(source.id, []),
                "snapshot_count": int(snapshot_counts.get(source.id, 0)),
            })

        retraction_impacts: list[dict[str, Any]] = []
        for source_id, impact_id in impact_rows:
            impact = session.get(ImpactCandidate, impact_id)
            source = source_by_id.get(source_id)
            if not impact or not source:
                continue
            retraction_impacts.append({
                "id": impact.id,
                "title": source.title or "",
                "state": impact.review_state or "candidate",
                "source_title": source.title or "",
            })

    return {
        "flagged_sources": flagged_sources,
        "counts": counts,
        "citation_health": _build_citation_health(case_id),
        "retraction_impacts": retraction_impacts,
    }


@router.get("/api/cases/{case_id}/compliance-report")
def get_compliance_report(
    case_id: str, format: Literal["markdown", "html"] = "markdown"
) -> dict[str, Any]:
    """Generate and return a structured Research Compliance & Integrity Audit Report."""
    try:
        return _service(CitationService).generate_compliance_report(case_id, format=format)
    except LookupError:
        raise HTTPException(404, f"case not found: {case_id}")
    except Exception as exc:
        raise HTTPException(500, f"failed to generate compliance report: {exc}")


@router.get("/api/cases/{case_id}/compliance-report/download")
def download_compliance_report(
    case_id: str, format: Literal["markdown", "html"] = "markdown"
) -> Response:
    """Download the Research Compliance & Integrity Audit Report file directly."""
    try:
        report_data = _service(CitationService).generate_compliance_report(case_id, format=format)
    except LookupError:
        raise HTTPException(404, f"case not found: {case_id}")
    except Exception as exc:
        raise HTTPException(500, f"failed to generate compliance report: {exc}")

    content = (
        report_data["report_html"]
        if format == "html"
        else report_data["report_markdown"]
    )
    media_type = "text/html; charset=utf-8" if format == "html" else "text/markdown; charset=utf-8"
    filename = report_data["filename"]

    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
