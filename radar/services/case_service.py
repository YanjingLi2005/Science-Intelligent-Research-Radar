"""Research case creation and deterministic Golden Case loading."""

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, sessionmaker

from radar.config import get_settings
from radar.db import SessionLocal, init_database, session_scope
from radar.models import (
    ActionItem, AuditEvent, Claim, ClaimRevision, ClaimSourceLink, ClaimSurface, G0Feedback,
    ImpactCandidate, ManuscriptVersion, ModelRun, PatchProposal, ResearchCase, ReviewDecision,
    RetrievalReceipt, ScanRun, Source, SourceSnapshot, WatchEntity,
)
from radar.parsers import parser_for
from radar.services.claim_service import ClaimService
from radar.services.condition_service import ConditionService


DEMO_CASE_ID = "case-demo-radar"


class ScanActiveError(RuntimeError):
    """A case with an active scan cannot be deleted."""

    def __init__(self, case_id: str):
        super().__init__(f"case_scan_active:{case_id}")

# Below this much extracted text a PDF almost certainly has no text layer
# (scanned images), so fail fast instead of building an empty case.
MIN_MANUSCRIPT_TEXT_CHARS = 200


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


class CaseService:
    def __init__(self, session_factory: sessionmaker[Session] = SessionLocal):
        self.session_factory = session_factory
        self.condition_service = ConditionService()

    @staticmethod
    def is_demo_case(research_case: ResearchCase) -> bool:
        """Sample/demo cases ship with the app or are prefixed ``RARE:``."""

        settings = research_case.settings_json or {}
        return bool(settings.get("is_sample")) or research_case.title.startswith("RARE:")

    def active_case(self) -> ResearchCase | None:
        with session_scope(self.session_factory) as session:
            case = session.scalar(select(ResearchCase).order_by(ResearchCase.created_at.desc()))
            if case:
                session.expunge(case)
            return case

    def primary_case(self) -> ResearchCase | None:
        """Return the user's manuscript case and never select the synthetic fixture."""

        with session_scope(self.session_factory) as session:
            case = session.scalar(
                select(ResearchCase)
                .where(ResearchCase.id != DEMO_CASE_ID)
                .order_by(ResearchCase.updated_at.desc(), ResearchCase.created_at.desc())
            )
            if case:
                session.expunge(case)
            return case

    def list_cases(self, *, include_synthetic_demo: bool = True) -> list[ResearchCase]:
        with session_scope(self.session_factory) as session:
            statement = select(ResearchCase)
            if not include_synthetic_demo:
                statement = statement.where(ResearchCase.id != DEMO_CASE_ID)
            cases = list(
                session.scalars(
                    statement.order_by(
                        ResearchCase.updated_at.desc(), ResearchCase.created_at.desc()
                    )
                )
            )
            for research_case in cases:
                session.expunge(research_case)
            return cases

    def get_case(self, case_id: str) -> ResearchCase | None:
        with session_scope(self.session_factory) as session:
            research_case = session.get(ResearchCase, case_id)
            if research_case:
                session.expunge(research_case)
            return research_case

    def load_demo_case(self, fixture_dir: Path | None = None, reset: bool = False) -> str:
        fixture_dir = fixture_dir or get_settings().fixture_case_dir
        fixture_dir = Path(fixture_dir)
        init_database(self.session_factory.kw["bind"])
        if reset:
            self.reset_demo_case()

        with session_scope(self.session_factory) as session:
            if session.get(ResearchCase, DEMO_CASE_ID):
                return DEMO_CASE_ID

            case_data = _read_json(fixture_dir / "case.json")
            manuscript_meta = _read_json(fixture_dir / "manuscript.json")
            manuscript_content = (fixture_dir / case_data["manuscript_file"]).read_text(encoding="utf-8")
            research_case = ResearchCase(
                id=case_data["id"], title=case_data["title"],
                research_question=case_data["research_question"], field=case_data["field"],
                settings_json={**case_data.get("settings", {}), "fixture_dir": str(fixture_dir)},
            )
            manuscript = ManuscriptVersion(
                id=manuscript_meta["id"], case_id=research_case.id,
                version_no=manuscript_meta["version_no"], file_name=manuscript_meta["file_name"],
                source_type=manuscript_meta["source_type"], content_text=manuscript_content,
                content_hash=hashlib.sha256(manuscript_content.encode()).hexdigest(), is_current=True,
            )
            session.add(research_case)
            session.flush()
            session.add(manuscript)
            session.flush()

            claims_data = _read_json(fixture_dir / "claims_gold.json")
            claim_contracts: dict[str, dict] = {}
            for item in claims_data:
                claim = Claim(
                    id=item["id"], case_id=research_case.id, stable_key=item["stable_key"],
                    lifecycle_state="active",
                )
                session.add(claim)
                session.flush()
                session.add(
                    ClaimRevision(
                        id=item["revision_id"], claim_id=item["id"], manuscript_version_id=manuscript.id,
                        revision_no=1, statement=item["statement"], claim_type="empirical_result",
                        centrality=item["centrality"], contract_json=item["contract"],
                        falsifiable_condition=item["falsifiable_condition"], source_quote=item["source_quote"],
                        source_locator=item["source_locator"], review_state="confirmed",
                    )
                )
                session.flush()
                claim_contracts[item["revision_id"]] = item["contract"]

            for item in _read_json(fixture_dir / "claim_surfaces.json"):
                session.add(
                    ClaimSurface(
                        id=item["id"], claim_revision_id=item["claim_revision_id"],
                        manuscript_version_id=manuscript.id, section=item["section"],
                        locator=item["locator"], quote=item["quote"], surface_role=item["surface_role"],
                    )
                )
            for item in _read_json(fixture_dir / "watch_entities.json"):
                session.add(
                    WatchEntity(
                        id=item["id"], case_id=research_case.id, entity_type=item["entity_type"],
                        canonical_name=item["canonical_name"], aliases_json=item["aliases"],
                    )
                )

            snapshot_by_source: dict[str, str] = {}
            for index, item in enumerate(_read_json(fixture_dir / "sources.json"), start=1):
                content = (fixture_dir / item["content_file"]).read_text(encoding="utf-8").strip()
                snapshot_id = f"snapshot-{index:02d}"
                snapshot_by_source[item["id"]] = snapshot_id
                source = Source(
                    id=item["id"], external_id=item["external_id"], title=item["title"],
                    authors_json=item["authors"], published_at=_dt(item.get("published_at")),
                    url=item["url"], doi=item.get("doi"), arxiv_id=item.get("arxiv_id"),
                    license=item.get("license"), integrity_state=item["integrity_state"],
                )
                session.add(source)
                session.flush()
                session.add(
                    SourceSnapshot(
                        id=snapshot_id, source_id=item["id"], version_label="fixture-v1",
                        title=item["title"], abstract=content, content_text=content,
                        content_hash=hashlib.sha256(content.encode()).hexdigest(),
                        event_time=_dt(item.get("published_at")), observed_at=_dt(item.get("published_at")),
                    )
                )

            now = datetime.now().astimezone()
            scan = ScanRun(
                id=case_data["scan_run_id"], case_id=research_case.id, mode="fixture",
                status="completed", started_at=now, finished_at=now,
                query_json={"fixture": "golden_case", "candidate_count": 20},
                stats_json={"scanned_papers": 20},
            )
            session.add(scan)
            session.flush()

            for item in _read_json(fixture_dir / "impacts_gold.json"):
                differences = self.condition_service.compare(
                    claim_contracts[item["claim_revision_id"]], item["incoming_contract"]
                )
                comparability = self.condition_service.overall_comparability(differences)
                evidence_new = {
                    **item["evidence_new"],
                    "source_snapshot_id": snapshot_by_source[item["source_id"]],
                }
                session.add(
                    ImpactCandidate(
                        id=item["id"], scan_run_id=scan.id,
                        claim_revision_id=item["claim_revision_id"],
                        source_snapshot_id=snapshot_by_source[item["source_id"]],
                        event_type=item["event_type"], stance=item["stance"],
                        impact_mode=item["impact_mode"], strategic_flags_json=item["strategic_flags"],
                        comparability=comparability,
                        condition_differences_json=[difference.model_dump() for difference in differences],
                        evidence_own_json=item["evidence_own"], evidence_new_json=evidence_new,
                        change_depth=item["change_depth"], severity=item["severity"],
                        suggested_action=item["suggested_action"], uncertainty_json=item["uncertainty"],
                        review_state="candidate", trust_state="verified",
                    )
                )

            session.flush()

            for item in _read_json(fixture_dir / "claim_source_links.json"):
                session.add(ClaimSourceLink(**item))
            session.flush()
            session.add(
                ModelRun(
                    id=str(uuid4()), stage="impact_assessment", provider="mock",
                    case_id=research_case.id, scan_run_id=scan.id,
                    model="golden-case-v1", prompt_hash=hashlib.sha256(b"golden-case-v1").hexdigest(),
                    schema_version="ImpactAssessmentOutput.v1",
                    input_refs_json=[scan.id], raw_response="fixture",
                    parsed_output_json={"impact_ids": [f"impact-{index:02d}" for index in range(1, 8)]},
                    validation_json={"exact_evidence": True, "condition_gate": True},
                    input_tokens=0, output_tokens=0, estimated_cost=0.0, latency_ms=0,
                )
            )
            session.add(
                AuditEvent(
                    id=str(uuid4()), case_id=research_case.id, event_type="demo_case_loaded",
                    object_type="ResearchCase", object_id=research_case.id,
                    payload_json={"claims": 10, "sources": 20, "impacts": 7},
                    actor_type="system", actor_id="fixture_loader",
                )
            )
        from radar.services.action_service import ActionService

        ActionService(self.session_factory).sync_scan_actions(scan.id)
        return DEMO_CASE_ID

    def add_watch_entity(
        self,
        case_id: str,
        *,
        entity_type: str,
        canonical_name: str,
        aliases: list[str] | None = None,
    ) -> str:
        """Register one competitor/team alias group for a case."""

        watch_id = str(uuid4())
        with session_scope(self.session_factory) as session:
            if session.get(ResearchCase, case_id) is None:
                raise LookupError(f"case not found: {case_id}")
            session.add(
                WatchEntity(
                    id=watch_id,
                    case_id=case_id,
                    entity_type=entity_type,
                    canonical_name=canonical_name,
                    aliases_json=list(aliases or []),
                )
            )
            session.add(
                AuditEvent(
                    id=str(uuid4()), case_id=case_id, event_type="watch_entity_added",
                    object_type="WatchEntity", object_id=watch_id,
                    payload_json={"entity_type": entity_type, "canonical_name": canonical_name},
                    actor_type="human", actor_id="local_user",
                )
            )
        return watch_id

    def remove_watch_entity(self, watch_id: str) -> None:
        with session_scope(self.session_factory) as session:
            watch = session.get(WatchEntity, watch_id)
            if watch is None:
                raise LookupError(f"watch entity not found: {watch_id}")
            session.add(
                AuditEvent(
                    id=str(uuid4()), case_id=watch.case_id, event_type="watch_entity_removed",
                    object_type="WatchEntity", object_id=watch_id,
                    payload_json={"canonical_name": watch.canonical_name},
                    actor_type="human", actor_id="local_user",
                )
            )
            session.delete(watch)

    def create_case(self, *, title: str, research_question: str, manuscript_path: Path) -> str:
        parsed = parser_for(manuscript_path).parse(manuscript_path)
        if len((parsed.full_text or "").strip()) < MIN_MANUSCRIPT_TEXT_CHARS:
            raise ValueError(
                "文稿文本提取失败：提取到的正文不足 "
                f"{MIN_MANUSCRIPT_TEXT_CHARS} 字符。该 PDF 很可能是扫描件或缺少文本层，"
                "请先用 OCR 工具生成可复制文本的版本，或改传 .tex/.md 文稿后重试。"
            )
        case_id = str(uuid4())
        manuscript_id = str(uuid4())
        with session_scope(self.session_factory) as session:
            research_case = ResearchCase(
                id=case_id, title=title, research_question=research_question,
                field="cs_ai", settings_json={"is_sample": False},
            )
            session.add(research_case)
            session.flush()
            session.add(
                ManuscriptVersion(
                    id=manuscript_id, case_id=case_id, version_no=1,
                    file_name=manuscript_path.name, source_type=manuscript_path.suffix.lstrip("."),
                    content_text=parsed.full_text, content_hash=parsed.content_hash, is_current=True,
                )
            )
            session.flush()
            session.add(
                AuditEvent(
                    id=str(uuid4()), case_id=case_id, event_type="case_created",
                    object_type="ResearchCase", object_id=case_id,
                    payload_json={"manuscript_id": manuscript_id}, actor_type="human", actor_id="local_user",
                )
            )
        ClaimService(self.session_factory).extract_candidates(manuscript_id)
        return case_id

    def add_manuscript_version(self, case_id: str, manuscript_path: Path) -> dict:
        """Add a new current manuscript without losing project history."""

        parsed = parser_for(manuscript_path).parse(manuscript_path)
        manuscript_id = str(uuid4())
        with session_scope(self.session_factory) as session:
            research_case = session.get(ResearchCase, case_id)
            if research_case is None:
                raise LookupError(f"case not found: {case_id}")
            current = session.scalar(
                select(ManuscriptVersion).where(
                    ManuscriptVersion.case_id == case_id,
                    ManuscriptVersion.is_current.is_(True),
                )
            )
            if current and current.content_hash == parsed.content_hash:
                return {
                    "manuscript_id": current.id,
                    "version_no": current.version_no,
                    "unchanged": True,
                    "carried_claims": 0,
                    "new_candidates": 0,
                    "previous_claims_not_found": 0,
                    "lost_claims": [],
                }
            next_version = (
                session.scalar(
                    select(func.max(ManuscriptVersion.version_no)).where(
                        ManuscriptVersion.case_id == case_id
                    )
                )
                or 0
            ) + 1
            current_versions = list(
                session.scalars(
                    select(ManuscriptVersion).where(
                        ManuscriptVersion.case_id == case_id,
                        ManuscriptVersion.is_current.is_(True),
                    )
                )
            )
            for version in current_versions:
                version.is_current = False
            session.add(
                ManuscriptVersion(
                    id=manuscript_id,
                    case_id=case_id,
                    version_no=next_version,
                    file_name=manuscript_path.name,
                    source_type=manuscript_path.suffix.lstrip("."),
                    content_text=parsed.full_text,
                    content_hash=parsed.content_hash,
                    is_current=True,
                )
            )
            research_case.updated_at = datetime.now(timezone.utc)
            session.add(
                AuditEvent(
                    id=str(uuid4()),
                    case_id=case_id,
                    event_type="manuscript_version_added",
                    object_type="ManuscriptVersion",
                    object_id=manuscript_id,
                    payload_json={
                        "version_no": next_version,
                        "file_name": manuscript_path.name,
                        "supersedes_manuscript_id": current.id if current else None,
                    },
                    actor_type="human",
                    actor_id="local_user",
                )
            )
        sync = ClaimService(self.session_factory).sync_manuscript_version(manuscript_id)
        return {
            "manuscript_id": manuscript_id,
            "version_no": next_version,
            "unchanged": False,
            **sync,
        }

    def delete_case(self, case_id: str) -> None:
        """Permanently delete a case and all its associated data."""
        with session_scope(self.session_factory) as session:
            active_scan = session.scalar(
                select(ScanRun.id).where(
                    ScanRun.case_id == case_id,
                    ScanRun.status.in_(["running", "cancel_requested"]),
                )
            )
            if active_scan is not None:
                raise ScanActiveError(case_id)
            scan_ids = list(session.scalars(select(ScanRun.id).where(ScanRun.case_id == case_id)))
            impact_ids = list(
                session.scalars(select(ImpactCandidate.id).where(ImpactCandidate.scan_run_id.in_(scan_ids)))
            ) if scan_ids else []
            claim_ids = list(session.scalars(select(Claim.id).where(Claim.case_id == case_id)))
            revision_ids = list(
                session.scalars(select(ClaimRevision.id).where(ClaimRevision.claim_id.in_(claim_ids)))
            ) if claim_ids else []
            manuscript_ids = list(
                session.scalars(select(ManuscriptVersion.id).where(ManuscriptVersion.case_id == case_id))
            )
            # Delete case-owned leaf rows before the entities they reference.
            session.execute(delete(ActionItem).where(ActionItem.case_id == case_id))
            session.execute(delete(PatchProposal).where(PatchProposal.case_id == case_id))
            if impact_ids:
                session.execute(delete(ReviewDecision).where(ReviewDecision.impact_candidate_id.in_(impact_ids)))
                session.execute(delete(ImpactCandidate).where(ImpactCandidate.id.in_(impact_ids)))
            model_run_filter = ModelRun.case_id == case_id
            if scan_ids:
                model_run_filter = model_run_filter | ModelRun.scan_run_id.in_(scan_ids)
            session.execute(delete(ModelRun).where(model_run_filter))
            if scan_ids:
                session.execute(delete(RetrievalReceipt).where(RetrievalReceipt.scan_run_id.in_(scan_ids)))
                session.execute(delete(ScanRun).where(ScanRun.id.in_(scan_ids)))
            session.execute(delete(RetrievalReceipt).where(RetrievalReceipt.case_id == case_id))
            if revision_ids:
                session.execute(delete(ClaimSourceLink).where(ClaimSourceLink.claim_revision_id.in_(revision_ids)))
                session.execute(delete(ClaimSurface).where(ClaimSurface.claim_revision_id.in_(revision_ids)))
                session.execute(delete(G0Feedback).where(G0Feedback.claim_revision_id.in_(revision_ids)))
                session.execute(delete(ClaimRevision).where(ClaimRevision.id.in_(revision_ids)))
            if claim_ids:
                session.execute(delete(Claim).where(Claim.id.in_(claim_ids)))
            session.execute(delete(WatchEntity).where(WatchEntity.case_id == case_id))
            if manuscript_ids:
                session.execute(delete(ManuscriptVersion).where(ManuscriptVersion.id.in_(manuscript_ids)))
            session.execute(delete(AuditEvent).where(AuditEvent.case_id == case_id))
            session.execute(delete(ResearchCase).where(ResearchCase.id == case_id))

    def reset_demo_case(self) -> None:
        with session_scope(self.session_factory) as session:
            scan_ids = list(session.scalars(select(ScanRun.id).where(ScanRun.case_id == DEMO_CASE_ID)))
            impact_ids = list(
                session.scalars(select(ImpactCandidate.id).where(ImpactCandidate.scan_run_id.in_(scan_ids)))
            ) if scan_ids else []
            claim_ids = list(session.scalars(select(Claim.id).where(Claim.case_id == DEMO_CASE_ID)))
            revision_ids = list(
                session.scalars(select(ClaimRevision.id).where(ClaimRevision.claim_id.in_(claim_ids)))
            ) if claim_ids else []
            manuscript_ids = list(
                session.scalars(select(ManuscriptVersion.id).where(ManuscriptVersion.case_id == DEMO_CASE_ID))
            )
            if impact_ids:
                session.execute(delete(ActionItem).where(ActionItem.impact_candidate_id.in_(impact_ids)))
                session.execute(delete(ReviewDecision).where(ReviewDecision.impact_candidate_id.in_(impact_ids)))
                session.execute(delete(PatchProposal).where(PatchProposal.impact_candidate_id.in_(impact_ids)))
                session.execute(delete(ImpactCandidate).where(ImpactCandidate.id.in_(impact_ids)))
            if scan_ids:
                session.execute(delete(RetrievalReceipt).where(RetrievalReceipt.scan_run_id.in_(scan_ids)))
                session.execute(delete(ScanRun).where(ScanRun.id.in_(scan_ids)))
            session.execute(delete(RetrievalReceipt).where(RetrievalReceipt.case_id == DEMO_CASE_ID))
            if revision_ids:
                session.execute(delete(ClaimSourceLink).where(ClaimSourceLink.claim_revision_id.in_(revision_ids)))
                session.execute(delete(ClaimSurface).where(ClaimSurface.claim_revision_id.in_(revision_ids)))
                session.execute(delete(G0Feedback).where(G0Feedback.claim_revision_id.in_(revision_ids)))
                session.execute(delete(ClaimRevision).where(ClaimRevision.id.in_(revision_ids)))
            if claim_ids:
                session.execute(delete(Claim).where(Claim.id.in_(claim_ids)))
            session.execute(delete(WatchEntity).where(WatchEntity.case_id == DEMO_CASE_ID))
            if manuscript_ids:
                session.execute(delete(ManuscriptVersion).where(ManuscriptVersion.id.in_(manuscript_ids)))
            session.execute(delete(AuditEvent).where(AuditEvent.case_id == DEMO_CASE_ID))
            session.execute(delete(ResearchCase).where(ResearchCase.id == DEMO_CASE_ID))
            session.execute(delete(ModelRun).where(ModelRun.provider == "mock"))
            snapshot_ids = list(
                session.scalars(select(SourceSnapshot.id).where(SourceSnapshot.source_id.like("source-%")))
            )
            if snapshot_ids:
                session.execute(delete(SourceSnapshot).where(SourceSnapshot.id.in_(snapshot_ids)))
            session.execute(delete(Source).where(Source.id.like("source-%")))
