"""Claim candidate extraction, atomic decomposition, semantic verification, and human gate G0."""

import difflib
import hashlib
import json
import logging
import re
import time
from pathlib import Path
from uuid import uuid4

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, sessionmaker

from radar.config import Settings, estimate_llm_cost_usd, get_settings
from radar.db import SessionLocal, session_scope
from radar.llm.base import LLMClient
from radar.llm.factory import build_analysis_llm
from radar.llm.text_utils import truncate_for_prompt
from radar.models import AuditEvent, Claim, ClaimRevision, G0Feedback, ManuscriptVersion, ModelRun
from radar.schemas import (
    AtomicClaimBatch,
    ClaimCandidateBatch,
    ClaimCandidateOutput,
    ClaimSemanticVerification,
    EmpiricalClaimContract,
)
from radar.services.evidence_service import (
    _normalized_text_with_offsets,
    resolve_exact_quote,
)
from radar.services.trust_service import TrustService


PROMPT_PATH = (
    Path(__file__).parents[1] / "llm" / "prompts" / "claim_candidate_extraction.txt"
)
DEFAULT_FALSIFIABLE_CONDITION = (
    "A matched evaluation does not reproduce the reported result."
)
DEFAULT_CARRY_SIMILARITY = 0.85

logger = logging.getLogger(__name__)

SENTENCE_SPAN = re.compile(
    r"(?:^|(?<=[.!?])\s+)(.{45,900}?[.!?])"
    r"(?=\s+(?:[A-Z]|\d+(?:\.\d+)*\s+[A-Z]|[•])|\s*$)",
    re.DOTALL,
)
OUTCOME_CUES = re.compile(
    r"\b(improv\w*|outperform\w*|surpass\w*|reduc\w*|lower\w*|higher|superior|"
    r"maintain\w*|reach\w*|achiev\w*|decreas\w*|increas\w*|"
    r"fragile|sensitive\s+to|regression|underperform\w*)\b",
    re.IGNORECASE,
)
EVIDENCE_FRAMES = re.compile(
    r"\b(results?|findings?|experiments?|evaluation|analysis)\b.{0,160}"
    r"\b(show\w*|reveal\w*|indicate\w*|demonstrate\w*|find\w*|achieve\w*)\b",
    re.IGNORECASE | re.DOTALL,
)
EMPIRICAL_TERMS = re.compile(
    r"\b(score|accuracy|exact[ -]match|f1|precision|recall|latency|throughput|rate|"
    r"robustness|queries|questions|documents|benchmark|dataset|domain|model)\b",
    re.IGNORECASE,
)
DATASET_CLAIM = re.compile(
    r"\b(?:construct|create|compris\w*|span\w*)\b.{0,180}\b\d[\d,]*\b.{0,120}"
    r"\b(?:questions|queries|documents|examples|instances|domains)\b",
    re.IGNORECASE | re.DOTALL,
)
LOW_QUALITY = re.compile(
    r"(^|\s)(?:Table|Figure)\s+\d|\bRelated Work\b|\bet al\.\b|"
    r"importance of (?:evaluating|improving)|^\s*\[\d+\]|^Appendix\b|"
    r"^It includes\b|\[\d+\].{0,100}\b(?:analy[sz]ed|found|reported)\b|"
    r"\bwe define\b|\bpipeline\b.{0,100}\bensures\b|"
    r"\b(?:E-Agent|Model|Assistant)\s+Response:\s|\bUser:\s|\bQuestion:\s",
    re.IGNORECASE,
)


def default_llm_client(settings: Settings) -> LLMClient | None:
    """Pick the configured analysis model via the shared factory.

    Returns None when no LLM is configured so callers can fall back to the
    deterministic heuristic without attempting a network call.
    """

    return build_analysis_llm(settings)


def _readable_statement(quote: str) -> str:
    """Normalize layout artifacts for display while preserving the exact quote separately."""
    statement = re.sub(r"(?<=[a-z])-\s*\n\s*(?=[a-z])", "", quote)
    statement = re.sub(r"\n\d+\.\s*$", "", statement)
    statement = re.sub(r"\s+", " ", statement).strip()
    return re.sub(r"-\s+(?=[A-Z])", "-", statement)


def _candidate_score(quote: str) -> int:
    if "\x00" in quote or len(quote) > 900:
        return -100
    scoring_text = _readable_statement(quote)
    score = 0
    outcome = OUTCOME_CUES.search(scoring_text)
    if outcome:
        score += 3
        if re.search(r"\b(RAG systems?|models?|scores?|queries|performance)\b", scoring_text, re.I):
            score += 1
    if EVIDENCE_FRAMES.search(scoring_text):
        score += 3
    if DATASET_CLAIM.search(scoring_text):
        score += 4
    if EMPIRICAL_TERMS.search(scoring_text):
        score += 1
    if re.search(r"\d", scoring_text):
        score += 1
    if LOW_QUALITY.search(scoring_text):
        score -= 5
    if re.search(r"\b(?:should|must)\b", scoring_text, re.IGNORECASE):
        score -= 3
    if re.match(r"^(?:This|It|They|Surprisingly,?\s+it)\b", scoring_text, re.IGNORECASE):
        score -= 2
    return score


def _rank_candidate_spans(content: str, limit: int = 20) -> list[tuple[int, int, str, int]]:
    """Find cross-line empirical sentences and rank them without changing source spans."""
    ranked: list[tuple[int, int, str, int]] = []
    references = re.search(r"\nReferences\s*\n\s*\[\d+\]", content, re.IGNORECASE)
    searchable_content = content[: references.start()] if references else content
    for match in SENTENCE_SPAN.finditer(searchable_content):
        quote = match.group(1).strip()
        score = _candidate_score(quote)
        if score < 5:
            continue
        start = content.find(quote, match.start(1), match.end(1) + 1)
        if start < 0:
            continue
        ranked.append((start, start + len(quote), quote, score))
    ranked.sort(key=lambda item: (-item[3], item[0]))
    selected = ranked[:limit]
    selected.sort(key=lambda item: item[0])
    return selected


def _overlaps(ranges: list[tuple[int, int]], start: int, end: int) -> bool:
    return any(start < taken_end and end > taken_start for taken_start, taken_end in ranges)


SEGMENT_END = re.compile(r"[.!?]+\s+")


def _locate_carried_quote(
    quote: str, content: str, threshold: float
) -> tuple[int, int, float] | None:
    """Locate a previous claim quote exactly, else by normalized fuzzy match.

    Returns ``(start, end, similarity)`` on the original content, or None when
    the best fuzzy segment scores below ``threshold``. The fuzzy path compares
    whitespace/hyphenation-normalized text so reworded sentences still carry,
    while the returned span always comes from the original content and is
    therefore exact-span verifiable downstream.
    """

    index = content.find(quote)
    if index >= 0:
        return index, index + len(quote), 1.0
    normalized_content, offsets = _normalized_text_with_offsets(content)
    normalized_quote, _ = _normalized_text_with_offsets(quote)
    if not normalized_quote or not offsets:
        return None
    # Compare against one-to-three consecutive sentence-like segments so a
    # reworded (possibly longer or shorter) claim still lines up, while a
    # sliding fixed-size window cannot.
    bounds: list[tuple[int, int]] = []
    position = 0
    for match in SEGMENT_END.finditer(normalized_content):
        bounds.append((position, match.end()))
        position = match.end()
    bounds.append((position, len(normalized_content)))
    best: tuple[float, int, int] | None = None
    for first in range(len(bounds)):
        for span in (1, 2, 3):
            if first + span > len(bounds):
                break
            start = bounds[first][0]
            end = bounds[first + span - 1][1]
            window = normalized_content[start:end]
            matcher = difflib.SequenceMatcher(None, normalized_quote, window)
            if matcher.real_quick_ratio() < threshold or matcher.quick_ratio() < threshold:
                continue
            # Trim leading/trailing non-matching characters so the carried
            # span covers exactly the reworded claim, not segment padding.
            blocks = [block for block in matcher.get_matching_blocks() if block.size]
            if not blocks:
                continue
            trimmed_start = start + blocks[0].b
            trimmed_end = start + blocks[-1].b + blocks[-1].size
            ratio = difflib.SequenceMatcher(
                None, normalized_quote, normalized_content[trimmed_start:trimmed_end]
            ).ratio()
            if ratio >= threshold and (best is None or ratio > best[0]):
                best = (ratio, trimmed_start, trimmed_end)
    if best is None:
        return None
    ratio, start, end = best
    return offsets[start], offsets[end - 1] + 1, ratio


class ClaimService:
    def __init__(
        self,
        session_factory: sessionmaker[Session] = SessionLocal,
        *,
        llm_client: LLMClient | None = None,
        settings: Settings | None = None,
        carry_similarity_threshold: float = DEFAULT_CARRY_SIMILARITY,
    ):
        self.session_factory = session_factory
        self.settings = settings or get_settings()
        self.llm_client = llm_client
        self.carry_similarity_threshold = carry_similarity_threshold
        self.trust = TrustService()

    def _resolve_llm_client(self) -> LLMClient | None:
        if self.llm_client is not None:
            return self.llm_client
        return default_llm_client(self.settings)

    def extract_candidates(self, manuscript_version_id: str) -> list[ClaimRevision]:
        with session_scope(self.session_factory) as session:
            manuscript = session.get(ManuscriptVersion, manuscript_version_id)
            if manuscript is None:
                raise LookupError(f"manuscript not found: {manuscript_version_id}")
            existing = list(
                session.scalars(
                    select(ClaimRevision).where(
                        ClaimRevision.manuscript_version_id == manuscript_version_id
                    )
                )
            )
            # When a manuscript was uploaded before any LLM was configured the
            # extraction silently degraded to the deterministic rules, and the
            # "already extracted" shortcut below would keep those low-quality
            # candidates forever — the user configures the LLM afterwards and
            # nothing re-extracts. Re-run the LLM extraction over the earlier
            # rule candidates (and only those): confirmed human-gated claims
            # are never touched, replaced or resurfaced.
            rerun_heuristic = self._should_rerun_rule_candidates(session, manuscript, existing)
            if existing and not rerun_heuristic:
                return existing

            next_number = self._next_stable_key_number(session, manuscript.case_id)
            client, batch, prompt, llm_error, latency_ms = self._try_llm_extraction(manuscript)
            if rerun_heuristic and client is not None and batch is None:
                # The LLM is configured now but the structured call failed
                # (e.g. transient provider error). Keep the existing rule
                # candidates so the user is never left with an empty claim
                # list; the extraction can be retried from the UI.
                return existing
            if rerun_heuristic:
                # Drop the superseded rule candidates (never any confirmed
                # ones — the guard above guarantees it) before persisting the
                # LLM-extracted replacements.
                self._delete_rule_candidates(session, manuscript_version_id)
            if batch is not None:
                candidates, dropped_anchors = self._persist_llm_candidates(
                    session, manuscript, batch.candidates,
                    excluded_ranges=[], next_number=next_number,
                )
                self._record_extraction_run(
                    session, manuscript, client=client, prompt=prompt, batch=batch,
                    candidates=candidates, dropped_anchors=dropped_anchors,
                    latency_ms=latency_ms,
                )
            else:
                candidates = self._persist_heuristic_candidates(
                    session, manuscript, excluded_ranges=[], next_number=next_number,
                )
                self._record_extraction_fallback_run(
                    session, manuscript, candidates=candidates, llm_error=llm_error,
                )

            session.add(
                AuditEvent(
                    id=str(uuid4()), case_id=manuscript.case_id,
                    event_type="claim_candidates_extracted", object_type="ManuscriptVersion",
                    object_id=manuscript.id,
                    payload_json={
                        "count": len(candidates),
                        "reran_rule_fallback": rerun_heuristic,
                    },
                    actor_type="system", actor_id="claim_service",
                )
            )
            session.flush()
            for candidate in candidates:
                session.expunge(candidate)
            return candidates

    def _should_rerun_rule_candidates(
        self,
        session: Session,
        manuscript: ManuscriptVersion,
        existing: list[ClaimRevision],
    ) -> bool:
        """True when the existing revisions are unconfirmed rule fallbacks that
        a now-available LLM should replace (see extract_candidates)."""
        if not existing:
            return False
        client = self._resolve_llm_client()
        if client is None:
            return False
        if any(rev.review_state != "candidate" for rev in existing):
            # Any human-confirmed / edited / rejected revision stays untouched.
            return False
        run = session.scalar(
            select(ModelRun)
            .where(
                ModelRun.stage == "claim_extraction",
                ModelRun.case_id == manuscript.case_id,
            )
            .order_by(ModelRun.created_at.desc())
        )
        if run is None:
            return False
        validation = run.validation_json or {}
        if not validation.get("path") == "heuristic":
            return False
        # Only re-run when the fallback run belongs to this manuscript
        # version (input_refs_json stores the manuscript id).
        return manuscript.id in (run.input_refs_json or [])

    def _delete_rule_candidates(self, session: Session, manuscript_version_id: str) -> None:
        """Remove rule-fallback claim candidates before an LLM re-extraction.

        Only candidate-state revisions are deleted; confirmed revisions are
        guarded against by _should_rerun_rule_candidates. Claim rows without
        any remaining revision are removed too so no orphaned claims linger.
        """
        from sqlalchemy import delete

        candidate_rev_ids = list(
            session.scalars(
                select(ClaimRevision.id).where(
                    ClaimRevision.manuscript_version_id == manuscript_version_id,
                    ClaimRevision.review_state == "candidate",
                )
            )
        )
        if not candidate_rev_ids:
            return
        orphaned_claim_ids = list(
            session.scalars(
                select(ClaimRevision.claim_id)
                .where(ClaimRevision.id.in_(candidate_rev_ids))
                .distinct()
            )
        )
        session.execute(
            delete(ClaimRevision).where(ClaimRevision.id.in_(candidate_rev_ids))
        )
        for claim_id in orphaned_claim_ids:
            remaining = session.scalar(
                select(func.count(ClaimRevision.id)).where(
                    ClaimRevision.claim_id == claim_id
                )
            )
            if not remaining:
                session.execute(delete(Claim).where(Claim.id == claim_id))

    def _try_llm_extraction(
        self, manuscript: ManuscriptVersion
    ) -> tuple[LLMClient | None, ClaimCandidateBatch | None, str, str | None, int]:
        """Attempt one structured extraction call; never raise into the caller.

        Returns ``(client, batch, prompt, error, latency_ms)``; ``batch`` is
        None when no LLM is configured or the call failed, in which case
        ``error`` describes why the deterministic fallback was used.
        """

        client = self._resolve_llm_client()
        if client is None:
            return None, None, "", "llm_not_configured", 0
        instructions = PROMPT_PATH.read_text(encoding="utf-8").strip()
        prompt = (
            f"{instructions}\n\nMANUSCRIPT TEXT:\n"
            f"{truncate_for_prompt(manuscript.content_text, purpose='claim candidate extraction')}"
        )
        started = time.perf_counter()
        try:
            batch = client.generate_structured(
                stage="claim_extraction",
                prompt=prompt,
                response_model=ClaimCandidateBatch,
            )
        except Exception as exc:
            logger.warning(
                "claim extraction LLM call failed for manuscript %s, falling back to "
                "deterministic heuristics: %s",
                manuscript.id,
                exc,
            )
            return client, None, prompt, str(exc), int((time.perf_counter() - started) * 1000)
        return client, batch, prompt, None, int((time.perf_counter() - started) * 1000)

    def _persist_llm_candidates(
        self,
        session: Session,
        manuscript: ManuscriptVersion,
        items: list[ClaimCandidateOutput],
        *,
        excluded_ranges: list[tuple[int, int]],
        next_number: int,
    ) -> tuple[list[ClaimRevision], list[str]]:
        """Persist LLM candidates whose source_quote resolves to an exact span.

        Candidates whose anchor cannot be located verbatim (same standard as
        EvidenceService.resolve_exact) are dropped and reported, never stored.
        """

        candidates: list[ClaimRevision] = []
        dropped: list[str] = []
        taken_ranges = list(excluded_ranges)
        for item in items:
            resolved = resolve_exact_quote(item.source_quote, manuscript.content_text)
            if resolved is None:
                dropped.append(item.statement[:120])
                continue
            quote, start, end = resolved
            if _overlaps(taken_ranges, start, end):
                continue
            claim = Claim(
                id=str(uuid4()),
                case_id=manuscript.case_id,
                stable_key=f"C{next_number}",
                lifecycle_state="active",
            )
            next_number += 1
            revision = ClaimRevision(
                id=str(uuid4()),
                claim_id=claim.id,
                manuscript_version_id=manuscript.id,
                revision_no=1,
                statement=item.statement.strip() or _readable_statement(quote),
                claim_type=item.claim_type,
                centrality=item.centrality_suggestion,
                contract_json=item.contract.model_dump(),
                falsifiable_condition=(
                    item.falsifiable_condition.strip() or DEFAULT_FALSIFIABLE_CONDITION
                ),
                source_quote=quote,
                source_locator=f"offset:{start}-{end}",
                review_state="candidate",
            )
            session.add(claim)
            session.flush()
            session.add(revision)
            session.flush()
            taken_ranges.append((start, end))
            candidates.append(revision)
        return candidates, dropped

    def _persist_heuristic_candidates(
        self,
        session: Session,
        manuscript: ManuscriptVersion,
        *,
        excluded_ranges: list[tuple[int, int]],
        next_number: int,
    ) -> list[ClaimRevision]:
        """Regex/cue-based extraction kept as the deterministic fallback path."""

        candidates: list[ClaimRevision] = []
        for start, end, quote, score in _rank_candidate_spans(manuscript.content_text):
            if _overlaps(excluded_ranges, start, end):
                continue
            if self.trust.verify_claim_quote(quote, manuscript.content_text).state == "blocked":
                continue
            claim = Claim(
                id=str(uuid4()),
                case_id=manuscript.case_id,
                stable_key=f"C{next_number}",
                lifecycle_state="active",
            )
            next_number += 1
            revision = ClaimRevision(
                id=str(uuid4()),
                claim_id=claim.id,
                manuscript_version_id=manuscript.id,
                revision_no=1,
                statement=_readable_statement(quote),
                claim_type="empirical_result",
                centrality="core" if score >= 7 else "major",
                contract_json=EmpiricalClaimContract().model_dump(),
                falsifiable_condition=DEFAULT_FALSIFIABLE_CONDITION,
                source_quote=quote,
                source_locator=f"offset:{start}-{end}",
                review_state="candidate",
            )
            session.add(claim)
            session.flush()
            session.add(revision)
            candidates.append(revision)
        return candidates

    def _record_extraction_run(
        self,
        session: Session,
        manuscript: ManuscriptVersion,
        *,
        client: LLMClient,
        prompt: str,
        batch: ClaimCandidateBatch,
        candidates: list[ClaimRevision],
        dropped_anchors: list[str],
        latency_ms: int,
    ) -> None:
        receipt = getattr(client, "last_receipt", {}) or {}
        usage = receipt.get("usage") or {}
        input_tokens = int(usage.get("prompt_tokens", 0))
        output_tokens = int(usage.get("completion_tokens", 0))
        model_name = (
            getattr(client, "model_name", None) or self.settings.llm_model or "unknown"
        )
        session.add(
            ModelRun(
                id=str(uuid4()),
                stage="claim_extraction",
                case_id=manuscript.case_id,
                provider=(
                    getattr(client, "provider_name", None)
                    or self.settings.llm_provider
                    or client.__class__.__name__
                ),
                model=model_name,
                prompt_hash=hashlib.sha256(prompt.encode()).hexdigest(),
                schema_version="ClaimCandidateBatch.v1",
                input_refs_json=[manuscript.id],
                raw_response=receipt.get("raw_response") or batch.model_dump_json(),
                parsed_output_json=batch.model_dump(),
                validation_json={
                    "pydantic": True,
                    "path": "llm",
                    "anchors_resolved": len(candidates),
                    "anchors_dropped": dropped_anchors,
                },
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                estimated_cost=estimate_llm_cost_usd(
                    model_name, input_tokens, output_tokens
                ),
                latency_ms=int(receipt.get("latency_ms", latency_ms)),
            )
        )

    def _record_extraction_fallback_run(
        self,
        session: Session,
        manuscript: ManuscriptVersion,
        *,
        candidates: list[ClaimRevision],
        llm_error: str | None,
    ) -> None:
        session.add(
            ModelRun(
                id=str(uuid4()),
                stage="claim_extraction",
                case_id=manuscript.case_id,
                provider="deterministic_fallback",
                model="empirical-cue-v1",
                prompt_hash=hashlib.sha256(manuscript.content_text.encode()).hexdigest(),
                schema_version="ClaimCandidateOutput.v1",
                input_refs_json=[manuscript.id],
                raw_response="",
                parsed_output_json={"candidate_ids": [item.id for item in candidates]},
                validation_json={"all_quotes_exact": True, "path": "heuristic", "llm_error": llm_error},
                input_tokens=0,
                output_tokens=0,
                estimated_cost=0.0,
                latency_ms=0,
            )
        )

    @staticmethod
    def _next_stable_key_number(session: Session, case_id: str) -> int:
        all_stable_keys = list(
            session.scalars(select(Claim.stable_key).where(Claim.case_id == case_id))
        )
        existing_numbers = [
            int(match.group(1))
            for stable_key in all_stable_keys
            if (match := re.fullmatch(r"C(\d+)", stable_key))
        ]
        return max(existing_numbers, default=0) + 1

    def sync_manuscript_version(self, manuscript_version_id: str) -> dict:
        """Carry exact Claims forward and extract candidates from a new manuscript version.

        Carry-over first tries the exact quote, then a normalized fuzzy match
        (``carry_similarity_threshold``). Previous claims that can no longer be
        located are returned explicitly in ``lost_claims`` instead of being
        silently counted, so the UI can surface them.
        """

        with session_scope(self.session_factory) as session:
            manuscript = session.get(ManuscriptVersion, manuscript_version_id)
            if manuscript is None:
                raise LookupError(f"manuscript not found: {manuscript_version_id}")
            existing_for_version = list(
                session.scalars(
                    select(ClaimRevision).where(
                        ClaimRevision.manuscript_version_id == manuscript_version_id
                    )
                )
            )
            if existing_for_version:
                return {
                    "carried_claims": sum(
                        item.supersedes_id is not None for item in existing_for_version
                    ),
                    "new_candidates": sum(
                        item.supersedes_id is None for item in existing_for_version
                    ),
                    "previous_claims_not_found": 0,
                    "lost_claims": [],
                }

            rows = list(
                session.execute(
                    select(Claim, ClaimRevision)
                    .join(ClaimRevision, ClaimRevision.claim_id == Claim.id)
                    .where(
                        Claim.case_id == manuscript.case_id,
                        ClaimRevision.manuscript_version_id != manuscript.id,
                        ClaimRevision.review_state.in_(["candidate", "confirmed"]),
                    )
                    .order_by(ClaimRevision.revision_no.desc(), ClaimRevision.created_at.desc())
                )
            )
            latest_by_claim: dict[str, tuple[Claim, ClaimRevision]] = {}
            for claim, revision in rows:
                latest_by_claim.setdefault(claim.id, (claim, revision))

            next_number = self._next_stable_key_number(session, manuscript.case_id)
            carried_ranges: list[tuple[int, int]] = []
            carried = 0
            fuzzy_carried = 0
            lost_claims: list[dict[str, str]] = []
            for claim, previous in latest_by_claim.values():
                located = _locate_carried_quote(
                    previous.source_quote,
                    manuscript.content_text,
                    self.carry_similarity_threshold,
                )
                if located is None:
                    lost_claims.append(
                        {
                            "claim_id": claim.id,
                            "stable_key": claim.stable_key,
                            "statement": previous.statement,
                        }
                    )
                    continue
                start, end, similarity = located
                carried_quote = manuscript.content_text[start:end]
                previous_state = previous.review_state
                if previous_state == "confirmed" and similarity < 1.0:
                    # A fuzzy carry can land on materially changed text (e.g.
                    # a reported number was edited in the manuscript): the old
                    # statement and the carried span no longer agree, so the
                    # claim must be re-gated by a human (G0) before it counts
                    # as confirmed again. Only exact matches keep confirmed.
                    previous_state = "candidate"
                previous.review_state = "superseded"
                revision = ClaimRevision(
                    id=str(uuid4()),
                    claim_id=claim.id,
                    manuscript_version_id=manuscript.id,
                    revision_no=previous.revision_no + 1,
                    statement=previous.statement,
                    claim_type=previous.claim_type,
                    centrality=previous.centrality,
                    contract_json=previous.contract_json,
                    falsifiable_condition=previous.falsifiable_condition,
                    source_quote=carried_quote,
                    source_locator=f"offset:{start}-{end}",
                    review_state=previous_state,
                    supersedes_id=previous.id,
                )
                session.add(revision)
                carried_ranges.append((start, end))
                carried += 1
                if similarity < 1.0:
                    fuzzy_carried += 1

            client, batch, prompt, llm_error, latency_ms = self._try_llm_extraction(manuscript)
            dropped_anchors: list[str] = []
            if batch is not None:
                candidates, dropped_anchors = self._persist_llm_candidates(
                    session, manuscript, batch.candidates,
                    excluded_ranges=carried_ranges, next_number=next_number,
                )
            else:
                candidates = self._persist_heuristic_candidates(
                    session, manuscript,
                    excluded_ranges=carried_ranges, next_number=next_number,
                )

            session.add(
                ModelRun(
                    id=str(uuid4()),
                    stage="claim_version_sync",
                    case_id=manuscript.case_id,
                    provider=(
                        (
                            getattr(client, "provider_name", None)
                            or self.settings.llm_provider
                            or client.__class__.__name__
                        )
                        if batch is not None
                        else "deterministic_fallback"
                    ),
                    model=(
                        (
                            getattr(client, "model_name", None)
                            or self.settings.llm_model
                            or "unknown"
                        )
                        if batch is not None
                        else "fuzzy-carry-and-empirical-cue-v2"
                    ),
                    prompt_hash=hashlib.sha256(
                        (prompt or manuscript.content_text).encode()
                    ).hexdigest(),
                    schema_version="ClaimVersionSync.v1",
                    input_refs_json=[manuscript.id],
                    raw_response="",
                    parsed_output_json={
                        "carried_claims": carried,
                        "fuzzy_carried_claims": fuzzy_carried,
                        "new_candidate_ids": [item.id for item in candidates],
                        "previous_claims_not_found": len(lost_claims),
                        "lost_claims": lost_claims,
                    },
                    validation_json={
                        "all_quotes_exact": True,
                        "carry_similarity_threshold": self.carry_similarity_threshold,
                        "candidate_extraction": "llm" if batch is not None else "heuristic",
                        "anchors_dropped": dropped_anchors,
                        "llm_error": llm_error,
                    },
                    input_tokens=0,
                    output_tokens=0,
                    estimated_cost=0.0,
                    latency_ms=0,
                )
            )
            session.add(
                AuditEvent(
                    id=str(uuid4()),
                    case_id=manuscript.case_id,
                    event_type="manuscript_claims_synchronized",
                    object_type="ManuscriptVersion",
                    object_id=manuscript.id,
                    payload_json={
                        "carried_claims": carried,
                        "new_candidates": len(candidates),
                        "previous_claims_not_found": len(lost_claims),
                        "lost_stable_keys": [item["stable_key"] for item in lost_claims],
                    },
                    actor_type="system",
                    actor_id="claim_service",
                )
            )
            return {
                "carried_claims": carried,
                "new_candidates": len(candidates),
                "previous_claims_not_found": len(lost_claims),
                "lost_claims": lost_claims,
            }

    # ---- Atomic claim decomposition ----

    def decompose_to_atomic(
        self, claim_revision_id: str
    ) -> list[dict]:
        """Decompose a complex claim into independently verifiable atomic sub-claims.

        Uses the LLM to break multi-faceted claims into single-assertion atoms,
        each tied to its own source quote span. Falls back to returning the
        original claim as a single atom when the LLM is unavailable.
        """
        with session_scope(self.session_factory) as session:
            revision = session.get(ClaimRevision, claim_revision_id)
            if revision is None:
                raise LookupError(f"claim revision not found: {claim_revision_id}")

            client = self._resolve_llm_client()
            if client is None:
                return [{
                    "statement": revision.statement,
                    "source_quote": revision.source_quote,
                    "source_locator": revision.source_locator,
                    "dependencies": [],
                    "verifiable_independently": True,
                }]

            manuscript = session.get(ManuscriptVersion, revision.manuscript_version_id)
            context = self._decomposition_context(manuscript, revision)

            instructions = (
                "Decompose the supplied claim into atomic, independently verifiable sub-claims. "
                "Each atom must be a single assertion. Preserve the exact source quote for "
                "each atom by copying the relevant portion verbatim from the manuscript text. "
                "Mark dependencies between atoms (e.g., atom B depends on atom A's result). "
                "Return at most 8 atoms."
            )
            prompt = (
                f"{instructions}\n\nCLAIM STATEMENT:\n{revision.statement}\n\n"
                f"SOURCE QUOTE:\n{revision.source_quote}\n\n"
                f"MANUSCRIPT CONTEXT:\n{context}"
            )
            started = time.perf_counter()
            try:
                batch = client.generate_structured(
                    stage="claim_atomic_decomposition",
                    prompt=prompt,
                    response_model=AtomicClaimBatch,
                )
                latency_ms = int((time.perf_counter() - started) * 1000)
            except Exception:
                return [{
                    "statement": revision.statement,
                    "source_quote": revision.source_quote,
                    "source_locator": revision.source_locator,
                    "dependencies": [],
                    "verifiable_independently": True,
                }]

            atoms: list[dict] = []
            for atom in batch.atomic_claims:
                # M5: atom quotes must be exact manuscript spans — an
                # invented atom quote is dropped, never displayed as evidence.
                resolved = (
                    resolve_exact_quote(atom.source_quote, manuscript.content_text)
                    if manuscript is not None
                    else None
                )
                if resolved is None:
                    continue
                quote, start, end = resolved
                atoms.append({
                    "statement": atom.statement,
                    "source_quote": quote,
                    "source_locator": f"offset:{start}-{end}",
                    "dependencies": atom.dependencies,
                    "verifiable_independently": atom.verifiable_independently,
                })
            if not atoms:
                return [{
                    "statement": revision.statement,
                    "source_quote": revision.source_quote,
                    "source_locator": revision.source_locator,
                    "dependencies": [],
                    "verifiable_independently": True,
                }]

            claim = session.get(Claim, revision.claim_id)
            session.add(ModelRun(
                id=str(uuid4()),
                stage="claim_atomic_decomposition",
                case_id=claim.case_id if claim else None,
                provider=getattr(client, "provider_name", None)
                or self.settings.llm_provider
                or client.__class__.__name__,
                model=getattr(client, "model_name", None)
                or self.settings.llm_model
                or "unknown",
                prompt_hash=hashlib.sha256(prompt.encode()).hexdigest(),
                schema_version="AtomicClaimBatch.v1",
                input_refs_json=[revision.id],
                raw_response="",
                parsed_output_json={"atoms": atoms},
                validation_json={"atoms_dropped_unanchored": len(batch.atomic_claims) - len(atoms)},
                input_tokens=0,
                output_tokens=0,
                estimated_cost=0.0,
                latency_ms=latency_ms,
            ))
            return atoms

    @staticmethod
    def _decomposition_context(
        manuscript: ManuscriptVersion | None, revision: ClaimRevision
    ) -> str:
        """Real manuscript context for atomic decomposition.

        Prefer the claim's own section when known, else a window around its
        offset locator, else the whole manuscript — never the source quote
        duplicated (the old prompt had no actual manuscript text to copy
        quotes from).
        """
        if manuscript is None:
            return revision.source_quote
        content = manuscript.content_text
        if revision.section_id:
            from radar.services.manuscript_parser import build_document_map
            doc_map = build_document_map(content)
            for section in doc_map.sections:
                if section.section_id == revision.section_id:
                    return truncate_for_prompt(
                        section.text, purpose="claim decomposition"
                    )
        if revision.source_locator.startswith("offset:"):
            try:
                start_text, end_text = revision.source_locator[len("offset:"):].split("-")
                start, end = int(start_text), int(end_text)
            except ValueError:
                start = end = -1
            if start >= 0:
                window_start = max(0, start - 2_000)
                window_end = min(len(content), end + 2_000)
                return truncate_for_prompt(
                    content[window_start:window_end], purpose="claim decomposition"
                )
        return truncate_for_prompt(content, purpose="claim decomposition")

    # ---- Semantic verification (statement-quote alignment) ----

    def verify_claim_semantics(
        self, claim_revision_id: str
    ) -> dict:
        """Verify that a claim's statement semantically aligns with its source quote.

        The existing pipeline only checks verbatim substring existence, which
        cannot detect when the LLM paraphrased statement adds unsupported
        superlatives or changes meaning. This method adds a semantic fidelity
        check using a separate LLM call.
        """
        with session_scope(self.session_factory) as session:
            revision = session.get(ClaimRevision, claim_revision_id)
            if revision is None:
                raise LookupError(f"claim revision not found: {claim_revision_id}")

            claim = session.get(Claim, revision.claim_id)
            stable_key = claim.stable_key if claim else ""
            client = self._resolve_llm_client()
            if client is None:
                return {
                    "claim_stable_key": stable_key,
                    "statement": revision.statement,
                    "source_quote": revision.source_quote,
                    "faithful": None,  # unverified, not verified
                    "issues": [],
                    "suggested_correction": "",
                    "verified": "skipped_no_llm",
                }

            instructions = (
                "Verify that the supplied STATEMENT faithfully reflects the SOURCE QUOTE "
                "without adding unsupported claims, strengthening modality, or changing "
                "the scientific meaning. Flag any discrepancies. If unfaithful, suggest "
                "a corrected statement that preserves the source quote's meaning exactly."
            )
            prompt = (
                f"{instructions}\n\nSTATEMENT:\n{revision.statement}\n\n"
                f"SOURCE QUOTE:\n{revision.source_quote}"
            )
            try:
                verification = client.generate_structured(
                    stage="claim_semantic_verification",
                    prompt=prompt,
                    response_model=ClaimSemanticVerification,
                )
            except Exception:
                return {
                    "claim_stable_key": stable_key,
                    "statement": revision.statement,
                    "source_quote": revision.source_quote,
                    "faithful": None,  # unverified, not verified
                    "issues": [],
                    "suggested_correction": "",
                    "verified": "failed_llm_call",
                }

            result = verification.model_dump()
            result["verified"] = "llm"
            return result

    # ---- End atomic decomposition and semantic verification ----

    # ---- Multi-stage pipeline (Solutions 1-5) ----

    def extract_with_pipeline(self, manuscript_version_id: str) -> dict:
        """Extract claims using the multi-stage section-aware pipeline."""
        from radar.services.claim_pipeline import ClaimPipeline
        pipeline = ClaimPipeline(
            self.session_factory,
            llm_client=self._resolve_llm_client(),
            settings=self.settings,
        )
        return pipeline.run_pipeline(manuscript_version_id)

    def track_claim_changes(self, prev_ver: str, curr_ver: str) -> dict:
        """Track claim lifecycle changes between versions (Solution 5)."""
        from radar.services.claim_pipeline import ClaimPipeline
        pipeline = ClaimPipeline(self.session_factory, llm_client=self._resolve_llm_client(), settings=self.settings)
        return pipeline.track_changes(prev_ver, curr_ver)

    def incremental_claim_extract(self, prev_ver: str, curr_ver: str) -> dict:
        """Extract claims only from changed sections (增量解析机制)."""
        from radar.services.claim_pipeline import ClaimPipeline
        pipeline = ClaimPipeline(self.session_factory, llm_client=self._resolve_llm_client(), settings=self.settings)
        return pipeline.incremental_extract(prev_ver, curr_ver)

    def confirm_with_feedback(
        self, revision_id: str, *, human_note: str = "",
        edit_fields: list[str] | None = None,
        before_values: dict | None = None, after_values: dict | None = None,
    ) -> ClaimRevision:
        return self._set_state_with_feedback(revision_id, "confirmed",
            human_note=human_note, edit_fields=edit_fields or [],
            before_values=before_values or {}, after_values=after_values or {})

    def reject_with_feedback(
        self, revision_id: str, *, reject_reason: str, human_note: str = "",
    ) -> ClaimRevision:
        return self._set_state_with_feedback(revision_id, "rejected",
            reject_reason=reject_reason, human_note=human_note)

    def _set_state_with_feedback(
        self, revision_id: str, state: str, *, reject_reason: str | None = None,
        human_note: str = "", edit_fields: list[str] | None = None,
        before_values: dict | None = None, after_values: dict | None = None,
    ) -> ClaimRevision:
        with session_scope(self.session_factory) as session:
            revision = session.get(ClaimRevision, revision_id)
            if revision is None:
                raise LookupError(f"claim revision not found: {revision_id}")
            manuscript = session.get(ManuscriptVersion, revision.manuscript_version_id)
            if state == "confirmed" and (
                manuscript is None or revision.source_quote not in manuscript.content_text
            ):
                raise ValueError("span_failed")
            revision.review_state = state
            claim = session.get(Claim, revision.claim_id)
            from radar.services.claim_pipeline import ClaimPipeline
            pipeline = ClaimPipeline(self.session_factory, llm_client=self._resolve_llm_client(), settings=self.settings)
            pipeline.record_g0_feedback(
                session, revision_id, state, reject_reason=reject_reason,
                edit_fields=edit_fields or [], before_values=before_values or {},
                after_values=after_values or {}, human_note=human_note,
            )
            session.add(AuditEvent(
                id=str(uuid4()), case_id=claim.case_id if claim else "unknown",
                event_type=f"claim_{state}", object_type="ClaimRevision",
                object_id=revision.id,
                payload_json={"reject_reason": reject_reason, "human_note": human_note},
                actor_type="human", actor_id="local_user",
            ))
            session.flush(); session.expunge(revision)
            return revision

    def confirm_candidate(self, revision_id: str) -> ClaimRevision:
        return self._set_state(revision_id, "confirmed")

    def reject_candidate(self, revision_id: str) -> ClaimRevision:
        return self._set_state(revision_id, "rejected")

    def _set_state(self, revision_id: str, state: str) -> ClaimRevision:
        with session_scope(self.session_factory) as session:
            revision = session.get(ClaimRevision, revision_id)
            if revision is None:
                raise LookupError(f"claim revision not found: {revision_id}")
            manuscript = session.get(ManuscriptVersion, revision.manuscript_version_id)
            if state == "confirmed" and (
                manuscript is None or revision.source_quote not in manuscript.content_text
            ):
                raise ValueError("span_failed")
            revision.review_state = state
            claim = session.get(Claim, revision.claim_id)
            # The primary confirm/reject buttons route here; they must leave the
            # same structured G0 trail as the *-feedback endpoints.
            session.add(G0Feedback(
                id=str(uuid4()), claim_revision_id=revision.id,
                decision=state, actor="local_user",
            ))
            session.add(
                AuditEvent(
                    id=str(uuid4()), case_id=claim.case_id if claim else "unknown",
                    event_type=f"claim_{state}", object_type="ClaimRevision",
                    object_id=revision.id, payload_json={}, actor_type="human", actor_id="local_user",
                )
            )
            session.flush()
            session.expunge(revision)
            return revision

    def edit_candidate(
        self, revision_id: str, *, statement: str, centrality: str,
        contract: dict, falsifiable_condition: str,
    ) -> ClaimRevision:
        with session_scope(self.session_factory) as session:
            current = session.get(ClaimRevision, revision_id)
            if current is None:
                raise LookupError(f"claim revision not found: {revision_id}")
            manuscript = session.get(ManuscriptVersion, current.manuscript_version_id)
            # G0 gate, same as _set_state: a confirmed revision must carry a
            # verbatim source quote. Editing must not bypass it.
            if current.source_quote not in (manuscript.content_text if manuscript else ""):
                raise ValueError("span_failed")
            # Editing a pending candidate must not auto-confirm it — G0 is a
            # separate human action. Only confirmed claims yield confirmed edits.
            was_confirmed = current.review_state == "confirmed"
            max_revision = session.scalar(
                select(func.max(ClaimRevision.revision_no)).where(ClaimRevision.claim_id == current.claim_id)
            ) or 1
            current.review_state = "superseded"
            revised = ClaimRevision(
                id=str(uuid4()), claim_id=current.claim_id,
                manuscript_version_id=current.manuscript_version_id,
                revision_no=max_revision + 1, statement=statement,
                claim_type="empirical_result", centrality=centrality,
                contract_json=EmpiricalClaimContract.model_validate(contract).model_dump(),
                falsifiable_condition=falsifiable_condition,
                source_quote=current.source_quote, source_locator=current.source_locator,
                review_state="confirmed" if was_confirmed else "candidate",
                supersedes_id=current.id,
            )
            session.add(revised)
            session.flush()
            claim = session.get(Claim, revised.claim_id)
            # Human edits must leave the same G0 trail as confirmations.
            session.add(G0Feedback(
                id=str(uuid4()), claim_revision_id=current.id, decision="edited",
                edit_fields_json=["statement", "centrality", "contract", "falsifiable_condition"],
                before_values_json={
                    "statement": current.statement,
                    "contract": current.contract_json,
                },
                after_values_json={
                    "statement": statement,
                    "contract": revised.contract_json,
                },
                actor="local_user",
            ))
            session.add(AuditEvent(
                id=str(uuid4()), case_id=claim.case_id if claim else "unknown",
                event_type="claim_edited", object_type="ClaimRevision",
                object_id=revised.id, payload_json={"supersedes_id": current.id},
                actor_type="human", actor_id="local_user",
            ))
            session.flush()
            session.expunge(revised)
            return revised

    def split_candidate(self, revision_id: str, statements: list[str]) -> list[ClaimRevision]:
        cleaned = [statement.strip() for statement in statements if statement.strip()]
        if len(cleaned) < 2:
            raise ValueError("split requires at least two statements")
        with session_scope(self.session_factory) as session:
            original = session.get(ClaimRevision, revision_id)
            if original is None:
                raise LookupError(f"claim revision not found: {revision_id}")
            original.review_state = "superseded"
            original_claim = session.get(Claim, original.claim_id)
            created: list[ClaimRevision] = []
            for index, statement in enumerate(cleaned, start=1):
                claim = Claim(
                    id=str(uuid4()), case_id=original_claim.case_id,
                    stable_key=f"{original_claim.stable_key}.{index}", lifecycle_state="active",
                )
                revision = ClaimRevision(
                    id=str(uuid4()), claim_id=claim.id,
                    manuscript_version_id=original.manuscript_version_id, revision_no=1,
                    statement=statement, claim_type="empirical_result",
                    centrality=original.centrality, contract_json=original.contract_json,
                    falsifiable_condition=original.falsifiable_condition,
                    source_quote=original.source_quote, source_locator=original.source_locator,
                    review_state="candidate", supersedes_id=original.id,
                )
                session.add(claim)
                session.flush()
                session.add(revision)
                session.flush()
                created.append(revision)
            session.add(G0Feedback(
                id=str(uuid4()), claim_revision_id=original.id, decision="edited",
                edit_fields_json=["split"],
                before_values_json={"statement": original.statement},
                after_values_json={
                    "child_stable_keys": [
                        f"{original_claim.stable_key}.{index}" for index in range(1, len(cleaned) + 1)
                    ],
                },
                actor="local_user",
            ))
            session.add(AuditEvent(
                id=str(uuid4()), case_id=original_claim.case_id,
                event_type="claim_split", object_type="ClaimRevision",
                object_id=original.id, payload_json={
                    "child_claim_ids": [item.claim_id for item in created],
                },
                actor_type="human", actor_id="local_user",
            ))
            session.flush()
            for revision in created:
                session.expunge(revision)
            return created
