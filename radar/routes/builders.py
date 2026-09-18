"""Internal projection builders: converts ORM entities into API response schemas."""

from __future__ import annotations

from typing import Any
from sqlalchemy import func, select

from radar.api_schemas import (
    _ACTION_TYPE_KIND,
    _IMPACT_SUGGESTION,
    _PRIORITY_MAP,
    _REVIEW_STATE_CLAIM_STATUS,
    _SEVERITY_URGENCY,
    _STANCE_VERDICT,
    _cost_yuan,
    _duration_ms,
    _parse_date,
    _parse_datetime,
    ActionItemOut,
    AuditRecOut,
    CitationHealthOut,
    CitationIssueOut,
    ClaimOut,
    CompetitorEntry,
    Contract,
    EvidenceItem,
    MatrixRow,
    PaperOut,
    ProjectProfile,
    ProjectSummary,
    RetrievalReceiptOut,
    RewriteView,
    VersionRecOut,
)
from radar.db import get_session_factory, session_scope
from radar.models import (
    ActionItem,
    Claim,
    ClaimRevision,
    ImpactCandidate,
    ManuscriptVersion,
    ModelRun,
    PatchProposal,
    ResearchCase,
    RetrievalReceipt,
    ScanRun,
    Source,
    SourceSnapshot,
    WatchEntity,
)
from radar.services.action_service import ActionService
from radar.services.citation_service import CitationService
from radar.services.impact_service import ImpactService
from radar.services.manuscript_understanding_service import ManuscriptUnderstandingService
from radar.services.retrieval_service import RetrievalService
from radar.routes.deps import _service


def _build_summary(case: ResearchCase) -> dict[str, Any]:
    """Build a ProjectSummary from a ResearchCase row + derived counts."""
    claims_total = 0
    claims_confirmed = 0
    latest_scan = ""
    file_name = ""
    version_label = "v1"
    urgent_count = 0

    with session_scope() as session:
        claims_total = session.scalar(
            select(func.count(Claim.id)).where(Claim.case_id == case.id)
        ) or 0

        confirmed_ct = session.scalar(
            select(func.count(Claim.id))
            .join(ClaimRevision, ClaimRevision.claim_id == Claim.id)
            .where(
                Claim.case_id == case.id,
                ClaimRevision.review_state == "confirmed",
            )
        ) or 0
        claims_confirmed = confirmed_ct

        latest_scan_row = session.scalar(
            select(ScanRun)
            .where(ScanRun.case_id == case.id)
            .order_by(ScanRun.created_at.desc())
        )
        if latest_scan_row:
            latest_scan = _parse_datetime(latest_scan_row.finished_at or latest_scan_row.created_at)

        manuscript = session.scalar(
            select(ManuscriptVersion)
            .where(
                ManuscriptVersion.case_id == case.id,
                ManuscriptVersion.is_current.is_(True),
            )
        )
        if manuscript:
            file_name = manuscript.file_name
            version_label = f"v{manuscript.version_no}"

        urgent_count = session.scalar(
            select(func.count(ActionItem.id)).where(
                ActionItem.case_id == case.id,
                ActionItem.priority.in_(["critical", "high"]),
                ActionItem.status.in_(["proposed", "open", "in_progress"]),
            )
        ) or 0

    short = case.title
    if ":" in short:
        short = short.split(":", 1)[0].strip()
    elif len(short) > 30:
        short = short[:30]

    return ProjectSummary(
        id=case.id,
        name=case.title,
        short=short,
        question=case.research_question,
        version=version_label,
        file=file_name,
        claimsConfirmed=claims_confirmed,
        claimsTotal=claims_total,
        lastScan=latest_scan,
        urgent=urgent_count,
        topics=_case_topics(case),
    ).model_dump()


def _build_project(case_id: str, case: ResearchCase) -> dict[str, Any]:
    """Build a full ProjectOut from a ResearchCase."""
    summary = _build_summary(case)
    claims = _build_claims(case_id)
    papers = _build_papers(case_id)
    actions = _build_actions(case_id)
    versions = _build_versions(case_id)
    audit = _build_audit(case_id)
    competitors = _build_competitors(case_id)
    profile = _build_profile(case_id)
    rewrite = _build_rewrite(case_id)
    retrieval_receipts = _build_retrieval_receipts(case_id)
    citation_health = _build_citation_health(case_id)

    return {
        **summary,
        "claims": [c.model_dump() for c in claims],
        "papers": papers,
        "actions": actions,
        "versions": versions,
        "audit": audit,
        "competitors": competitors,
        "profile": profile,
        "rewrite": rewrite,
        "fidelity": _latest_fidelity(case_id),
        "retrievalReceipts": retrieval_receipts,
        "citationHealth": citation_health,
    }


def _build_retrieval_receipts(case_id: str) -> list[dict[str, Any]]:
    """Map recent retrieval receipts to the frontend's camelCase contract."""
    with session_scope() as session:
        rows = list(
            session.scalars(
                select(RetrievalReceipt)
                .where(RetrievalReceipt.case_id == case_id)
                .order_by(RetrievalReceipt.fetched_at.desc())
                .limit(50)
            )
        )
    return [
        RetrievalReceiptOut(
            id=row.id,
            sourceKind=row.source_kind,
            endpoint=row.endpoint,
            query=row.query_text,
            requestedCount=row.requested_count,
            retrievedCount=row.retrieved_count,
            pages=row.pages,
            responseHash=row.response_hash,
            status=row.status,
            warnings=row.warnings_json or [],
            fetchedAt=_parse_datetime(row.fetched_at),
        ).model_dump()
        for row in rows
    ]


def _build_citation_health(case_id: str) -> dict[str, Any]:
    """Map the deterministic citation service result to API casing."""
    raw = CitationService(get_session_factory()).analyze_case(case_id)
    return CitationHealthOut(
        manuscriptVersionId=raw.get("manuscript_version_id", ""),
        checkedAt=raw.get("checked_at", ""),
        referenceEntries=raw.get("reference_entries", 0),
        identifiers=raw.get("identifiers", []),
        identifierCount=raw.get("identifier_count", 0),
        resolvedCount=raw.get("resolved_count", 0),
        metadataCoverage=raw.get("metadata_coverage", 1.0),
        citationKeys=raw.get("citation_keys", 0),
        bibliographyKeys=raw.get("bibliography_keys", 0),
        unresolvedKeys=raw.get("unresolved_keys", []),
        unusedKeys=raw.get("unused_keys", []),
        duplicateIdentifiers=raw.get("duplicate_identifiers", []),
        integrity=raw.get("integrity", {}),
        issues=[CitationIssueOut(**item) for item in raw.get("issues", [])],
    ).model_dump()


def _latest_fidelity(case_id: str) -> dict | None:
    """Fidelity block of the latest completed scan, or None when no scan has finished."""
    with session_scope() as session:
        scan = session.scalar(
            select(ScanRun)
            .where(
                ScanRun.case_id == case_id,
                ScanRun.status == "completed",
            )
            .order_by(ScanRun.created_at.desc())
        )
        if scan is None:
            return None
        fidelity = (scan.stats_json or {}).get("fidelity")
        return fidelity if isinstance(fidelity, dict) else None


def _build_claims(case_id: str) -> list[ClaimOut]:
    """Build ClaimOut list: one entry per claim, using the latest revision."""
    with session_scope() as session:
        claims = list(session.scalars(select(Claim).where(Claim.case_id == case_id)))
        result: list[ClaimOut] = []
        for claim in claims:
            rev = session.scalar(
                select(ClaimRevision)
                .where(ClaimRevision.claim_id == claim.id)
                .order_by(ClaimRevision.revision_no.desc())
            )
            if rev is None:
                continue
            result.append(_revision_to_claim_out(rev))
        return result


def _revision_to_claim_out(rev: ClaimRevision) -> ClaimOut:
    """Map one ClaimRevision to a ClaimOut."""
    contract_raw = rev.contract_json or {}
    contract = Contract(
        task=contract_raw.get("task") or "",
        dataset=contract_raw.get("dataset") or "",
        split=contract_raw.get("split") or "",
        metric=contract_raw.get("metric") or "",
        baseline=contract_raw.get("comparator") or contract_raw.get("baseline") or "",
        scope=contract_raw.get("scope") or "",
    )

    evidence: list[EvidenceItem] = []
    with session_scope() as session:
        impacts = list(
            session.scalars(
                select(ImpactCandidate).where(
                    ImpactCandidate.claim_revision_id == rev.id,
                    ImpactCandidate.review_state.in_(["confirmed", "edited"]),
                )
            )
        )
        for imp in impacts:
            evidence.append(
                EvidenceItem(
                    kind=_stance_evidence_kind(imp.stance),
                    paperId=imp.id,
                    note=_IMPACT_SUGGESTION.get(imp.suggested_action, imp.suggested_action),
                )
            )

    radar_watch = any(
        e.kind == "challenge" or e.kind == "completeness" for e in evidence
    )

    return ClaimOut(
        id=rev.id,
        text=rev.statement,
        status=_REVIEW_STATE_CLAIM_STATUS.get(rev.review_state, "valid"),
        confirmed="yes" if rev.review_state in ("confirmed", "edited") else "pending",
        radarWatch=radar_watch,
        quote=rev.source_quote,
        loc=rev.source_locator,
        contract=contract,
        falsifiable=rev.falsifiable_condition,
        evidence=evidence,
    )


def _build_papers(case_id: str) -> list[dict[str, Any]]:
    """Build PaperOut list from impact candidates."""
    with session_scope() as session:
        impacts = list(
            session.scalars(
                select(ImpactCandidate)
                .join(ScanRun, ScanRun.id == ImpactCandidate.scan_run_id)
                .where(ScanRun.case_id == case_id)
                .order_by(ImpactCandidate.created_at.desc())
            )
        )
        result: list[dict[str, Any]] = []
        for imp in impacts:
            result.append(_impact_to_paper_out(imp).model_dump())
        return result


def _impact_to_paper_out(
    imp: ImpactCandidate, session_factory: Any = None
) -> PaperOut:
    """Map one ImpactCandidate to a PaperOut."""
    title = ""
    authors: list[str] = []
    source_kind = "unknown"
    source_identifier = ""
    source_url = ""
    doi = ""
    venue = ""
    publication_type = ""
    arxiv_id = ""
    arxiv_primary_category = ""
    fields_of_study: list[str] = []
    ccf_rank = ""
    date_str = ""
    claim_ids: list[str] = []

    with session_scope(session_factory) as session:
        snapshot = session.get(SourceSnapshot, imp.source_snapshot_id)
        if snapshot:
            source = session.get(Source, snapshot.source_id)
            if source:
                title = source.title
                authors = source.authors_json or []
                source_kind = source.source_kind or (
                    source.external_id.split(":", 1)[0]
                    if source.external_id
                    else "unknown"
                )
                source_identifier = source.external_id
                source_url = source.url or ""
                doi = source.doi or ""
                venue = source.venue or ""
                publication_type = source.publication_type or ""
                arxiv_id = source.arxiv_id or ""
                arxiv_primary_category = source.arxiv_primary_category or ""
                fields_of_study = source.fields_of_study_json or []
                ccf_rank = source.ccf_rank or ""
                date_str = _parse_date(source.published_at)

        rev = session.get(ClaimRevision, imp.claim_revision_id)
        if rev:
            claim_ids = [rev.claim_id]

    matrix: list[MatrixRow] = []
    for diff in imp.condition_differences_json or []:
        matrix.append(
            MatrixRow(
                field=diff.get("field", ""),
                ours=diff.get("own_value") or "",
                theirs=diff.get("incoming_value") or "",
                status=_diff_status(diff.get("status", "")),
            )
        )

    evidence_new = imp.evidence_new_json or {}
    evidence_own = imp.evidence_own_json or {}
    suggestion = _IMPACT_SUGGESTION.get(imp.suggested_action, imp.suggested_action)
    uncertainty = ""
    if imp.uncertainty_json:
        uncertainty = "; ".join(imp.uncertainty_json)

    strategy_payload = getattr(imp, "strategy_payload_json", {}) or {}
    adv_critique = strategy_payload.get("adversarial_critique") or {}
    lethal_q = (
        adv_critique.get("lethal_reviewer_question")
        if isinstance(adv_critique, dict)
        else ""
    ) or ""
    key_diff = (
        strategy_payload.get("key_difference")
        or (adv_critique.get("key_difference") if isinstance(adv_critique, dict) else {})
        or {}
    )
    defense = (
        strategy_payload.get("defense_strategy")
        or (adv_critique.get("defense_strategy") if isinstance(adv_critique, dict) else {})
        or {}
    )
    bench_comp = strategy_payload.get("benchmark_comparison")
    if not bench_comp:
        bench_comp = ImpactService.compute_benchmark_comparison(
            own_claim_contract=imp.evidence_own_json,
            incoming_metrics=ImpactService.extract_benchmark_metrics(evidence_new.get("quote", "")),
        )

    flags = list(getattr(imp, "strategic_flags_json", []) or [])
    payload_flags = strategy_payload.get("strategic_flags") or []
    if isinstance(payload_flags, list):
        for flag in payload_flags:
            if flag and flag not in flags:
                flags.append(flag)
    has_emp_dom = (
        bool(strategy_payload.get("empirical_dominance"))
        or ("EMPIRICAL_DOMINANCE" in flags)
        or ImpactService.has_empirical_dominance(bench_comp or [])
    )

    author_prof = strategy_payload.get("author_profile")
    influence_met = strategy_payload.get("influence_metrics")
    lab_time = strategy_payload.get("lab_timeline")
    if not author_prof or not influence_met or not lab_time:
        try:
            rep = _service(RetrievalService).enrich_reputation_and_influence(
                authors=authors,
                title=title,
                doi=doi,
                arxiv_id=arxiv_id,
                venue=venue,
                cited_by_count=source.cited_by_count if source else None,
                published_at=source.published_at if source else None,
                external_id=source_identifier,
            )
            author_prof = author_prof or rep.get("author_profile") or {}
            influence_met = influence_met or rep.get("influence_metrics") or {}
            lab_time = lab_time or rep.get("lab_timeline") or []
        except Exception:
            author_prof = author_prof or {}
            influence_met = influence_met or {}
            lab_time = lab_time or []

    return PaperOut(
        id=imp.id,
        title=title,
        authors=authors,
        sourceKind=source_kind,
        sourceIdentifier=source_identifier,
        sourceUrl=source_url,
        doi=doi,
        venue=venue,
        publicationType=publication_type,
        arxivId=arxiv_id,
        arxivPrimaryCategory=arxiv_primary_category,
        fieldsOfStudy=fields_of_study,
        ccfRank=ccf_rank,
        date=date_str,
        verdict=_STANCE_VERDICT.get(imp.stance, "none"),
        urgency=_SEVERITY_URGENCY.get(imp.severity, "info"),
        claimIds=claim_ids,
        quote=evidence_new.get("quote", ""),
        quoteLoc=evidence_new.get("locator", ""),
        yourQuote=evidence_own.get("quote", ""),
        yourLoc=evidence_own.get("locator", ""),
        matrix=[m.model_dump() for m in matrix],
        why=suggestion,
        suggestion=suggestion,
        uncertainty=uncertainty,
        reviewState=(
            imp.review_state
            if imp.review_state in {"candidate", "edited", "confirmed", "dismissed"}
            else "candidate"
        ),
        impactType=strategy_payload.get("impact_type", _STANCE_VERDICT.get(imp.stance, "none")),
        confidenceScore=float(strategy_payload.get("confidence_score", 0.95)),
        oneSentenceVerdict=strategy_payload.get("one_sentence_verdict", ""),
        lethalReviewerQuestion=lethal_q,
        keyDifference=key_diff,
        defenseStrategy=defense,
        benchmarkComparison=bench_comp or [],
        strategicFlags=flags,
        empiricalDominance=has_emp_dom,
        authorProfile=author_prof or {},
        influenceMetrics=influence_met or {},
        labTimeline=lab_time or [],
    )


def _build_actions(case_id: str) -> list[dict[str, Any]]:
    """Build ActionItemOut list."""
    items = _service(ActionService).list_actions(case_id)
    return [_action_to_out(item) for item in items]


def _action_to_out(item: ActionItem) -> dict[str, Any]:
    """Map one ActionItem to ActionItemOut dict."""
    from radar.services.action_service import ACTION_SKILL_MAP

    return ActionItemOut(
        id=item.id,
        kind=_ACTION_TYPE_KIND.get(item.action_type, "writing"),
        priority=_PRIORITY_MAP.get(item.priority, "P2"),
        title=item.title,
        due=item.due_label,
        claimId=item.claim_revision_id or "",
        sourcePaperId=item.impact_candidate_id or "",
        reason=item.rationale or "",
        checklist=item.checklist_json,
        status=item.status,
        suggestedSkill=ACTION_SKILL_MAP.get(item.action_type, ""),
    ).model_dump()


def _build_versions(case_id: str) -> list[dict[str, Any]]:
    """Build VersionRecOut list from manuscript versions."""
    with session_scope() as session:
        versions = list(
            session.scalars(
                select(ManuscriptVersion)
                .where(ManuscriptVersion.case_id == case_id)
                .order_by(ManuscriptVersion.version_no.desc())
            )
        )
        result: list[dict[str, Any]] = []
        for ver in versions:
            claims_count = session.scalar(
                select(func.count(ClaimRevision.id)).where(
                    ClaimRevision.manuscript_version_id == ver.id
                )
            ) or 0
            result.append(
                VersionRecOut(
                    id=ver.id,
                    v=f"v{ver.version_no}",
                    date=_parse_date(ver.created_at),
                    file=ver.file_name,
                    claims=claims_count,
                    note=ver.source_type,
                ).model_dump()
            )
        return result


def _build_audit(case_id: str) -> list[dict[str, Any]]:
    """Build AuditRecOut list from ModelRun rows."""
    with session_scope() as session:
        runs = list(
            session.scalars(
                select(ModelRun)
                .where(ModelRun.case_id == case_id)
                .order_by(ModelRun.created_at.asc())
            )
        )
        result: list[dict[str, Any]] = []
        for run in runs:
            validation = run.validation_json or {}
            raw = (run.raw_response or "").lower()
            if validation.get("error") or "traceback" in raw or "llm_provider_failed" in raw:
                result_value = "fail"
            elif (
                validation.get("anchors_dropped")
                or validation.get("dropped_anchors")
                or validation.get("llm_error")
                or validation.get("failed_pairs")
                or validation.get("pydantic") is False
            ):
                result_value = "warn"
            else:
                result_value = "pass"
            result.append(
                AuditRecOut(
                    stage=run.stage,
                    provider=run.provider,
                    model=run.model,
                    duration=_duration_ms(run.latency_ms),
                    cost=_cost_yuan(run.estimated_cost),
                    result=result_value,
                ).model_dump()
            )
        return result


def _build_competitors(case_id: str) -> list[dict[str, Any]]:
    """Build competitor list."""
    with session_scope() as session:
        entities = list(
            session.scalars(
                select(WatchEntity).where(WatchEntity.case_id == case_id)
            )
        )
        return [
            CompetitorEntry(
                id=e.id, team=e.canonical_name, aliases=e.aliases_json
            ).model_dump()
            for e in entities
        ]


def _build_profile(case_id: str) -> dict[str, Any]:
    """Build profile dict from latest manuscript understanding."""
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


def _build_rewrite(case_id: str) -> dict[str, Any]:
    """Build the latest rewrite / patch proposal for the case."""
    with session_scope() as session:
        patch = session.scalar(
            select(PatchProposal)
            .where(
                PatchProposal.case_id == case_id,
                PatchProposal.approval_state.in_(["candidate", "approved"]),
            )
            .order_by(PatchProposal.created_at.desc())
        )
        if patch is None:
            return RewriteView().model_dump()

        claim_id = ""
        impact = session.get(ImpactCandidate, patch.impact_candidate_id)
        if impact:
            rev = session.get(ClaimRevision, impact.claim_revision_id)
            if rev:
                claim_id = rev.claim_id

        from radar.api_schemas import CheckItem

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
        ).model_dump()


def _case_topics(case: ResearchCase) -> list[str]:
    """Derive topics from the claim contracts and manuscript understanding."""
    topics: list[str] = []
    settings = case.settings_json or {}
    if isinstance(settings.get("topics"), list):
        topics.extend(settings["topics"])
    if case.field and case.field not in topics:
        topics.insert(0, case.field)
    return topics or []


def _stance_evidence_kind(stance: str) -> str:
    if stance == "challenges":
        return "challenge"
    if stance == "supports":
        return "support"
    return "completeness"


def _diff_status(raw: str) -> str:
    mapping = {
        "match": "same",
        "compatible_alias": "same",
        "partial": "partial",
        "mismatch": "diff",
        "unknown": "diff",
    }
    return mapping.get(raw, "same")
