"""Weekly summary, evidence pack, and audit export."""

import json

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from radar.db import SessionLocal, session_scope
from radar.models import (
    ActionItem,
    AuditEvent,
    Claim,
    ClaimRevision,
    ImpactCandidate,
    ScanRun,
    Source,
    SourceSnapshot,
)
from radar.services.action_service import ActionService
from radar.services.ledger_service import LedgerService


class ReportService:
    def __init__(self, session_factory: sessionmaker[Session] = SessionLocal):
        self.session_factory = session_factory
        self.ledger = LedgerService(session_factory)
        self.actions = ActionService(session_factory)

    def get_weekly_summary(self, scan_run_id: str) -> dict[str, int]:
        with session_scope(self.session_factory) as session:
            scan = session.get(ScanRun, scan_run_id)
            if scan is None:
                raise LookupError(f"scan run not found: {scan_run_id}")
            impacts = list(
                session.scalars(select(ImpactCandidate).where(ImpactCandidate.scan_run_id == scan_run_id))
            )
            material_impacts = [
                item
                for item in impacts
                if item.impact_mode != "no_material_change"
                or item.event_type == "retraction"
                or bool(item.strategic_flags_json)
            ]
            return {
                "scanned_papers": int(scan.stats_json.get("scanned_papers", 0)),
                "routed_papers": int(scan.stats_json.get("routed_pairs", 0)),
                "related_papers": len(
                    {item.source_snapshot_id for item in material_impacts}
                ),
                "critical": sum(item.severity == "critical" for item in material_impacts),
                "review": sum(item.severity == "review" for item in material_impacts),
                "informative": sum(
                    item.severity == "informative" for item in material_impacts
                ),
                "supports": sum(item.stance == "supports" for item in material_impacts),
                "challenges": sum(item.stance == "challenges" for item in material_impacts),
                "competitor_alerts": sum(
                    "competitor" in item.strategic_flags_json
                    for item in material_impacts
                ),
                "integrity_alerts": sum(
                    item.impact_mode == "research_integrity"
                    for item in material_impacts
                ),
            }

    def get_weekly_action_report(self, scan_run_id: str) -> dict:
        self.actions.sync_scan_actions(scan_run_id)
        actions = self.actions.list_actions(
            case_id=self._scan_case_id(scan_run_id),
            scan_run_id=scan_run_id,
        )
        counts_by_type = {
            action_type: sum(item.action_type == action_type for item in actions)
            for action_type in {
                "team_decision",
                "experiment",
                "data",
                "writing",
                "cite",
                "competitor_response",
                "revalidation",
            }
        }
        urgent = sum(item.priority in {"critical", "high"} for item in actions)
        summary = self.get_weekly_summary(scan_run_id)
        return {
            "scan_run_id": scan_run_id,
            "headline": (
                f"本周 {summary['related_papers']} 篇论文产生 "
                f"{len(actions)} 个项目动作，其中 {urgent} 个高优先级。"
            ),
            "urgent": urgent,
            "open_actions": len(actions),
            "counts_by_type": counts_by_type,
            "summary": summary,
            "actions": [
                {
                    "id": item.id,
                    "type": item.action_type,
                    "priority": item.priority,
                    "title": item.title,
                    "rationale": item.rationale,
                    "checklist": item.checklist_json,
                    "due": item.due_label,
                    "status": item.status,
                    "impact_candidate_id": item.impact_candidate_id,
                    "claim_revision_id": item.claim_revision_id,
                }
                for item in actions
            ],
        }

    def _scan_case_id(self, scan_run_id: str) -> str:
        with session_scope(self.session_factory) as session:
            scan = session.get(ScanRun, scan_run_id)
            if scan is None:
                raise LookupError(f"scan run not found: {scan_run_id}")
            return scan.case_id

    def get_writing_brief(self, case_id: str) -> dict:
        with session_scope(self.session_factory) as session:
            impacts = list(
                session.scalars(
                    select(ImpactCandidate)
                    .join(ScanRun, ScanRun.id == ImpactCandidate.scan_run_id)
                    .where(
                        ScanRun.case_id == case_id,
                        ImpactCandidate.trust_state == "verified",
                        ImpactCandidate.review_state != "dismissed",
                        ImpactCandidate.impact_mode != "no_material_change",
                    )
                    .order_by(ImpactCandidate.created_at.desc())
                )
            )
            entries: list[dict] = []
            seen: set[tuple[str, str, str, str]] = set()
            for impact in impacts:
                revision = session.get(ClaimRevision, impact.claim_revision_id)
                claim = session.get(Claim, revision.claim_id) if revision else None
                snapshot = session.get(SourceSnapshot, impact.source_snapshot_id)
                source = session.get(Source, snapshot.source_id) if snapshot else None
                if not all([revision, claim, source]):
                    continue
                dedupe_key = (
                    claim.stable_key,
                    source.id,
                    impact.stance,
                    impact.impact_mode,
                )
                if dedupe_key in seen:
                    continue
                seen.add(dedupe_key)
                entries.append(
                    {
                        "impact_id": impact.id,
                        "claim": claim.stable_key,
                        "claim_statement": revision.statement,
                        "stance": impact.stance,
                        "impact_mode": impact.impact_mode,
                        "source_title": source.title,
                        "source_url": source.url,
                        "source_pdf_url": source.pdf_url,
                        "source_doi": source.doi,
                        "source_venue": source.venue or "arXiv",
                        "source_publication_type": source.publication_type or "preprint",
                        "source_published_at": (
                            source.published_at.date().isoformat()
                            if source.published_at
                            else None
                        ),
                        "evidence": impact.evidence_new_json,
                        "comparability": impact.comparability,
                        "review_state": impact.review_state,
                        "suggested_action": impact.suggested_action,
                    }
                )
            writing_actions = list(
                session.scalars(
                    select(ActionItem).where(
                        ActionItem.case_id == case_id,
                        ActionItem.action_type == "writing",
                        ActionItem.status != "dismissed",
                    )
                )
            )

        supports = [item for item in entries if item["stance"] == "supports"]
        challenges = [item for item in entries if item["stance"] == "challenges"]
        integrity = [
            item for item in entries if item["impact_mode"] == "research_integrity"
        ]
        context = [
            item
            for item in entries
            if item not in supports
            and item not in challenges
            and item not in integrity
        ]
        return {
            "supports": supports,
            "challenges": challenges,
            "boundary_and_prior_art": context,
            "integrity": integrity,
            "writing_actions": [
                {
                    "id": item.id,
                    "priority": item.priority,
                    "title": item.title,
                    "rationale": item.rationale,
                    "checklist": item.checklist_json,
                    "status": item.status,
                }
                for item in writing_actions
            ],
        }

    def export_writing_brief(self, case_id: str) -> str:
        brief = self.get_writing_brief(case_id)
        lines = [
            "# Research Radar — Discussion Evidence Brief",
            "",
            "Candidate evidence is clearly labeled until human confirmation.",
            "",
        ]
        for heading, key in [
            ("Supporting evidence", "supports"),
            ("Counter-evidence", "challenges"),
            ("Boundary conditions and prior art", "boundary_and_prior_art"),
            ("Integrity risks", "integrity"),
        ]:
            lines.extend([f"## {heading}", ""])
            entries = brief[key]
            if not entries:
                lines.extend(["- None recorded.", ""])
                continue
            for item in entries:
                quote = item["evidence"].get("quote", "")
                locator = item["evidence"].get("locator", "")
                lines.extend(
                    [
                        f"### {item['claim']} — {item['source_title']}",
                        "",
                        f"- State: `{item['review_state']}`; stance: `{item['stance']}`; comparability: `{item['comparability']}`",
                        f"- Suggested action: `{item['suggested_action']}`",
                        f"- Title: {item['source_title']}",
                        f"- Venue: {item['source_venue']} ({item['source_publication_type']})",
                        f"- Published: {item['source_published_at'] or 'not registered'}",
                        f"- Original: {item['source_url']}",
                        f"- PDF: {item['source_pdf_url'] or 'not registered'}",
                        f"- DOI: {item['source_doi'] or 'not registered'}",
                        f"> {quote}",
                        f"- Locator: `{locator}`",
                        "",
                    ]
                )
        lines.extend(["## Writing actions", ""])
        for item in brief["writing_actions"]:
            lines.append(
                f"- [{item['status']}] **{item['title']}** — {item['rationale']}"
            )
        return "\n".join(lines).strip() + "\n"

    def get_evidence_pack(self, claim_id: str) -> dict:
        ledger = self.ledger.get_claim_ledger(claim_id)
        return {
            "schema": "ResearchRadarEvidencePack.v1",
            "claim": {
                "id": ledger["claim_id"], "stable_key": ledger["stable_key"],
                "statement": ledger["statement"], "centrality": ledger["centrality"],
                "contract": ledger["contract"], "health": ledger["health"],
            },
            "supports": ledger["supports"],
            "challenges": ledger["challenges"],
            "integrity": ledger["integrity"],
            "safety_note": "Only human-confirmed decisions are included.",
        }

    def audit_export(self, case_id: str, *, limit: int | None = 500) -> str:
        """Export audit events as JSON, most recent first, capped at ``limit``.

        Pass ``limit=None`` for the historical full-table dump.
        """

        with session_scope(self.session_factory) as session:
            statement = (
                select(AuditEvent)
                .where(AuditEvent.case_id == case_id)
                .order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
            )
            if limit is not None:
                statement = statement.limit(limit)
            events = list(session.scalars(statement))
            payload = [
                {
                    "id": event.id, "event_type": event.event_type,
                    "object_type": event.object_type, "object_id": event.object_id,
                    "payload": event.payload_json, "actor_type": event.actor_type,
                    "actor_id": event.actor_id, "created_at": event.created_at.isoformat(),
                }
                for event in events
            ]
            return json.dumps(payload, ensure_ascii=False, indent=2)
