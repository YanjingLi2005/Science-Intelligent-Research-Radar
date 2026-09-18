"""SQLAlchemy models for the local Research Radar source of truth."""

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from radar.db import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ResearchCase(TimestampMixin, Base):
    __tablename__ = "research_cases"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    title: Mapped[str] = mapped_column(String, nullable=False)
    research_question: Mapped[str] = mapped_column(Text, nullable=False)
    field: Mapped[str] = mapped_column(String, default="cs_ai")
    settings_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class ManuscriptVersion(TimestampMixin, Base):
    __tablename__ = "manuscript_versions"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("research_cases.id"), index=True)
    version_no: Mapped[int] = mapped_column(Integer, default=1)
    file_name: Mapped[str] = mapped_column(String)
    source_type: Mapped[str] = mapped_column(String)
    content_text: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)


class Claim(TimestampMixin, Base):
    __tablename__ = "claims"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("research_cases.id"), index=True)
    stable_key: Mapped[str] = mapped_column(String, index=True)
    lifecycle_state: Mapped[str] = mapped_column(String, default="active")
    # Solution 5: Incremental tracking
    track_state: Mapped[str] = mapped_column(String, default="new", doc="new|modified|deleted|unchanged|superseded|needs_revalidation")
    # Solution: Claim Graph
    parent_claim_id: Mapped[str | None] = mapped_column(ForeignKey("claims.id"), nullable=True, doc="Parent claim for hierarchical claim graph")
    claim_role: Mapped[str] = mapped_column(String, default="sub_claim", doc="core_conclusion|sub_claim|experimental_support|boundary_condition|method_component|comparative")


class ClaimRevision(TimestampMixin, Base):
    __tablename__ = "claim_revisions"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    claim_id: Mapped[str] = mapped_column(ForeignKey("claims.id"), index=True)
    manuscript_version_id: Mapped[str] = mapped_column(ForeignKey("manuscript_versions.id"))
    revision_no: Mapped[int] = mapped_column(Integer, default=1)
    statement: Mapped[str] = mapped_column(Text)
    claim_type: Mapped[str] = mapped_column(String, default="empirical_result")
    centrality: Mapped[str] = mapped_column(String)
    contract_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    falsifiable_condition: Mapped[str] = mapped_column(Text)
    source_quote: Mapped[str] = mapped_column(Text)
    source_locator: Mapped[str] = mapped_column(String)
    review_state: Mapped[str] = mapped_column(String, default="candidate", index=True)
    supersedes_id: Mapped[str | None] = mapped_column(ForeignKey("claim_revisions.id"), nullable=True)
    # Solution: Author attribution
    author_attribution: Mapped[str] = mapped_column(String, default="uncertain")
    # Solution 1: Section anchoring
    section_id: Mapped[str | None] = mapped_column(String, nullable=True)
    # Solution: Qualifiers
    qualifiers_json: Mapped[list[str]] = mapped_column(JSON, default=list)


class ClaimSurface(TimestampMixin, Base):
    __tablename__ = "claim_surfaces"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    claim_revision_id: Mapped[str] = mapped_column(ForeignKey("claim_revisions.id"), index=True)
    manuscript_version_id: Mapped[str] = mapped_column(ForeignKey("manuscript_versions.id"))
    section: Mapped[str] = mapped_column(String)
    locator: Mapped[str] = mapped_column(String)
    quote: Mapped[str] = mapped_column(Text)
    surface_role: Mapped[str] = mapped_column(String)


class Source(TimestampMixin, Base):
    __tablename__ = "sources"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    external_id: Mapped[str] = mapped_column(String, unique=True, index=True)
    source_kind: Mapped[str] = mapped_column(String, default="unknown", index=True)
    title: Mapped[str] = mapped_column(Text)
    authors_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    url: Mapped[str] = mapped_column(String)
    doi: Mapped[str | None] = mapped_column(String, nullable=True)
    arxiv_id: Mapped[str | None] = mapped_column(String, nullable=True)
    license: Mapped[str | None] = mapped_column(String, nullable=True)
    venue: Mapped[str | None] = mapped_column(Text, nullable=True)
    publication_type: Mapped[str] = mapped_column(String, default="preprint")
    pdf_url: Mapped[str | None] = mapped_column(String, nullable=True)
    cited_by_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    integrity_state: Mapped[str] = mapped_column(String, default="normal")
    # CS/AI domain metadata
    arxiv_primary_category: Mapped[str | None] = mapped_column(String, nullable=True)
    fields_of_study_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    ccf_rank: Mapped[str | None] = mapped_column(String, nullable=True)


class SourceSnapshot(TimestampMixin, Base):
    __tablename__ = "source_snapshots"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id"), index=True)
    version_label: Mapped[str] = mapped_column(String)
    title: Mapped[str] = mapped_column(Text)
    abstract: Mapped[str] = mapped_column(Text)
    content_text: Mapped[str] = mapped_column(Text)
    # Immutable identity: hash of the abstract. Full-text enrichment writes
    # full_text_hash below so the snapshot id and content_hash never churn.
    content_hash: Mapped[str] = mapped_column(String)
    full_text_hash: Mapped[str | None] = mapped_column(String)
    event_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class RetrievalReceipt(TimestampMixin, Base):
    """Reproducibility receipt for one bounded external search request."""

    __tablename__ = "retrieval_receipts"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("research_cases.id"), index=True)
    scan_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("scan_runs.id"), nullable=True, index=True
    )
    source_kind: Mapped[str] = mapped_column(String, index=True)
    endpoint: Mapped[str] = mapped_column(Text)
    query_text: Mapped[str] = mapped_column(Text)
    filters_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    requested_count: Mapped[int] = mapped_column(Integer, default=0)
    retrieved_count: Mapped[int] = mapped_column(Integer, default=0)
    pages: Mapped[int] = mapped_column(Integer, default=1)
    response_hash: Mapped[str] = mapped_column(String, default="")
    status: Mapped[str] = mapped_column(String, default="success", index=True)
    warnings_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ClaimSourceLink(TimestampMixin, Base):
    __tablename__ = "claim_source_links"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    claim_revision_id: Mapped[str] = mapped_column(ForeignKey("claim_revisions.id"), index=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id"), index=True)
    relation_type: Mapped[str] = mapped_column(String)
    source_locator: Mapped[str] = mapped_column(String)
    review_state: Mapped[str] = mapped_column(String, default="confirmed")


class WatchEntity(TimestampMixin, Base):
    __tablename__ = "watch_entities"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("research_cases.id"), index=True)
    entity_type: Mapped[str] = mapped_column(String)
    canonical_name: Mapped[str] = mapped_column(String)
    aliases_json: Mapped[list[str]] = mapped_column(JSON, default=list)


class ScanRun(TimestampMixin, Base):
    __tablename__ = "scan_runs"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("research_cases.id"), index=True)
    mode: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="pending")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    query_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    stats_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Heartbeat for zombie-scan recovery: every progress/state write refreshes it.
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class ImpactCandidate(TimestampMixin, Base):
    __tablename__ = "impact_candidates"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    scan_run_id: Mapped[str] = mapped_column(ForeignKey("scan_runs.id"), index=True)
    claim_revision_id: Mapped[str] = mapped_column(ForeignKey("claim_revisions.id"), index=True)
    source_snapshot_id: Mapped[str] = mapped_column(ForeignKey("source_snapshots.id"), index=True)
    event_type: Mapped[str] = mapped_column(String)
    stance: Mapped[str] = mapped_column(String)
    impact_mode: Mapped[str] = mapped_column(String)
    strategic_flags_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    comparability: Mapped[str] = mapped_column(String)
    condition_differences_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    evidence_own_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    evidence_new_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    change_depth: Mapped[int] = mapped_column(Integer)
    severity: Mapped[str] = mapped_column(String)
    suggested_action: Mapped[str] = mapped_column(String)
    uncertainty_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    strategy_payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    review_state: Mapped[str] = mapped_column(String, default="candidate", index=True)
    trust_state: Mapped[str] = mapped_column(String, default="generated")


class ActionItem(TimestampMixin, Base):
    __tablename__ = "action_items"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("research_cases.id"), index=True)
    scan_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("scan_runs.id"), nullable=True, index=True
    )
    impact_candidate_id: Mapped[str | None] = mapped_column(
        ForeignKey("impact_candidates.id"), nullable=True, index=True
    )
    claim_revision_id: Mapped[str | None] = mapped_column(
        ForeignKey("claim_revisions.id"), nullable=True, index=True
    )
    action_type: Mapped[str] = mapped_column(String, index=True)
    priority: Mapped[str] = mapped_column(String, index=True)
    title: Mapped[str] = mapped_column(String)
    rationale: Mapped[str] = mapped_column(Text)
    checklist_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    due_label: Mapped[str] = mapped_column(String, default="this_week")
    status: Mapped[str] = mapped_column(String, default="proposed", index=True)
    advice_source: Mapped[str] = mapped_column(String, default="rule")


class ReviewDecision(TimestampMixin, Base):
    __tablename__ = "review_decisions"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    impact_candidate_id: Mapped[str] = mapped_column(ForeignKey("impact_candidates.id"), index=True)
    decision: Mapped[str] = mapped_column(String)
    edited_payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    actor: Mapped[str] = mapped_column(String, default="local_user")


# ---- Solution 4: G0 Structured Feedback ----

class G0Feedback(TimestampMixin, Base):
    __tablename__ = "g0_feedback"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    claim_revision_id: Mapped[str] = mapped_column(ForeignKey("claim_revisions.id"), index=True)
    decision: Mapped[str] = mapped_column(String, doc="confirmed|edited|rejected")
    reject_reason: Mapped[str | None] = mapped_column(String, nullable=True,
        doc="wrong_attribution|missing_qualifier|not_a_claim|duplicate|too_broad|unsupported|wrong_decomposition|wrong_type|wrong_centrality|other")
    edit_fields_json: Mapped[list[str]] = mapped_column(JSON, default=list, doc="Which fields were edited")
    before_values_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    after_values_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    human_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    actor: Mapped[str] = mapped_column(String, default="local_user")


class PatchProposal(TimestampMixin, Base):
    __tablename__ = "patch_proposals"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("research_cases.id"), index=True)
    manuscript_version_id: Mapped[str] = mapped_column(ForeignKey("manuscript_versions.id"))
    impact_candidate_id: Mapped[str] = mapped_column(ForeignKey("impact_candidates.id"), index=True)
    target_locator: Mapped[str] = mapped_column(String)
    edit_class: Mapped[str] = mapped_column(String)
    before_text: Mapped[str] = mapped_column(Text)
    after_text: Mapped[str] = mapped_column(Text)
    citations_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    evidence_refs_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    validations_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    approval_state: Mapped[str] = mapped_column(String, default="candidate")


class AuditEvent(TimestampMixin, Base):
    __tablename__ = "audit_events"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("research_cases.id"), index=True)
    event_type: Mapped[str] = mapped_column(String)
    object_type: Mapped[str] = mapped_column(String)
    object_id: Mapped[str] = mapped_column(String, index=True)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    actor_type: Mapped[str] = mapped_column(String)
    actor_id: Mapped[str] = mapped_column(String)


class ModelRun(TimestampMixin, Base):
    __tablename__ = "model_runs"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    stage: Mapped[str] = mapped_column(String)
    case_id: Mapped[str | None] = mapped_column(
        ForeignKey("research_cases.id"), nullable=True, index=True
    )
    scan_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("scan_runs.id"), nullable=True, index=True
    )
    provider: Mapped[str] = mapped_column(String)
    model: Mapped[str] = mapped_column(String)
    prompt_hash: Mapped[str] = mapped_column(String)
    schema_version: Mapped[str] = mapped_column(String)
    input_refs_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    raw_response: Mapped[str] = mapped_column(Text)
    parsed_output_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    validation_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    estimated_cost: Mapped[float] = mapped_column(Float, default=0.0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)


__all__ = [
    "ActionItem", "AuditEvent", "Base", "Claim", "ClaimRevision", "ClaimSourceLink",
    "ClaimSurface", "ImpactCandidate", "ManuscriptVersion", "ModelRun",
    "PatchProposal", "ResearchCase", "ReviewDecision", "ScanRun", "Source",
    "SourceSnapshot", "RetrievalReceipt", "WatchEntity",
]
