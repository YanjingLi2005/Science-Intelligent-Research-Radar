"""Bounded deep-research service inspired by gpt-researcher.

The product already runs claim-driven weekly radar scans. This service adds a
free-form "deep research" mode: it decomposes a research question into
sub-queries, searches the same academic adapters (arXiv / OpenAlex /
Semantic Scholar), stores the papers as normal sources, compresses each
source into a citation-ready summary, and finally synthesizes a structured
brief with sources, contradictions, gaps, and next steps.

The design keeps the product's trust principles: every source in the brief is
a real stored paper with a URL/DOI, no fabricated citations are allowed, and
the LLM is used only to plan/compress/synthesize — never to invent evidence.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from radar.adapters.arxiv import ArxivSearchAdapter
from radar.adapters.chinese_literature import ChineseLiteratureSearchAdapter
from radar.adapters.crossref_search import CrossrefSearchAdapter
from radar.adapters.openalex import OpenAlexSearchAdapter
from radar.adapters.pubmed import PubmedSearchAdapter
from radar.adapters.semantic_scholar import SemanticScholarAdapter
from radar.adapters.unpaywall import UnpaywallAdapter
from radar.config import Settings, get_settings
from radar.db import SessionLocal, session_scope
from radar.llm.base import LLMClient
from radar.llm.factory import build_analysis_llm
from radar.llm.provider import ProviderLLMClient
from radar.llm.text_utils import truncate_for_prompt
from radar.models import (
    AuditEvent,
    Claim,
    ClaimRevision,
    ManuscriptVersion,
    ModelRun,
    ResearchCase,
    ScanRun,
    Source,
    SourceSnapshot,
)
from radar.schemas import (
    DeepResearchBrief,
    DeepResearchPlan,
    DeepResearchSection,
    DeepResearchSourceSummary,
    DeepResearchSubQuery,
    SourceRecord,
    WatchQuery,
)
from radar.services.dedup import SourceDeduplicator
from radar.services.weekly_radar_service import ScanCancelled

PROMPT_DIR = Path(__file__).parents[1] / "llm" / "prompts"

# Keep deep research bounded: a handful of sub-queries and a small final
# source set make the feature useful without turning one click into a
# multi-hour, high-cost run.
_DEFAULT_MAX_SUBQUERIES = 5
_DEFAULT_MAX_SOURCES = 12
_MAX_SUBQUERY_WORDS = 10
_MAX_SUBQUERY_CHARS = 120
_QUERY_NUMBERING_PREFIX = re.compile(r"^(?:\d+\s*[.)、]|-|•|\*)\s*")
_ARXIV_FIELD_PREFIX = re.compile(
    r"\b(?:ti|au|abs|co|jr|cat|rn|id|all):", re.IGNORECASE
)
_ARXIV_BOOLEAN_TOKENS = {"AND", "OR", "ANDNOT"}
_SOURCE_SUMMARY_CHARS = 12_000
_SYNTHESIS_SOURCE_SUMMARY_CHARS = 3_000


def _prompt(name: str, payload: dict) -> str:
    instructions = (PROMPT_DIR / name).read_text(encoding="utf-8").strip()
    return f"{instructions}\n\nINPUT JSON:\n{json.dumps(payload, ensure_ascii=False, indent=2)}"


def _sanitize_search_query(raw_query: str) -> str:
    """Reduce one LLM-suggested line to a safe plain-keyword query."""
    text = raw_query.strip().splitlines()[0] if raw_query.strip() else ""
    text = _QUERY_NUMBERING_PREFIX.sub("", text)
    text = re.sub(r"[*_`\"'“”‘’()]", " ", text)
    text = _ARXIV_FIELD_PREFIX.sub(" ", text)
    words = [word for word in text.split() if word not in _ARXIV_BOOLEAN_TOKENS]
    text = " ".join(words[:_MAX_SUBQUERY_WORDS])
    return text[:_MAX_SUBQUERY_CHARS].strip()


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _normalize_title(value: str) -> str:
    return " ".join(
        "".join(character.lower() if character.isalnum() else " " for character in value).split()
    )


class DeepResearchService:
    """Run one bounded deep-research pass over academic literature."""

    def __init__(
        self,
        session_factory: sessionmaker[Session] = SessionLocal,
        *,
        search_adapters: list | None = None,
        llm_client: LLMClient | None = None,
        settings: Settings | None = None,
        unpaywall_adapter: UnpaywallAdapter | None = None,
        arxiv_adapter: ArxivSearchAdapter | None = None,
    ):
        self.session_factory = session_factory
        self.settings = settings or get_settings()
        if search_adapters is not None:
            adapters = list(search_adapters)
        else:
            adapters = [
                ArxivSearchAdapter(self.settings.data_dir / "cache" / "arxiv"),
                OpenAlexSearchAdapter(),
                SemanticScholarAdapter(
                    api_key=self.settings.semantic_scholar_api_key
                ),
                PubmedSearchAdapter(email=self.settings.crossref_mailto),
                CrossrefSearchAdapter(mailto=self.settings.crossref_mailto),
                ChineseLiteratureSearchAdapter(mailto=self.settings.crossref_mailto),
            ]
        self.search_adapters = adapters
        self.llm_client = (
            llm_client
            or build_analysis_llm(self.settings)
            or ProviderLLMClient(self.settings)
        )
        self.unpaywall = unpaywall_adapter or UnpaywallAdapter(
            email=self.settings.crossref_mailto
        )
        self.arxiv = arxiv_adapter or ArxivSearchAdapter(
            self.settings.data_dir / "cache" / "arxiv"
        )

    # ------------------------------------------------------------------
    # Public entry point (compatible with the scan-runner worker contract)
    # ------------------------------------------------------------------

    def run(
        self,
        case_id: str,
        *,
        question: str = "",
        depth: int = 1,
        max_sources: int = _DEFAULT_MAX_SOURCES,
        max_subqueries: int = _DEFAULT_MAX_SUBQUERIES,
        progress_callback=None,
        scan_id: str | None = None,
        cancel_check=None,
    ) -> str:
        """Run a deep-research pass and store the brief in the ScanRun stats.

        ``scan_id`` may be a pre-created ScanRun row (the scan runner does
        this) or None, in which case the service creates its own row.
        """
        max_sources = max(1, min(int(max_sources), 30))
        max_subqueries = max(1, min(int(max_subqueries), 8))
        depth = max(1, min(int(depth), 3))

        case, manuscript, claims = self._load_case(case_id)
        if manuscript is None:
            raise ValueError("manuscript_missing")
        research_question = (question or case.research_question or "").strip()
        if not research_question:
            raise ValueError("research_question_required")

        scan_id = self._start_scan(
            case_id,
            {
                "question": research_question,
                "depth": depth,
                "max_sources": max_sources,
                "max_subqueries": max_subqueries,
            },
            scan_id=scan_id,
        )

        warnings: list[str] = []
        try:
            self._emit_progress(progress_callback, 0.03, "正在把研究问题拆解为子查询…")
            plan = self._plan(research_question, claims, max_subqueries=max_subqueries)
            if not plan.sub_queries:
                warnings.append("deep_research_plan_fallback")
            sub_queries = [item.query for item in plan.sub_queries if item.query.strip()]
            if not sub_queries:
                sub_queries = self._fallback_queries(research_question, claims)[:max_subqueries]
            self._emit_progress(
                progress_callback,
                0.08,
                f"规划完成：{len(sub_queries)} 条子查询",
            )

            records, source_failures = self._search_all(
                case_id,
                sub_queries,
                max_sources=max_sources,
                progress_callback=progress_callback,
                cancel_check=cancel_check,
            )
            if not records:
                raise RuntimeError("deep_research_no_results")
            if source_failures:
                warnings.extend(
                    f"{name}: {error}" for name, error in source_failures.items()
                )

            # Depth >= 2: expand with follow-up queries based on the first
            # round's top papers (gpt-researcher style recursive expansion).
            if depth >= 2:
                self._emit_progress(progress_callback, 0.38, "根据首批结果扩展追问子查询…")
                followup = self._followup_queries(
                    research_question,
                    records,
                    sub_queries,
                    max_subqueries=max_subqueries,
                )
                if followup:
                    extra_records, extra_failures = self._search_all(
                        case_id,
                        followup,
                        max_sources=max_sources,
                        progress_callback=progress_callback,
                        cancel_check=cancel_check,
                    )
                    merged = SourceDeduplicator()
                    for record in [*records, *extra_records]:
                        merged.add(record)
                    records = sorted(
                        merged.values(),
                        key=lambda item: _parse_datetime(item.published_at)
                        or datetime.min.replace(tzinfo=timezone.utc),
                        reverse=True,
                    )[:max_sources]
                    sub_queries = list(dict.fromkeys([*sub_queries, *followup]))
                    source_failures.update(extra_failures)
                    warnings.extend(
                        f"{name}: {error}"
                        for name, error in extra_failures.items()
                    )

            self._emit_progress(progress_callback, 0.45, "正在去重并保存候选论文…")
            snapshot_ids = self._store_records(records)
            # Keep only as many as requested; the store order follows the
            # records order (newest first after _search_all).
            snapshot_ids = snapshot_ids[:max_sources]
            if not snapshot_ids:
                raise RuntimeError("deep_research_no_sources_stored")

            self._emit_progress(progress_callback, 0.52, "正在压缩单篇论文要点…")
            summaries = self._summarize_sources(
                case_id,
                scan_id,
                snapshot_ids,
                research_question,
                progress_callback=progress_callback,
                cancel_check=cancel_check,
            )

            self._emit_progress(progress_callback, 0.82, "正在撰写深度调研简报…")
            brief = self._synthesize(
                case_id,
                scan_id,
                research_question,
                plan.title,
                sub_queries,
                summaries,
            )
            brief.warnings = list(dict.fromkeys([*brief.warnings, *warnings]))

            self._finish_scan(
                scan_id,
                status="completed",
                stats={
                    "mode": "deep_research",
                    "question": research_question,
                    "depth": depth,
                    "sub_queries": sub_queries,
                    "scanned_papers": len(records),
                    "stored_sources": len(snapshot_ids),
                    "source_failures": source_failures,
                    "research_brief": brief.model_dump(),
                },
            )
            self._emit_progress(progress_callback, 1.0, "深度调研完成。")
            return scan_id
        except ScanCancelled:
            self._finish_scan(
                scan_id,
                status="cancelled",
                stats={"mode": "deep_research", "question": research_question},
                error_message="Deep research cancelled by user.",
            )
            self._emit_progress(progress_callback, 0.0, "深度调研已取消。")
            return scan_id
        except Exception as exc:
            self._finish_scan(
                scan_id,
                status="failed",
                stats={"mode": "deep_research", "question": research_question},
                error_message=str(exc),
            )
            raise

    # ------------------------------------------------------------------
    # Case / scan helpers
    # ------------------------------------------------------------------

    def _load_case(
        self, case_id: str
    ) -> tuple[ResearchCase, ManuscriptVersion | None, list[ClaimRevision]]:
        with session_scope(self.session_factory) as session:
            research_case = session.get(ResearchCase, case_id)
            if research_case is None:
                raise LookupError(f"case not found: {case_id}")
            manuscript = session.scalar(
                select(ManuscriptVersion).where(
                    ManuscriptVersion.case_id == case_id,
                    ManuscriptVersion.is_current.is_(True),
                )
            )
            claims = list(
                session.scalars(
                    select(ClaimRevision)
                    .join(Claim, Claim.id == ClaimRevision.claim_id)
                    .where(
                        Claim.case_id == case_id,
                        ClaimRevision.review_state == "confirmed",
                    )
                )
            )
            for item in claims:
                session.expunge(item)
            if manuscript is not None:
                session.expunge(manuscript)
            session.expunge(research_case)
            return research_case, manuscript, claims

    def _start_scan(
        self, case_id: str, query_payload: dict, *, scan_id: str | None = None
    ) -> str:
        with session_scope(self.session_factory) as session:
            existing = session.get(ScanRun, scan_id) if scan_id else None
            if existing is not None:
                existing.mode = "deep_research"
                if existing.status == "cancel_requested":
                    existing.status = "cancelled"
                    existing.finished_at = datetime.now(timezone.utc)
                    existing.error_message = "Deep research cancelled before it started."
                else:
                    existing.status = "running"
                    existing.finished_at = None
                existing.started_at = datetime.now(timezone.utc)
                existing.query_json = query_payload
                return existing.id
            scan_id = scan_id or str(uuid4())
            session.add(
                ScanRun(
                    id=scan_id,
                    case_id=case_id,
                    mode="deep_research",
                    status="running",
                    started_at=datetime.now(timezone.utc),
                    query_json=query_payload,
                    stats_json={},
                )
            )
            return scan_id

    def _finish_scan(
        self,
        scan_id: str,
        *,
        status: str,
        stats: dict,
        error_message: str | None = None,
    ) -> None:
        with session_scope(self.session_factory) as session:
            scan = session.get(ScanRun, scan_id)
            if scan is None:
                return
            scan.status = status
            scan.finished_at = datetime.now(timezone.utc)
            scan.error_message = error_message
            merged = {**(scan.stats_json or {}), **stats}
            merged["progress"] = {
                "value": 1.0 if status == "completed" else 0.0,
                "message": "深度调研完成。" if status == "completed" else f"深度调研失败：{error_message}",
            }
            scan.stats_json = merged

    @staticmethod
    def _emit_progress(callback, value: float, message: str) -> None:
        if callback is None:
            return
        try:
            callback(max(0.0, min(float(value), 1.0)), message)
        except Exception:
            pass

    @staticmethod
    def _raise_if_cancelled(cancel_check) -> None:
        if cancel_check is not None and cancel_check():
            raise ScanCancelled("deep research cancelled by user")

    # ------------------------------------------------------------------
    # Planning
    # ------------------------------------------------------------------

    def _plan(
        self, question: str, claims: list[ClaimRevision], *, max_subqueries: int
    ) -> DeepResearchPlan:
        statements = [
            revision.statement.strip()
            for revision in claims
            if revision.statement.strip()
        ]
        try:
            output = self.llm_client.generate_structured(
                stage="deep_research_plan",
                prompt=_prompt(
                    "deep_research_plan.txt",
                    {
                        "research_question": question,
                        "confirmed_claims": statements[:12],
                        "max_subqueries": max_subqueries,
                    },
                ),
                response_model=DeepResearchPlan,
            )
            cleaned = []
            for item in output.sub_queries:
                query = _sanitize_search_query(item.query)
                if query and query.lower() not in {q.lower() for q in cleaned}:
                    cleaned.append(query)
                if len(cleaned) >= max_subqueries:
                    break
            return DeepResearchPlan(
                title=output.title,
                sub_queries=[
                    DeepResearchSubQuery(query=q, rationale="", focus="")
                    for q in cleaned
                ],
            )
        except Exception:
            return DeepResearchPlan(title="", sub_queries=[])

    @staticmethod
    def _fallback_queries(question: str, claims: list[ClaimRevision]) -> list[str]:
        candidates = [question]
        candidates.extend(
            revision.statement.strip()
            for revision in claims
            if revision.statement.strip()
        )
        result: list[str] = []
        for candidate in candidates:
            cleaned = _sanitize_search_query(candidate)
            if cleaned and cleaned.lower() not in {item.lower() for item in result}:
                result.append(cleaned)
        return result

    def _followup_queries(
        self,
        question: str,
        records: list[SourceRecord],
        current_queries: list[str],
        *,
        max_subqueries: int,
    ) -> list[str]:
        """Generate follow-up sub-queries from the first round's top papers."""
        if not records:
            return []
        top = [
            {
                "title": record.title,
                "abstract": (record.abstract or "")[:1200],
                "venue": record.venue or "",
                "year": (record.published_at or "")[:4],
            }
            for record in records[:6]
        ]
        try:
            output = self.llm_client.generate_structured(
                stage="deep_research_followup",
                prompt=_prompt(
                    "deep_research_followup.txt",
                    {
                        "research_question": question,
                        "existing_sub_queries": current_queries,
                        "initial_results": top,
                        "max_subqueries": max_subqueries,
                    },
                ),
                response_model=DeepResearchPlan,
            )
        except Exception:
            return []
        existing = {query.lower() for query in current_queries}
        result: list[str] = []
        for item in output.sub_queries:
            query = _sanitize_search_query(item.query)
            if query and query.lower() not in existing and query.lower() not in {q.lower() for q in result}:
                result.append(query)
            if len(result) >= max_subqueries:
                break
        return result

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def _apply_scan_filters(
        self, case_id: str, records: list[SourceRecord]
    ) -> list[SourceRecord]:
        """Apply persisted CCF rank / arXiv category filters."""
        with session_scope(self.session_factory) as session:
            case = session.get(ResearchCase, case_id)
            filters = (
                ((case.settings_json or {}).get("scan_filters") or {})
                if case is not None
                else {}
            )
        ccf_rank = filters.get("ccf_rank") or None
        categories = filters.get("arxiv_categories") or []
        if ccf_rank:
            records = [record for record in records if record.ccf_rank == ccf_rank]
        if categories:
            allowed = set(categories)
            records = [
                record
                for record in records
                if record.arxiv_primary_category
                and record.arxiv_primary_category in allowed
            ]
        return records

    def _search_all(
        self,
        case_id: str,
        queries: list[str],
        *,
        max_sources: int,
        progress_callback=None,
        cancel_check=None,
    ) -> tuple[list[SourceRecord], dict[str, str]]:
        dedup = SourceDeduplicator()
        source_failures: dict[str, str] = {}
        per_query = max(5, min(20, max_sources))
        total = len(queries)
        for index, query in enumerate(queries):
            self._raise_if_cancelled(cancel_check)
            for adapter in self.search_adapters:
                source_name = getattr(adapter, "source_kind", adapter.__class__.__name__)
                try:
                    results = adapter.search(
                        case_id,
                        WatchQuery(query=query, max_results=per_query),
                    )
                except Exception as exc:
                    source_failures[source_name] = str(exc)
                    continue
                for record in results:
                    dedup.add(record)
            self._emit_progress(
                progress_callback,
                0.12 + (0.30 * (index + 1) / max(total, 1)),
                f"已搜索 {index + 1}/{total} 条子查询",
            )
        filtered_records = self._apply_scan_filters(
            case_id, dedup.values()
        )
        records = sorted(
            filtered_records,
            key=lambda item: _parse_datetime(item.published_at)
            or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        return records[:max_sources], source_failures

    @staticmethod
    def _merge_deduped(
        records_by_key: dict[str, SourceRecord], record: SourceRecord
    ) -> None:
        key = record.external_id or ""
        if not key and record.doi:
            key = f"doi:{record.doi.lower()}"
        if not key:
            key = f"title:{_normalize_title(record.title)}"
        existing = records_by_key.get(key)
        if existing is None:
            records_by_key[key] = record
            return
        # Prefer the record with a DOI / richer abstract when the same paper
        # arrives from multiple sources.
        if record.doi and not existing.doi:
            records_by_key[key] = record
        elif record.abstract and len(record.abstract) > len(existing.abstract or ""):
            records_by_key[key] = record

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _store_records(self, records: list[SourceRecord]) -> list[str]:
        """Persist external records as Source/SourceSnapshot rows.

        This mirrors WeeklyRadarService._store_records so deep-research papers
        appear in the same source store and can be reused by later scans.
        """
        snapshot_ids: list[str] = []
        with session_scope(self.session_factory) as session:
            for record in records:
                content = (record.abstract or "").strip()
                if not content:
                    continue
                source_kind = record.source_kind or record.external_id.split(":", 1)[0]
                source = session.scalar(
                    select(Source).where(Source.external_id == record.external_id)
                )
                if source is None:
                    source = Source(
                        id=str(uuid4()),
                        external_id=record.external_id,
                        source_kind=source_kind,
                        title=record.title,
                        authors_json=record.authors,
                        published_at=_parse_datetime(record.published_at),
                        url=record.url,
                        doi=record.doi,
                        arxiv_id=record.arxiv_id,
                        license=record.license,
                        venue=record.venue,
                        publication_type=record.publication_type,
                        pdf_url=record.pdf_url,
                        cited_by_count=record.cited_by_count,
                        integrity_state="normal",
                        arxiv_primary_category=record.arxiv_primary_category,
                        fields_of_study_json=record.fields_of_study,
                        ccf_rank=record.ccf_rank,
                    )
                    session.add(source)
                    session.flush()
                else:
                    source.source_kind = source_kind or source.source_kind
                    source.title = record.title or source.title
                    source.authors_json = record.authors or source.authors_json
                    source.published_at = _parse_datetime(record.published_at) or source.published_at
                    source.url = record.url or source.url
                    source.doi = record.doi or source.doi
                    source.arxiv_id = record.arxiv_id or source.arxiv_id
                    source.license = record.license or source.license
                    source.venue = record.venue or source.venue
                    source.publication_type = record.publication_type or source.publication_type
                    source.pdf_url = record.pdf_url or source.pdf_url
                    if record.cited_by_count is not None:
                        source.cited_by_count = record.cited_by_count
                    if record.arxiv_primary_category:
                        source.arxiv_primary_category = record.arxiv_primary_category
                    if record.fields_of_study:
                        source.fields_of_study_json = list(
                            dict.fromkeys([*source.fields_of_study_json, *record.fields_of_study])
                        )
                    if record.ccf_rank:
                        source.ccf_rank = record.ccf_rank
                content_hash = hashlib.sha256(content.encode()).hexdigest()
                snapshot = session.scalar(
                    select(SourceSnapshot).where(
                        SourceSnapshot.source_id == source.id,
                        SourceSnapshot.content_hash == content_hash,
                    )
                )
                if snapshot is None:
                    snapshot = SourceSnapshot(
                        id=str(uuid4()),
                        source_id=source.id,
                        version_label=record.published_at or "deep-research",
                        title=record.title,
                        abstract=record.abstract,
                        content_text=content,
                        content_hash=content_hash,
                        event_time=_parse_datetime(record.published_at),
                        observed_at=datetime.now(timezone.utc),
                    )
                    session.add(snapshot)
                    session.flush()
                snapshot_ids.append(snapshot.id)
        return snapshot_ids

    def _snapshot_text(self, snapshot_id: str) -> tuple[str, str, str] | None:
        with session_scope(self.session_factory) as session:
            snapshot = session.get(SourceSnapshot, snapshot_id)
            if snapshot is None:
                return None
            return snapshot.title, snapshot.abstract, snapshot.content_text

    # ------------------------------------------------------------------
    # Source compression
    # ------------------------------------------------------------------

    def _summarize_sources(
        self,
        case_id: str,
        scan_id: str,
        snapshot_ids: list[str],
        question: str,
        *,
        progress_callback=None,
        cancel_check=None,
    ) -> list[DeepResearchSourceSummary]:
        summaries: list[DeepResearchSourceSummary] = []
        total = len(snapshot_ids)
        for index, snapshot_id in enumerate(snapshot_ids, start=1):
            self._raise_if_cancelled(cancel_check)
            text_tuple = self._snapshot_text(snapshot_id)
            if text_tuple is None:
                continue
            title, abstract, content_text = text_tuple
            source_text = content_text or abstract
            source_text = truncate_for_prompt(
                source_text,
                max_chars=_SOURCE_SUMMARY_CHARS,
                purpose="deep research source summary",
            )
            summary = self._summarize_one(case_id, scan_id, snapshot_id, question, title, source_text)
            if summary is None:
                summary = DeepResearchSourceSummary(
                    source_id=snapshot_id,
                    title=title,
                    authors=[],
                    year=None,
                    venue=None,
                    url="",
                    relevance="",
                    key_findings=[(abstract or content_text or "")[:400]],
                )
            summaries.append(summary)
            self._emit_progress(
                progress_callback,
                0.52 + (0.28 * index / max(total, 1)),
                f"已压缩 {index}/{total} 篇论文要点",
            )
        return summaries

    def _summarize_one(
        self,
        case_id: str,
        scan_id: str,
        snapshot_id: str,
        question: str,
        title: str,
        source_text: str,
    ) -> DeepResearchSourceSummary | None:
        try:
            output = self.llm_client.generate_structured(
                stage="deep_research_source_summary",
                prompt=_prompt(
                    "deep_research_source_summary.txt",
                    {
                        "research_question": question,
                        "paper_title": title,
                        "paper_text": source_text,
                    },
                ),
                response_model=DeepResearchSourceSummary,
            )
        except Exception:
            return None
        output.source_id = snapshot_id
        self._record_model_run(
            case_id,
            scan_id,
            stage="deep_research_source_summary",
            prompt=source_text[:2000],
            output=output.model_dump(),
        )
        return output

    # ------------------------------------------------------------------
    # Synthesis
    # ------------------------------------------------------------------

    def _synthesize(
        self,
        case_id: str,
        scan_id: str,
        question: str,
        plan_title: str,
        sub_queries: list[str],
        summaries: list[DeepResearchSourceSummary],
    ) -> DeepResearchBrief:
        # Keep the synthesis prompt bounded: pass compact per-source cards.
        compact = [
            {
                "source_id": item.source_id,
                "title": item.title,
                "authors": item.authors,
                "year": item.year,
                "venue": item.venue,
                "url": item.url,
                "doi": item.doi,
                "relevance": item.relevance,
                "key_findings": item.key_findings[:6],
                "limitations": item.limitations[:4],
                "contradictions": item.contradictions[:4],
            }
            for item in summaries
        ]
        try:
            output = self.llm_client.generate_structured(
                stage="deep_research_synthesis",
                prompt=_prompt(
                    "deep_research_synthesis.txt",
                    {
                        "research_question": question,
                        "plan_title": plan_title,
                        "sub_queries": sub_queries,
                        "sources": compact,
                    },
                ),
                response_model=DeepResearchBrief,
            )
            # Never let the model invent source ids; only real stored ids are kept.
            valid_ids = {item.source_id for item in summaries}
            output.sources = [
                item for item in output.sources if item.source_id in valid_ids
            ]
            for section in output.sections:
                section.source_ids = [
                    sid for sid in section.source_ids if sid in valid_ids
                ]
            self._record_model_run(
                case_id,
                scan_id,
                stage="deep_research_synthesis",
                prompt=json.dumps(
                    {"question": question, "sources_count": len(compact)},
                    ensure_ascii=False,
                ),
                output=output.model_dump(),
            )
            return output
        except Exception:
            # Deterministic fallback: a readable brief assembled from the
            # compressed source cards, so an LLM outage never destroys a run.
            fallback = DeepResearchBrief(
                title=plan_title or question,
                executive_summary=(
                    f"针对「{question}」的深度调研共收录 {len(summaries)} 篇论文。"
                    "以下为各来源要点与后续建议。"
                ),
                sections=[
                    DeepResearchSection(
                        heading="主要发现",
                        findings=[
                            finding
                            for item in summaries
                            for finding in item.key_findings[:2]
                        ][:12],
                        source_ids=[item.source_id for item in summaries],
                    )
                ],
                key_insights=[
                    f"{item.title}: {item.relevance}" for item in summaries if item.relevance
                ],
                contradictions=[
                    finding
                    for item in summaries
                    for finding in item.contradictions
                ][:8],
                research_gaps=[
                    f"{item.title}: {limitation}"
                    for item in summaries
                    for limitation in item.limitations[:2]
                ][:8],
                recommended_next_steps=[
                    "人工阅读以上高相关论文的全文，确认与你论文 Claim 的具体关系。",
                    "对 contradictions 中的论文运行一次 Claim 影响评估（文献雷达扫描）。",
                    "把关键来源加入竞品监控或引用候选。",
                ],
                sources=summaries,
                warnings=["deep_research_synthesis_fallback"],
            )
            return fallback

    # ------------------------------------------------------------------
    # Audit trail
    # ------------------------------------------------------------------

    def _record_model_run(
        self,
        case_id: str,
        scan_id: str,
        *,
        stage: str,
        prompt: str,
        output: dict,
    ) -> None:
        receipt = getattr(self.llm_client, "last_receipt", {}) or {}
        usage = receipt.get("usage") or {}
        try:
            with session_scope(self.session_factory) as session:
                session.add(
                    ModelRun(
                        id=str(uuid4()),
                        stage=stage,
                        case_id=case_id,
                        scan_run_id=scan_id,
                        provider=getattr(self.llm_client, "provider_name", "unknown"),
                        model=getattr(self.llm_client, "model_name", "unknown"),
                        prompt_hash=hashlib.sha256(prompt.encode()).hexdigest(),
                        schema_version=f"{stage}.v1",
                        input_refs_json=[],
                        raw_response=receipt.get("raw_response", ""),
                        parsed_output_json=output,
                        validation_json={},
                        input_tokens=int(usage.get("prompt_tokens", 0)),
                        output_tokens=int(usage.get("completion_tokens", 0)),
                        latency_ms=int(receipt.get("latency_ms", 0)),
                    )
                )
                session.add(
                    AuditEvent(
                        id=str(uuid4()),
                        case_id=case_id,
                        event_type=f"{stage}_executed",
                        object_type="DeepResearch",
                        object_id=scan_id,
                        payload_json={"stage": stage},
                        actor_type="model",
                        actor_id="deep_research",
                    )
                )
        except Exception:
            # Audit persistence must never mask a completed research run.
            pass
