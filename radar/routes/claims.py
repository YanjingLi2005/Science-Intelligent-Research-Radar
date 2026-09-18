"""Scientific claim extraction, verification, graph evolution, and review routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Form, HTTPException
from sqlalchemy import select

from radar.api_schemas import ClaimOut, EditClaimRequest, SplitClaimRequest
from radar.db import session_scope
from radar.models import Claim, ClaimRevision, ClaimSourceLink, ManuscriptVersion, Source
from radar.services.claim_service import ClaimService
from radar.routes.builders import _build_claims, _revision_to_claim_out
from radar.routes.deps import _parse_json_field, _service

router = APIRouter(tags=["claims"])


@router.get("/api/cases/{case_id}/claim-graph")
def claim_graph(case_id: str) -> dict[str, Any]:
    """Return the bounded claim/source evolution graph for a case."""
    max_nodes = 200
    with session_scope() as session:
        claims = list(session.scalars(select(Claim).where(Claim.case_id == case_id)))
        claim_by_id = {claim.id: claim for claim in claims}
        revisions = list(
            session.scalars(
                select(ClaimRevision)
                .join(Claim, Claim.id == ClaimRevision.claim_id)
                .where(Claim.case_id == case_id)
                .order_by(ClaimRevision.created_at.asc(), ClaimRevision.revision_no.asc())
            )
        )

        latest_by_claim: dict[str, ClaimRevision] = {}
        for revision in revisions:
            latest_by_claim[revision.claim_id] = revision

        nodes: list[dict[str, Any]] = []
        included_revision_ids: set[str] = set()
        for revision in revisions:
            if len(nodes) >= max_nodes:
                break
            claim = claim_by_id[revision.claim_id]
            included_revision_ids.add(revision.id)
            nodes.append(
                {
                    "id": f"claim:{revision.id}",
                    "type": "claim",
                    "label": revision.statement,
                    "data": {
                        "stable_key": claim.stable_key,
                        "statement": revision.statement,
                        "centrality": revision.centrality,
                        "review_state": revision.review_state,
                        "claim_id": claim.id,
                        "revision_id": revision.id,
                        "revision_no": revision.revision_no,
                        "lifecycle_state": claim.lifecycle_state,
                        "claim_role": claim.claim_role,
                    },
                }
            )

        edges: list[dict[str, Any]] = []
        edge_ids: set[str] = set()
        source_nodes: dict[str, dict[str, Any]] = {}
        truncated = len(revisions) > len(included_revision_ids)

        def add_edge(edge_id: str, source: str, target: str, relation: str, label: str) -> None:
            known_node_ids = {node["id"] for node in nodes} | set(source_nodes)
            if edge_id in edge_ids or source not in known_node_ids or target not in known_node_ids:
                return
            edge_ids.add(edge_id)
            edges.append({"id": edge_id, "source": source, "target": target, "label": label, "data": {"relation": relation}})

        revision_ids = set(included_revision_ids)
        for revision in revisions:
            if revision.id not in revision_ids:
                continue
            current_id = f"claim:{revision.id}"
            if revision.supersedes_id:
                add_edge(
                    f"supersedes:{revision.id}:{revision.supersedes_id}",
                    current_id,
                    f"claim:{revision.supersedes_id}",
                    "supersedes",
                    "supersedes",
                )
            claim = claim_by_id[revision.claim_id]
            if claim.parent_claim_id:
                parent_revision = latest_by_claim.get(claim.parent_claim_id)
                if parent_revision:
                    add_edge(
                        f"parent:{revision.id}:{parent_revision.id}",
                        current_id,
                        f"claim:{parent_revision.id}",
                        "parent",
                        "parent",
                    )

        source_rows = list(
            session.execute(
                select(ClaimSourceLink, Source)
                .join(Source, Source.id == ClaimSourceLink.source_id)
                .join(ClaimRevision, ClaimRevision.id == ClaimSourceLink.claim_revision_id)
                .join(Claim, Claim.id == ClaimRevision.claim_id)
                .where(Claim.case_id == case_id, ClaimSourceLink.claim_revision_id.in_(revision_ids or {"__none__"}))
            )
        )
        for link, source in source_rows:
            source_id = f"source:{source.id}"
            if source_id not in source_nodes:
                if len(nodes) + len(source_nodes) >= max_nodes:
                    truncated = True
                    continue
                source_nodes[source_id] = {
                    "id": source_id,
                    "type": "source",
                    "label": source.title or source.external_id,
                    "data": {
                        "title": source.title,
                        "integrity_state": source.integrity_state,
                        "external_id": source.external_id,
                        "source_id": source.id,
                    },
                }
            add_edge(
                f"claim-source:{link.claim_revision_id}:{source.id}",
                f"claim:{link.claim_revision_id}",
                source_id,
                link.relation_type,
                link.relation_type,
            )

        nodes.extend(source_nodes.values())
        return {
            "nodes": nodes,
            "edges": edges,
            "computed": {
                "node_count": len(nodes),
                "edge_count": len(edges),
                "claim_count": sum(node["type"] == "claim" for node in nodes),
                "source_count": sum(node["type"] == "source" for node in nodes),
                "truncated": truncated,
            },
        }


@router.get("/api/cases/{case_id}/claims", response_model=list[ClaimOut])
def list_claims(case_id: str) -> list[dict[str, Any]]:
    """Return all claims (confirmed + candidate) for a case, newest revision per claim."""
    claims_out = _build_claims(case_id)
    return [claim.model_dump() for claim in claims_out]


@router.post("/api/cases/{case_id}/claims/{rev_id}/confirm")
def confirm_claim(case_id: str, rev_id: str) -> dict[str, Any]:
    """Confirm a claim candidate (human gate G0)."""
    try:
        revision = _service(ClaimService).confirm_candidate(rev_id)
    except LookupError:
        raise HTTPException(404, f"claim revision not found: {rev_id}")
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return _revision_to_claim_out(revision).model_dump()


@router.post("/api/cases/{case_id}/claims/{rev_id}/reject")
def reject_claim(case_id: str, rev_id: str) -> dict[str, Any]:
    """Reject a claim candidate."""
    try:
        revision = _service(ClaimService).reject_candidate(rev_id)
    except LookupError:
        raise HTTPException(404, f"claim revision not found: {rev_id}")
    return _revision_to_claim_out(revision).model_dump()


@router.put("/api/cases/{case_id}/claims/{rev_id}")
def edit_claim(case_id: str, rev_id: str, body: EditClaimRequest) -> dict[str, Any]:
    """Edit a claim (creates a new revision, supersedes the old one)."""
    try:
        revision = _service(ClaimService).edit_candidate(
            rev_id,
            statement=body.statement,
            centrality=body.centrality,
            contract=body.contract.model_dump(),
            falsifiable_condition=body.falsifiable_condition,
        )
    except LookupError:
        raise HTTPException(404, f"claim revision not found: {rev_id}")
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return _revision_to_claim_out(revision).model_dump()


@router.post("/api/cases/{case_id}/claims/{rev_id}/split")
def split_claim(case_id: str, rev_id: str, body: SplitClaimRequest) -> list[dict[str, Any]]:
    """Split one claim into multiple child claims."""
    try:
        revisions = _service(ClaimService).split_candidate(rev_id, body.statements)
    except LookupError:
        raise HTTPException(404, f"claim revision not found: {rev_id}")
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return [_revision_to_claim_out(rev).model_dump() for rev in revisions]


@router.post("/api/cases/{case_id}/claims/{rev_id}/decompose")
def decompose_claim(case_id: str, rev_id: str) -> dict[str, Any]:
    """Decompose a claim into atomic sub-claims."""
    try:
        atoms = _service(ClaimService).decompose_to_atomic(rev_id)
    except LookupError:
        raise HTTPException(404, f"claim revision not found: {rev_id}")
    return {"claim_revision_id": rev_id, "atomic_claims": atoms}


@router.post("/api/cases/{case_id}/claims/{rev_id}/verify-semantics")
def verify_claim_semantics(case_id: str, rev_id: str) -> dict[str, Any]:
    """Verify a claim's statement semantically aligns with its source quote."""
    try:
        result = _service(ClaimService).verify_claim_semantics(rev_id)
    except LookupError:
        raise HTTPException(404, f"claim revision not found: {rev_id}")
    return result


@router.post("/api/cases/{case_id}/claims/extract-pipeline")
def extract_claims_pipeline(case_id: str, manuscript_version_id: str = Form(...)) -> dict[str, Any]:
    """Extract claims using the multi-stage section-aware pipeline (Solutions 1-3)."""
    try:
        result = _service(ClaimService).extract_with_pipeline(manuscript_version_id)
    except LookupError as e:
        raise HTTPException(404, str(e))
    return result


@router.post("/api/cases/{case_id}/claims/re-extract")
def re_extract_claims(case_id: str) -> dict[str, Any]:
    """Re-run claim extraction on the current manuscript version."""
    with session_scope() as session:
        manuscript = session.scalar(
            select(ManuscriptVersion).where(
                ManuscriptVersion.case_id == case_id,
                ManuscriptVersion.is_current.is_(True),
            )
        )
        if manuscript is None:
            raise HTTPException(404, f"manuscript not found for case: {case_id}")
        manuscript_version_id = manuscript.id
    try:
        revisions = _service(ClaimService).extract_candidates(manuscript_version_id)
    except LookupError as e:
        raise HTTPException(404, str(e))
    confirmed = sum(1 for rev in revisions if rev.review_state in ("confirmed", "edited"))
    return {
        "manuscript_version_id": manuscript_version_id,
        "claims": len(revisions),
        "confirmed": confirmed,
    }


@router.post("/api/cases/{case_id}/claims/{rev_id}/confirm-feedback")
def confirm_claim_with_feedback(
    case_id: str, rev_id: str,
    human_note: str = Form(""),
    edit_fields: str = Form("[]"),
    before_values: str = Form("{}"),
    after_values: str = Form("{}"),
) -> dict[str, Any]:
    """Confirm claim with structured G0 feedback (Solution 4)."""
    try:
        revision = _service(ClaimService).confirm_with_feedback(
            rev_id, human_note=human_note,
            edit_fields=_parse_json_field(edit_fields, []),
            before_values=_parse_json_field(before_values, {}),
            after_values=_parse_json_field(after_values, {}),
        )
    except LookupError:
        raise HTTPException(404, f"claim revision not found: {rev_id}")
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return _revision_to_claim_out(revision).model_dump()


@router.post("/api/cases/{case_id}/claims/{rev_id}/reject-feedback")
def reject_claim_with_feedback(
    case_id: str, rev_id: str,
    reject_reason: str = Form(...),
    human_note: str = Form(""),
) -> dict[str, Any]:
    """Reject claim with structured reason (Solution 4)."""
    try:
        revision = _service(ClaimService).reject_with_feedback(
            rev_id, reject_reason=reject_reason, human_note=human_note,
        )
    except LookupError:
        raise HTTPException(404, f"claim revision not found: {rev_id}")
    return _revision_to_claim_out(revision).model_dump()


@router.post("/api/cases/{case_id}/claims/track-changes")
def track_claim_changes(
    case_id: str,
    previous_version_id: str = Form(...),
    current_version_id: str = Form(...),
) -> dict[str, Any]:
    """Track claim lifecycle changes between manuscript versions (Solution 5)."""
    try:
        result = _service(ClaimService).track_claim_changes(previous_version_id, current_version_id)
    except LookupError as e:
        raise HTTPException(404, str(e))
    return result


@router.post("/api/cases/{case_id}/claims/incremental-extract")
def incremental_claim_extract(
    case_id: str,
    previous_version_id: str = Form(...),
    current_version_id: str = Form(...),
) -> dict[str, Any]:
    """Extract claims only from changed sections between versions (增量解析)."""
    try:
        result = _service(ClaimService).incremental_claim_extract(previous_version_id, current_version_id)
    except LookupError as e:
        raise HTTPException(404, str(e))
    return result
