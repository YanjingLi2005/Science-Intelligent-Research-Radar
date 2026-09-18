"""Research Case management, upload, profile analysis, and watchlist routes."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import PlainTextResponse
from sqlalchemy import select

from radar.api_schemas import (
    CompetitorEntry,
    CompetitorRequest,
    ProjectOut,
    ProjectProfile,
    ProjectSummary,
)
from radar.db import get_session_factory, session_scope
from radar.models import (
    Claim,
    ClaimRevision,
    ImpactCandidate,
    ManuscriptVersion,
    ResearchCase,
    WatchEntity,
)
from radar.services.case_service import CaseService, ScanActiveError
from radar.services.consistency_service import check_manuscript
from radar.services.manuscript_understanding_service import ManuscriptUnderstandingService
from radar.routes.builders import _build_project, _build_summary
from radar.routes.deps import _service

router = APIRouter(tags=["cases"])


@router.get("/api/cases", response_model=list[ProjectSummary])
def list_cases() -> list[dict[str, Any]]:
    """Return a lightweight list of all research cases."""
    cases = _service(CaseService).list_cases()
    result: list[dict[str, Any]] = []
    for case in cases:
        summary = _build_summary(case)
        result.append(summary)
    return result


@router.get("/api/lab/claim-matrix")
def claim_matrix() -> dict[str, Any]:
    """Return the current user's confirmed/edited claims across all cases."""
    with session_scope() as session:
        cases = list(
            session.scalars(
                select(ResearchCase).order_by(
                    ResearchCase.updated_at.desc(), ResearchCase.created_at.desc()
                )
            )
        )
        case_ids = [case.id for case in cases]
        if not case_ids:
            return {
                "cases": [],
                "rows": [],
                "summary": {
                    "total_cases": 0,
                    "total_claims": 0,
                    "critical_claims": 0,
                    "review_claims": 0,
                    "clean_claims": 0,
                },
            }

        claims = list(session.scalars(select(Claim).where(Claim.case_id.in_(case_ids))))
        claim_by_id = {claim.id: claim for claim in claims}
        revisions = list(
            session.scalars(
                select(ClaimRevision)
                .where(
                    ClaimRevision.claim_id.in_(claim_by_id),
                    ClaimRevision.review_state.in_(["confirmed", "edited"]),
                )
                .order_by(ClaimRevision.revision_no.desc(), ClaimRevision.created_at.desc())
            )
        )

        latest_by_claim: dict[str, ClaimRevision] = {}
        for revision in revisions:
            latest_by_claim.setdefault(revision.claim_id, revision)

        # Collect impacts for risk urgency evaluation
        impacts = list(
            session.scalars(
                select(ImpactCandidate)
                .where(ImpactCandidate.claim_revision_id.in_([r.id for r in revisions]))
            )
        ) if revisions else []
        impacts_by_rev: dict[str, list[ImpactCandidate]] = {}
        for imp in impacts:
            impacts_by_rev.setdefault(imp.claim_revision_id, []).append(imp)

        def group_key(case: ResearchCase) -> str | None:
            settings = case.settings_json or {}
            value = settings.get("group") if isinstance(settings, dict) else None
            return value if isinstance(value, str) and value.strip() else None

        case_out = []
        for case in cases:
            short = case.title.split(":", 1)[0].strip() if ":" in case.title else case.title[:30]
            case_out.append({
                "id": case.id,
                "title": case.title,
                "short": short,
                "question": case.research_question,
                "group_key": group_key(case),
            })

        rows: list[dict[str, Any]] = []
        for case in cases:
            case_group = group_key(case)
            for claim in claims:
                if claim.case_id != case.id:
                    continue
                revision = latest_by_claim.get(claim.id)
                if revision is None:
                    continue
                raw = revision.contract_json or {}
                claim_impacts = impacts_by_rev.get(revision.id, [])
                has_critical = any(
                    imp.severity == "critical"
                    or (imp.stance == "challenges" and imp.comparability == "compatible")
                    for imp in claim_impacts
                )
                has_review = any(
                    imp.severity == "review"
                    or imp.stance == "challenges"
                    or imp.impact_mode == "boundary_condition"
                    for imp in claim_impacts
                )
                has_support = any(imp.stance == "supports" for imp in claim_impacts)

                urgency = (
                    "critical" if has_critical
                    else "review" if has_review
                    else "supported" if has_support
                    else "normal"
                )

                rows.append({
                    "case_id": case.id,
                    "case_title": case.title,
                    "group_key": case_group,
                    "claim_id": claim.id,
                    "stable_key": claim.stable_key,
                    "statement": revision.statement,
                    "contract": {
                        "task": raw.get("task") or "",
                        "dataset": raw.get("dataset") or "",
                        "split": raw.get("split") or "",
                        "metric": raw.get("metric") or "",
                        "comparator": raw.get("comparator") or raw.get("baseline") or "",
                        "scope": raw.get("scope") or "",
                    },
                    "review_state": revision.review_state,
                    "urgency": urgency,
                    "impact_count": len(claim_impacts),
                    "challenge_count": sum(imp.stance == "challenges" for imp in claim_impacts),
                })

        summary = {
            "total_cases": len(cases),
            "total_claims": len(rows),
            "critical_claims": sum(r["urgency"] == "critical" for r in rows),
            "review_claims": sum(r["urgency"] == "review" for r in rows),
            "clean_claims": sum(r["urgency"] in {"normal", "supported"} for r in rows),
        }

        return {"cases": case_out, "rows": rows, "summary": summary}


@router.post("/api/cases", response_model=ProjectSummary)
async def create_case(
    title: str = File(...),
    research_question: str = File(default=""),
    manuscript: UploadFile = File(...),
) -> dict[str, Any]:
    """Create a new research case by uploading a manuscript PDF/TeX/MD file."""
    suffix = Path(manuscript.filename or "upload.pdf").suffix or ".pdf"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(await manuscript.read())
        tmp_path = Path(tmp.name)

    try:
        case_id = _service(CaseService).create_case(
            title=title,
            research_question=research_question,
            manuscript_path=tmp_path,
        )
    finally:
        tmp_path.unlink(missing_ok=True)

    case = _service(CaseService).get_case(case_id)
    if case is None:
        raise HTTPException(500, "case created but not found")
    return _build_summary(case)


@router.delete("/api/cases/{case_id}")
def delete_case(case_id: str) -> dict[str, Any]:
    """Permanently delete a research case and all its associated data."""
    svc = _service(CaseService)
    case = svc.get_case(case_id)
    if case is None:
        raise HTTPException(404, f"case not found: {case_id}")
    try:
        svc.delete_case(case_id)
    except ScanActiveError:
        raise HTTPException(409, "请先取消正在运行的扫描，再删除项目")
    return {"deleted": case_id, "title": case.title}


@router.get("/api/cases/{case_id}", response_model=ProjectOut)
def get_case(case_id: str) -> dict[str, Any]:
    """Return the full project view for one research case."""
    case = _service(CaseService).get_case(case_id)
    if case is None:
        raise HTTPException(404, f"case not found: {case_id}")
    return _build_project(case_id, case)


@router.get("/api/cases/{case_id}/writing-brief", response_class=PlainTextResponse)
def get_writing_brief(case_id: str) -> str:
    """Return the markdown writing brief for a case."""
    case = _service(CaseService).get_case(case_id)
    if case is None:
        raise HTTPException(404, f"case not found: {case_id}")
    from radar.services.report_service import ReportService

    return ReportService(get_session_factory()).export_writing_brief(case_id)


@router.post("/api/cases/{case_id}/upload")
async def upload_new_version(case_id: str, manuscript: UploadFile = File(...)) -> dict[str, Any]:
    """Upload a new version of the manuscript for an existing case."""
    case = _service(CaseService).get_case(case_id)
    if case is None:
        raise HTTPException(404, f"case not found: {case_id}")

    suffix = Path(manuscript.filename or "upload.pdf").suffix or ".pdf"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(await manuscript.read())
        tmp_path = Path(tmp.name)

    try:
        result = _service(CaseService).add_manuscript_version(case_id, tmp_path)
    finally:
        tmp_path.unlink(missing_ok=True)
    return result


@router.get("/api/cases/{case_id}/profile")
def get_profile(case_id: str) -> dict[str, Any]:
    """Return the latest manuscript-understanding profile for a case."""
    profile = ManuscriptUnderstandingService.latest_profile(case_id, get_session_factory())
    if profile is None:
        return ProjectProfile().model_dump()
    return ProjectProfile(
        question=profile.research_problem,
        thesis=profile.central_thesis,
        contributions=profile.contributions,
        findings=profile.key_findings,
        limits=profile.limitations,
    ).model_dump()


@router.post("/api/cases/{case_id}/profile/analyze")
def analyze_profile(case_id: str) -> dict[str, Any]:
    """Run a full manuscript-understanding analysis (may be slow)."""
    try:
        _, profile = _service(ManuscriptUnderstandingService).analyze(case_id)
    except LookupError:
        raise HTTPException(404, f"case not found: {case_id}")
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return ProjectProfile(
        question=profile.research_problem,
        thesis=profile.central_thesis,
        contributions=profile.contributions,
        findings=profile.key_findings,
        limits=profile.limitations,
    ).model_dump()


@router.get("/api/cases/{case_id}/competitors", response_model=list[CompetitorEntry])
def list_competitors(case_id: str) -> list[dict[str, Any]]:
    """Return the competitor watchlist for a case."""
    with session_scope() as session:
        entities = list(
            session.scalars(
                select(WatchEntity).where(WatchEntity.case_id == case_id)
            )
        )
        return [
            CompetitorEntry(
                id=entity.id, team=entity.canonical_name, aliases=entity.aliases_json
            )
            for entity in entities
        ]


@router.post("/api/cases/{case_id}/competitors")
def add_competitor(case_id: str, body: CompetitorRequest) -> dict[str, Any]:
    """Add a competitor/team to the watchlist."""
    try:
        watch_id = _service(CaseService).add_watch_entity(
            case_id,
            entity_type="competitor",
            canonical_name=body.team,
            aliases=body.aliases,
        )
    except LookupError:
        raise HTTPException(404, f"case not found: {case_id}")
    return {"id": watch_id, "team": body.team, "aliases": body.aliases}


@router.delete("/api/cases/{case_id}/competitors/{watch_id}")
def remove_competitor(case_id: str, watch_id: str) -> dict[str, Any]:
    """Remove a competitor from the watchlist."""
    try:
        _service(CaseService).remove_watch_entity(watch_id)
    except LookupError:
        raise HTTPException(404, f"watch entity not found: {watch_id}")
    return {"deleted": watch_id}


@router.get("/api/cases/{case_id}/consistency")
def get_consistency(case_id: str, semantic: bool = False) -> dict[str, Any]:
    """Internal-consistency check over the current manuscript.

    By default runs only the deterministic numeric check: it reads the stored
    manuscript text and nothing else — no LLM, no network — so it is safe to
    call on every page load. ``semantic=true`` additionally runs layer 2,
    which loads the NLI model and calls the LLM; it is slow and costs tokens,
    so the client asks for it explicitly rather than getting it by default.

    Findings from either layer are candidates for a human to confirm,
    consistent with the G0/G1/G2 gates; nothing here mutates the manuscript.
    """
    case = _service(CaseService).get_case(case_id)
    if case is None:
        raise HTTPException(404, f"case not found: {case_id}")
    with session_scope() as session:
        manuscript = session.scalar(
            select(ManuscriptVersion).where(
                ManuscriptVersion.case_id == case_id,
                ManuscriptVersion.is_current.is_(True),
            )
        )
        if manuscript is None:
            raise HTTPException(404, f"manuscript not found for case: {case_id}")
        content = manuscript.content_text or ""
        file_name = manuscript.file_name
        version_no = manuscript.version_no

    result = check_manuscript(content, semantic=semantic)
    result["manuscript"] = {
        "file_name": file_name,
        "version_no": version_no,
        "chars": len(content),
    }
    return result
