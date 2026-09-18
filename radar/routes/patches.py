"""Manuscript patch generation, review (approve/reject), diff export, and git sync API routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, File, HTTPException

from radar.api_schemas import CheckItem, GitSyncRequest, RewriteView
from radar.db import session_scope
from radar.models import ClaimRevision, ImpactCandidate, PatchProposal, ResearchCase
from radar.services.patch_service import PatchService
from radar.routes.deps import _service

router = APIRouter(tags=["patches"])


def _patch_to_rewrite(
    patch: PatchProposal, session_factory: Any = None
) -> dict[str, Any]:
    claim_id = ""
    with session_scope(session_factory) as session:
        impact = session.get(ImpactCandidate, patch.impact_candidate_id)
        if impact:
            rev = session.get(ClaimRevision, impact.claim_revision_id)
            if rev:
                claim_id = rev.claim_id

    return RewriteView(
        patchId=patch.id,
        claimId=claim_id,
        before=patch.before_text,
        after=patch.after_text,
        loc=patch.target_locator,
        checks=[
            CheckItem(label=k, ok=bool(v))
            for k, v in (patch.validations_json or {}).items()
        ],
        patchType=patch.edit_class or "SCOPE_ADJUSTMENT",
        targetLocation=patch.target_locator,
        originalSentence=patch.before_text,
        patchedSentence=patch.after_text,
    ).model_dump()


@router.post("/api/cases/{case_id}/patches")
def generate_patch_for_impact(impact_id: str = File(...)) -> dict[str, Any]:
    """Generate a manuscript rewrite patch for a confirmed impact."""
    try:
        patch = _service(PatchService).generate_patch(impact_id)
    except LookupError:
        raise HTTPException(404, f"impact not found: {impact_id}")
    except ValueError as exc:
        raise HTTPException(400, str(exc))

    claim_id = ""
    with session_scope() as session:
        impact = session.get(ImpactCandidate, patch.impact_candidate_id)
        if impact:
            rev = session.get(ClaimRevision, impact.claim_revision_id)
            if rev:
                claim_id = rev.claim_id

    return {
        "patchId": patch.id,
        "claimId": claim_id,
        "loc": patch.target_locator,
        "before": patch.before_text,
        "after": patch.after_text,
        "patchType": patch.edit_class or "SCOPE_ADJUSTMENT",
        "targetLocation": patch.target_locator,
        "originalSentence": patch.before_text,
        "patchedSentence": patch.after_text,
        "checks": [
            {"label": k, "ok": v}
            for k, v in (patch.validations_json or {}).items()
        ],
    }


@router.post("/api/cases/{case_id}/patches/{patch_id}/approve")
def approve_patch(case_id: str, patch_id: str) -> dict[str, Any]:
    """Approve a generated patch."""
    try:
        patch = _service(PatchService).approve_patch(patch_id)
    except LookupError:
        raise HTTPException(404, f"patch not found: {patch_id}")
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return _patch_to_rewrite(patch)


@router.post("/api/cases/{case_id}/patches/{patch_id}/reject")
def reject_patch(case_id: str, patch_id: str) -> dict[str, Any]:
    """Reject a generated patch."""
    try:
        patch = _service(PatchService).reject_patch(patch_id)
    except LookupError:
        raise HTTPException(404, f"patch not found: {patch_id}")
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return _patch_to_rewrite(patch)


@router.post("/api/cases/{case_id}/patches/multi-location")
def generate_multi_location_patch(
    case_id: str, impact_id: str = File(...)
) -> dict[str, Any]:
    """Generate multi-location edits for a confirmed impact."""
    try:
        result = _service(PatchService).generate_multi_location_patch(impact_id)
    except LookupError:
        raise HTTPException(404, f"impact not found: {impact_id}")
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return result


@router.post("/api/cases/{case_id}/patches/{patch_id}/export-diff")
def export_patch_diff(case_id: str, patch_id: str) -> dict[str, str]:
    """Export a patch as unified diff format."""
    with session_scope() as session:
        patch = session.get(PatchProposal, patch_id)
        if patch is None:
            raise HTTPException(404, f"patch not found: {patch_id}")
        edit = {
            "section": patch.target_locator or "manuscript",
            "edit_class": patch.edit_class,
            "before_text": patch.before_text,
            "after_text": patch.after_text,
            "reason": "",
        }
        diff = PatchService.export_unified_diff([edit])
        return {"patch_id": patch_id, "diff": diff}


@router.get("/api/cases/{case_id}/patches/git-export")
def export_case_git_patch(case_id: str) -> dict[str, Any]:
    try:
        return _service(PatchService).export_case_git_patch(case_id)
    except LookupError:
        raise HTTPException(404, f"case not found: {case_id}")


@router.get("/api/cases/{case_id}/patches/unified-diff")
def export_case_unified_diff(case_id: str) -> dict[str, Any]:
    """Export standard POSIX unified diff covering manuscript .tex and references .bib."""
    try:
        return _service(PatchService).export_case_unified_diff(case_id)
    except LookupError:
        raise HTTPException(404, f"case not found: {case_id}")


@router.post("/api/cases/{case_id}/patches/apply-local")
def apply_case_patches_to_local(case_id: str) -> dict[str, Any]:
    """Apply approved patches directly to the local manuscript file and .bib."""
    try:
        return _service(PatchService).apply_case_patches_to_local(case_id)
    except LookupError:
        raise HTTPException(404, f"case not found: {case_id}")


@router.get("/api/cases/{case_id}/git-sync")
def get_git_sync(case_id: str) -> dict[str, Any]:
    with session_scope() as session:
        case = session.get(ResearchCase, case_id)
        if case is None:
            raise HTTPException(404, f"case not found: {case_id}")
        return (case.settings_json or {}).get("git_sync") or {"remote_url": "", "branch": "main", "enabled": False}


@router.put("/api/cases/{case_id}/git-sync")
def put_git_sync(case_id: str, body: GitSyncRequest) -> dict[str, Any]:
    with session_scope() as session:
        case = session.get(ResearchCase, case_id)
        if case is None:
            raise HTTPException(404, f"case not found: {case_id}")
        settings = dict(case.settings_json or {})
        settings["git_sync"] = body.model_dump()
        case.settings_json = settings
        return settings["git_sync"]
