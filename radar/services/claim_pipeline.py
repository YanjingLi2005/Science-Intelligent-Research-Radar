"""Multi-stage claim extraction pipeline (Solution 2).

Replaces single-shot LLM extraction with:
  Candidate Detection → Author Attribution → Claim Classification
  → Atomic Decomposition → Qualifier Extraction → Evidence Verification
  → Deduplication → G0 Human Confirmation
"""

import re
import json
import hashlib
import time
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from radar.config import Settings, estimate_llm_cost_usd, get_settings
from radar.db import SessionLocal, session_scope
from radar.llm.base import LLMClient
from radar.llm.factory import build_analysis_llm
from radar.llm.text_utils import truncate_for_prompt
from radar.models import (
    AuditEvent,
    Claim,
    ClaimRevision,
    G0Feedback,
    ManuscriptVersion,
    ModelRun,
)
from radar.services.evidence_service import resolve_exact_quote
from radar.schemas import (
    AuthorAttribution,
    ClaimCandidate,
    ClaimLifecycle,
    ClaimRole,
    ClaimType,
    DocumentMap,
    ManuscriptOverview,
    SectionInfo,
)
from radar.services.manuscript_parser import build_document_map, generate_manuscript_overview


PROMPT_DIR = Path(__file__).parents[1] / "llm" / "prompts"


def _pipeline_prompt(stage: str, payload: dict) -> str:
    path = PROMPT_DIR / "pipeline" / f"{stage}.txt"
    if path.exists():
        instructions = path.read_text(encoding="utf-8").strip()
        return f"{instructions}\n\nINPUT:\n{json.dumps(payload, ensure_ascii=False, indent=2)}"
    return json.dumps(payload, ensure_ascii=False, indent=2)


SECTION_CLAIM_FOCUS: dict[str, str] = {
    "abstract":     "Identify the paper's core conclusions and main contributions.",
    "introduction": "Extract claims about contributions, innovation, and research gaps.",
    "methods":      "Extract claims about method design, mechanisms, and components.",
    "results":      "Extract experimental and quantitative result claims with conditions.",
    "discussion":   "Extract explanatory claims, generalizations, and implications.",
    "limitations":  "Extract boundary conditions and acknowledged limitations.",
    "default":      "Extract any empirical or methodological claims.",
}


def _section_focus(heading: str) -> str:
    clean = re.sub(r"^\d+(?:\.\d+)*\.?\s*", "", heading).strip().lower()
    for key, focus in SECTION_CLAIM_FOCUS.items():
        if key in clean:
            return focus
    return SECTION_CLAIM_FOCUS["default"]


class ClaimPipeline:
    """Multi-stage claim extraction pipeline."""

    def __init__(
        self,
        session_factory: sessionmaker[Session] = SessionLocal,
        *,
        llm_client: LLMClient | None = None,
        settings: Settings | None = None,
    ):
        self.session_factory = session_factory
        self.settings = settings or get_settings()
        self.llm_client = llm_client or build_analysis_llm(self.settings)

    # ---- Stage 0: Manuscript Overview (Solution 3) ----

    def stage_overview(self, content: str) -> ManuscriptOverview:
        return generate_manuscript_overview(content, llm_client=self.llm_client, settings=self.settings)

    # ---- Stage 1: Build Document Map (Solution 1) ----

    def stage_document_map(self, content: str) -> DocumentMap:
        return build_document_map(content)

    # ---- Stage 2: Candidate Detection (per section) ----

    def stage_candidates(self, content: str, overview: ManuscriptOverview, doc_map: DocumentMap) -> list[ClaimCandidate]:
        if self.llm_client is None:
            return self._rule_candidates(content, doc_map)

        all_candidates: list[ClaimCandidate] = []
        llm_failed = False
        for section in doc_map.sections:
            if len(section.text.strip()) < 200:
                continue
            focus = _section_focus(section.heading)
            payload = {
                "section_heading": section.heading,
                "section_text": section.text[:8000],
                "extraction_focus": focus,
                "overview": overview.model_dump(),
            }
            try:
                output = self.llm_client.generate_structured(
                    stage="claim_candidate_detection",
                    prompt=_pipeline_prompt("candidate_detection", payload),
                    response_model=ClaimCandidateBatch,
                )
                for c in output.candidates:
                    c.section_id = section.section_id
                    c.source_locator = f"sec:{section.section_id}"
                    all_candidates.append(c)
            except Exception:
                # LLM unavailable/failed: fall back to rule-based detection
                llm_failed = True
                break

        if llm_failed or not all_candidates:
            # Degrade to deterministic extraction when the LLM path is broken
            rule_based = self._rule_candidates(content, doc_map)
            if rule_based:
                return rule_based
        return all_candidates

    def _rule_candidates(self, content: str, doc_map: DocumentMap) -> list[ClaimCandidate]:
        """Deterministic fallback using section structure."""
        candidates: list[ClaimCandidate] = []
        sentence_pat = re.compile(r"(?:^|(?<=[.!?])\s+)(.{40,600}?[.!?])(?=\s+[A-Z]|\s*$)", re.DOTALL)
        key_terms = re.compile(r"\b(improves?|achieves?|reduces?|outperforms?|shows?|finds?|demonstrates?|"
                               r"proposes?|introduces?|maintains?|reaches?|obtains?|yields?|"
                               r"outperforms?|surpasses?|outperforms?|is robust to|is superior to)\b", re.I)

        for section in doc_map.sections:
            # Strip LaTeX/markdown heading markup from section start
            sec_text = re.sub(r"^(?:\n\s*)*\\sub\w+\s*\{[^}]*\}\s*", "", section.text)
            sec_text = re.sub(r"^(?:\n\s*)*\\section\*?\s*\{[^}]*\}\s*", "", sec_text)
            sec_text = re.sub(r"^(?:\n\s*)*#+\s*[^\n]*\n\s*", "", sec_text)
            sec_text = re.sub(r"^(?:\n\s*)*\d+(?:\.\d+)*\.?\s+[^\n]*\n\s*", "", sec_text)

            for match in sentence_pat.finditer(sec_text):
                quote = match.group(1).strip()
                # Skip sentences that are just headings
                if re.search(r"\\section|\\subsection|^#{1,4}\s", quote):
                    continue
                # Claim-like: has outcome keyword AND (number OR comparative framing)
                is_claim = key_terms.search(quote) and (
                    re.search(r"\d", quote) or re.search(r"\b(more|better|higher|lower|state-of-the-art|SOTA)\b", quote, re.I)
                )
                if is_claim:
                    candidates.append(ClaimCandidate(
                        statement=quote,
                        source_quote=quote,
                        source_locator=f"sec:{section.section_id}",
                        section_id=section.section_id,
                    ))
        return candidates[:20]

    # ---- Stage 3: Author Attribution ----

    def stage_attribution(self, candidates: list[ClaimCandidate], overview: ManuscriptOverview) -> list[dict]:
        if self.llm_client is None:
            return [{"author_attribution": "uncertain", "confidence": 0.5} for _ in candidates]

        results: list[dict] = []
        for c in candidates[:15]:
            payload = {
                "statement": c.statement,
                "source_quote": c.source_quote,
                "author_terms": overview.author_terms,
                "cited_work_markers": overview.cited_work_markers,
            }
            try:
                output = self.llm_client.generate_structured(
                    stage="claim_attribution",
                    prompt=_pipeline_prompt("author_attribution", payload),
                    response_model=AttributionOutput,
                )
                results.append({
                    "author_attribution": output.attribution,
                    "confidence": output.confidence,
                })
            except Exception:
                results.append({"author_attribution": "uncertain", "confidence": 0.5})
        return results

    # ---- Stage 4: Claim Classification ----

    def stage_classification(self, candidates: list[ClaimCandidate], overview: ManuscriptOverview) -> list[dict]:
        if self.llm_client is None:
            return [{"claim_type": "empirical_result", "claim_role": "sub_claim"} for _ in candidates]

        results: list[dict] = []
        for c in candidates[:15]:
            payload = {"statement": c.statement, "source_quote": c.source_quote, "overview": overview.model_dump()}
            try:
                output = self.llm_client.generate_structured(
                    stage="claim_classification",
                    prompt=_pipeline_prompt("claim_classification", payload),
                    response_model=ClassificationOutput,
                )
                results.append({"claim_type": output.claim_type, "claim_role": output.claim_role})
            except Exception:
                results.append({"claim_type": "empirical_result", "claim_role": "sub_claim"})
        return results

    # ---- Stage 5: Deduplication ----

    def stage_deduplicate(self, candidates: list[ClaimCandidate]) -> list[ClaimCandidate]:
        seen: set[str] = set()
        unique: list[ClaimCandidate] = []
        for c in candidates:
            normalized = re.sub(r"\s+", " ", c.statement.lower()).strip()
            if normalized not in seen:
                seen.add(normalized)
                unique.append(c)
        return unique

    # ---- Stage 6: G0 Feedback Recording (Solution 4) ----

    def record_g0_feedback(
        self,
        session: Session,
        claim_revision_id: str,
        decision: str,
        *,
        reject_reason: str | None = None,
        edit_fields: list[str] | None = None,
        before_values: dict | None = None,
        after_values: dict | None = None,
        human_note: str | None = None,
    ) -> G0Feedback:
        fb = G0Feedback(
            id=str(uuid4()),
            claim_revision_id=claim_revision_id,
            decision=decision,
            reject_reason=reject_reason,
            edit_fields_json=edit_fields or [],
            before_values_json=before_values or {},
            after_values_json=after_values or {},
            human_note=human_note,
            actor="local_user",
        )
        session.add(fb)
        session.flush()
        return fb

    # ---- Full Pipeline ----

    def run_pipeline(
        self,
        manuscript_version_id: str,
        *,
        progress_callback=None,
    ) -> dict:
        with session_scope(self.session_factory) as session:
            ms = session.get(ManuscriptVersion, manuscript_version_id)
            if ms is None:
                raise LookupError(f"manuscript not found: {manuscript_version_id}")

            content = ms.content_text
            results = {"stages_completed": [], "claims_extracted": 0, "stage_details": {}}

            # Stage 0: Overview
            overview = self.stage_overview(content)
            results["stage_details"]["overview"] = overview.model_dump()
            results["stages_completed"].append("overview")

            # Stage 1: Document Map
            doc_map = self.stage_document_map(content)
            results["stage_details"]["sections"] = len(doc_map.sections)

            # Stage 2: Candidates
            candidates = self.stage_candidates(content, overview, doc_map)
            results["stages_completed"].append("candidates")
            results["stage_details"]["candidates_raw"] = len(candidates)

            # Stage 5: Deduplicate
            candidates = self.stage_deduplicate(candidates)
            results["stage_details"]["after_dedup"] = len(candidates)

            # Stage 3: Attribution + Stage 4: Classification (batched)
            if self.llm_client and candidates:
                attributions = self.stage_attribution(candidates, overview)
                classifications = self.stage_classification(candidates, overview)
                results["stages_completed"].extend(["attribution", "classification"])

                # Assemble full results
                assembled = []
                dropped_anchors: list[str] = []
                next_number = self._next_key(session, ms.case_id)
                for i, c in enumerate(candidates):
                    attr = attributions[i] if i < len(attributions) else {"author_attribution": "uncertain", "confidence": 0.5}
                    cls = classifications[i] if i < len(classifications) else {"claim_type": "empirical_result", "claim_role": "sub_claim"}

                    # Skip cited/background claims
                    if attr["author_attribution"] in ("cited_author", "background_knowledge", "future_work"):
                        continue

                    # G0 gate: only candidates whose source quote resolves to
                    # one exact manuscript span may be persisted (same standard
                    # as the main extraction path). LLM-invented quotes are
                    # dropped, never stored — otherwise the claim can never be
                    # confirmed and the quote breaks evidence verification.
                    resolved = resolve_exact_quote(c.source_quote, ms.content_text)
                    if resolved is None:
                        dropped_anchors.append(c.statement[:120])
                        continue
                    quote, start, end = resolved

                    claim = Claim(
                        id=str(uuid4()),
                        case_id=ms.case_id,
                        stable_key=f"C{next_number}",
                        lifecycle_state="active",
                        track_state="new",
                        claim_role=cls["claim_role"],
                    )
                    next_number += 1
                    revision = ClaimRevision(
                        id=str(uuid4()),
                        claim_id=claim.id,
                        manuscript_version_id=ms.id,
                        revision_no=1,
                        statement=c.statement,
                        claim_type=cls["claim_type"],
                        centrality="core" if cls["claim_role"] == "core_conclusion" else "major",
                        contract_json={},
                        falsifiable_condition="",
                        source_quote=quote,
                        source_locator=f"offset:{start}-{end}",
                        review_state="candidate",
                        author_attribution=attr["author_attribution"],
                        section_id=c.section_id,
                    )
                    session.add(claim)
                    session.flush()
                    session.add(revision)
                    assembled.append(revision)

                session.flush()
                results["claims_extracted"] = len(assembled)
                results["dropped_anchors"] = dropped_anchors
            else:
                # Fallback: heuristic extraction
                assembled = []
                dropped_anchors = []
                next_number = self._next_key(session, ms.case_id)
                for i, c in enumerate(candidates[:12]):
                    resolved = resolve_exact_quote(c.source_quote, ms.content_text)
                    if resolved is None:
                        dropped_anchors.append(c.statement[:120])
                        continue
                    quote, start, end = resolved
                    claim = Claim(
                        id=str(uuid4()),
                        case_id=ms.case_id,
                        stable_key=f"C{next_number}",
                        lifecycle_state="active",
                        track_state="new",
                        claim_role="sub_claim",
                    )
                    next_number += 1
                    revision = ClaimRevision(
                        id=str(uuid4()),
                        claim_id=claim.id,
                        manuscript_version_id=ms.id,
                        revision_no=1,
                        statement=c.statement,
                        claim_type="empirical_result",
                        centrality="major",
                        contract_json={},
                        falsifiable_condition="",
                        source_quote=quote,
                        source_locator=f"offset:{start}-{end}",
                        review_state="candidate",
                        author_attribution="uncertain",
                        section_id=c.section_id,
                    )
                    session.add(claim)
                    session.flush()
                    session.add(revision)
                    assembled.append(revision)
                session.flush()
                results["claims_extracted"] = len(assembled)
                results["dropped_anchors"] = dropped_anchors

            results["stages_completed"].append("persisted")
            return results

    @staticmethod
    def _next_key(session: Session, case_id: str) -> int:
        existing = list(session.scalars(select(Claim.stable_key).where(Claim.case_id == case_id)))
        nums = [int(m.group(1)) for s in existing if (m := re.fullmatch(r"C(\d+)", s))]
        return max(nums, default=0) + 1

    # ---- Incremental Extraction (增量解析机制) ----

    def incremental_extract(
        self,
        previous_version_id: str,
        current_version_id: str,
    ) -> dict:
        """Extract claims ONLY from changed sections between manuscript versions.

        Instead of re-extracting the full manuscript every time a new version
        is uploaded, this:
        1. Diffs the two versions at section level (diff_sections)
        2. Only runs LLM extraction on changed/new sections
        3. Carries forward claims from unchanged sections (reusing claim_ids)
        4. Marks claims from deleted sections as deleted

        Returns per-section results so the UI can show what was analyzed.
        """
        with session_scope(self.session_factory) as session:
            prev = session.get(ManuscriptVersion, previous_version_id)
            curr = session.get(ManuscriptVersion, current_version_id)
            if prev is None or curr is None:
                raise LookupError("version_not_found")

            # Section-level diff: which sections changed?
            try:
                from radar.services.claim_diff import diff_sections
                section_changes = diff_sections(prev.content_text, curr.content_text)
            except ImportError:
                section_changes = []

            changed_sections: set[str] = set()
            for change in section_changes:
                if change.get("change") != "unchanged":
                    changed_sections.add(change.get("section", "").strip().lower())

            # Build current document map to map sections to offsets
            current_map = build_document_map(curr.content_text)

            # Find which sections in the CURRENT doc correspond to changes.
            # When nothing changed at all, every headed section is unchanged —
            # claims must carry with their state, not be re-extracted.
            sections_to_extract: list[SectionInfo] = []
            unchanged_sections: list[SectionInfo] = []
            for section in current_map.sections:
                key = section.heading.strip().lower()
                if not key or key == "full text":
                    sections_to_extract.append(section)
                    continue
                if key in changed_sections:
                    sections_to_extract.append(section)
                else:
                    unchanged_sections.append(section)

            # Carry forward claims whose source_quote falls in unchanged
            # sections. Only the LATEST revision per claim is considered —
            # an edit/split leaves superseded siblings on the same version
            # that must not be carried again (duplicate claims, broken DAG).
            prev_rows = list(session.scalars(
                select(ClaimRevision)
                .where(ClaimRevision.manuscript_version_id == prev.id)
                .order_by(ClaimRevision.revision_no.desc(), ClaimRevision.created_at.desc())
            ))
            latest_by_claim: dict[str, ClaimRevision] = {}
            for rev in prev_rows:
                latest_by_claim.setdefault(rev.claim_id, rev)
            prev_claims = list(latest_by_claim.values())

            carried = 0
            reextracted = 0
            held_ranges: list[tuple[int, int]] = []
            for prev_rev in prev_claims:
                # Superseded/rejected revisions are history: carrying them
                # would resurrect replaced or rejected content.
                if prev_rev.review_state in ("superseded", "rejected"):
                    continue
                claim = session.get(Claim, prev_rev.claim_id)
                quote_pos = curr.content_text.find(prev_rev.source_quote)
                if quote_pos >= 0:
                    # Check which section this quote falls in
                    in_changed = any(
                        sec.start_offset <= quote_pos < sec.end_offset
                        for sec in sections_to_extract
                    )
                    if not in_changed:
                        # Quote unchanged and outside changed sections: carry
                        # forward (exact quote preserved, state preserved).
                        if claim:
                            claim.track_state = "unchanged"
                            previous_state = prev_rev.review_state
                            prev_rev.review_state = "superseded"
                            new_rev = ClaimRevision(
                                id=str(uuid4()),
                                claim_id=claim.id,
                                manuscript_version_id=curr.id,
                                revision_no=prev_rev.revision_no + 1,
                                statement=prev_rev.statement,
                                claim_type=prev_rev.claim_type,
                                centrality=prev_rev.centrality,
                                contract_json=prev_rev.contract_json,
                                falsifiable_condition=prev_rev.falsifiable_condition,
                                source_quote=prev_rev.source_quote,
                                source_locator=prev_rev.source_locator,
                                review_state=previous_state,
                                supersedes_id=prev_rev.id,
                                author_attribution=prev_rev.author_attribution,
                                section_id=prev_rev.section_id,
                            )
                            session.add(new_rev)
                            held_ranges.append((quote_pos, quote_pos + len(prev_rev.source_quote)))
                            carried += 1
                            continue
                    # Quote still verbatim but inside a changed section: carry
                    # the claim identity with a re-gated (candidate) revision.
                    if claim:
                        claim.track_state = "needs_revalidation"
                        prev_rev.review_state = "superseded"
                        new_rev = ClaimRevision(
                            id=str(uuid4()),
                            claim_id=claim.id,
                            manuscript_version_id=curr.id,
                            revision_no=prev_rev.revision_no + 1,
                            statement=prev_rev.statement,
                            claim_type=prev_rev.claim_type,
                            centrality=prev_rev.centrality,
                            contract_json=prev_rev.contract_json,
                            falsifiable_condition=prev_rev.falsifiable_condition,
                            source_quote=prev_rev.source_quote,
                            source_locator=prev_rev.source_locator,
                            review_state="candidate",  # G0 re-gate
                            supersedes_id=prev_rev.id,
                            author_attribution=prev_rev.author_attribution,
                            section_id=prev_rev.section_id,
                        )
                        session.add(new_rev)
                        held_ranges.append((quote_pos, quote_pos + len(prev_rev.source_quote)))
                        carried += 1
                        reextracted += 1
                        continue
                # Quote no longer present in the current manuscript: the
                # claim is orphaned on this version until a human decides.
                if claim:
                    claim.track_state = "needs_revalidation"
                    reextracted += 1

            session.flush()

            # Now extract claims from changed sections only
            overview = self.stage_overview(curr.content_text)
            extracted_from_changed = 0
            if sections_to_extract:
                # Reuse stage_candidates but restricted to changed sections
                candidates = self._extract_from_sections(sections_to_extract, overview)
                if candidates:
                    next_number = self._next_key(session, curr.case_id)
                    for c in candidates:
                        # Same G0 anchor gate as the main pipeline, plus: never
                        # mint a new claim for text that already belongs to a
                        # carried claim (overlapping spans are held).
                        resolved = resolve_exact_quote(c.source_quote, curr.content_text)
                        if resolved is None:
                            continue
                        quote, start, end = resolved
                        if any(start < taken_end and end > taken_start for taken_start, taken_end in held_ranges):
                            continue
                        claim = Claim(
                            id=str(uuid4()),
                            case_id=curr.case_id,
                            stable_key=f"C{next_number}",
                            lifecycle_state="active",
                            track_state="new",
                            claim_role="sub_claim",
                        )
                        next_number += 1
                        revision = ClaimRevision(
                            id=str(uuid4()),
                            claim_id=claim.id,
                            manuscript_version_id=curr.id,
                            revision_no=1,
                            statement=c.statement,
                            claim_type="empirical_result",
                            centrality="major",
                            contract_json={},
                            falsifiable_condition="",
                            source_quote=quote,
                            source_locator=f"offset:{start}-{end}",
                            review_state="candidate",
                            author_attribution="uncertain",
                            section_id=c.section_id,
                        )
                        session.add(claim)
                        session.flush()
                        session.add(revision)
                        held_ranges.append((start, end))
                        extracted_from_changed += 1
                    session.flush()

            return {
                "section_changes": section_changes,
                "sections_analyzed": len(sections_to_extract),
                "sections_carried": len(unchanged_sections),
                "claims_carried": carried,
                "claims_reextracted_or_revalidated": reextracted,
                "new_claims_from_changed_sections": extracted_from_changed,
            }

    def _extract_from_sections(
        self,
        sections: list[SectionInfo],
        overview: ManuscriptOverview,
    ) -> list[ClaimCandidate]:
        """Extract claim candidates from a restricted set of sections."""
        if self.llm_client is None:
            content = "\n\n".join(s.text for s in sections)
            from radar.services.manuscript_parser import build_document_map
            doc_map = build_document_map(content)
            return self._rule_candidates(content, doc_map)

        all_candidates: list[ClaimCandidate] = []
        for section in sections:
            if len(section.text.strip()) < 200:
                continue
            focus = _section_focus(section.heading)
            payload = {
                "section_heading": section.heading,
                "section_text": section.text[:8000],
                "extraction_focus": focus,
                "overview": overview.model_dump(),
            }
            try:
                output = self.llm_client.generate_structured(
                    stage="claim_candidate_detection",
                    prompt=_pipeline_prompt("candidate_detection", payload),
                    response_model=ClaimCandidateBatch,
                )
                for c in output.candidates:
                    c.section_id = section.section_id
                    c.source_locator = f"sec:{section.section_id}"
                    all_candidates.append(c)
            except Exception:
                continue
        if not all_candidates:
            content = "\n\n".join(s.text for s in sections)
            from radar.services.manuscript_parser import build_document_map
            doc_map = build_document_map(content)
            return self._rule_candidates(content, doc_map)
        return all_candidates

    # ---- Incremental Tracking (Solution 5) ----

    def track_changes(
        self,
        previous_version_id: str,
        current_version_id: str,
    ) -> dict:
        """Compare claims between manuscript versions and mark lifecycle states."""
        with session_scope(self.session_factory) as session:
            prev = session.get(ManuscriptVersion, previous_version_id)
            curr = session.get(ManuscriptVersion, current_version_id)
            if prev is None or curr is None:
                return {"error": "version_not_found"}

            # Compare the LATEST revision per claim on each version: edits and
            # splits leave superseded siblings that must not skew the diff.
            prev_rows = list(session.scalars(
                select(ClaimRevision)
                .where(ClaimRevision.manuscript_version_id == prev.id)
                .order_by(ClaimRevision.revision_no.desc(), ClaimRevision.created_at.desc())
            ))
            latest_prev: dict[str, ClaimRevision] = {}
            for rev in prev_rows:
                latest_prev.setdefault(rev.claim_id, rev)
            curr_rows = list(session.scalars(
                select(ClaimRevision)
                .where(ClaimRevision.manuscript_version_id == curr.id)
                .order_by(ClaimRevision.revision_no.desc(), ClaimRevision.created_at.desc())
            ))
            latest_curr: dict[str, ClaimRevision] = {}
            for rev in curr_rows:
                latest_curr.setdefault(rev.claim_id, rev)
            prev_claims = list(latest_prev.values())
            curr_claims = list(latest_curr.values())

            prev_keys = {c.claim_id for c in prev_claims}
            curr_keys = {c.claim_id for c in curr_claims}

            new_count = len(curr_keys - prev_keys)
            deleted_count = len(prev_keys - curr_keys)
            unchanged_count = len(prev_keys & curr_keys)

            # Detect modified claims (same claim_id, different statement)
            modified = 0
            modified_details: list[dict] = []
            for cc in curr_claims:
                if cc.claim_id in prev_keys:
                    pc = next(p for p in prev_claims if p.claim_id == cc.claim_id)
                    if cc.statement != pc.statement or cc.source_quote != pc.source_quote:
                        claim = session.get(Claim, cc.claim_id)
                        if claim:
                            claim.track_state = "modified"
                            try:
                                from radar.services.claim_diff import diff_claims
                                diff = diff_claims(pc.statement, cc.statement)
                                modified_details.append({
                                    "claim_id": cc.claim_id,
                                    "stable_key": claim.stable_key,
                                    "change_type": diff["change_type"],
                                    "change_ratio": diff["change_ratio"],
                                })
                            except ImportError:
                                modified_details.append({
                                    "claim_id": cc.claim_id,
                                    "stable_key": claim.stable_key,
                                    "change_type": "modified",
                                    "change_ratio": 1.0,
                                })
                        modified += 1
                    else:
                        claim = session.get(Claim, cc.claim_id)
                        if claim:
                            claim.track_state = "unchanged"

            # Mark deleted claims
            for pc in prev_claims:
                if pc.claim_id not in curr_keys:
                    claim = session.get(Claim, pc.claim_id)
                    if claim:
                        claim.track_state = "deleted"

            session.flush()

            # Section-level change analysis (Solution 5: which sections changed)
            section_changes: list[dict] = []
            try:
                from radar.services.claim_diff import diff_sections
                section_changes = diff_sections(prev.content_text, curr.content_text)
            except ImportError:
                pass

            return {
                "new": new_count,
                "modified": modified,
                "deleted": deleted_count,
                "unchanged": unchanged_count - modified,
                "total_current": len(curr_claims),
                "modified_details": modified_details,
                "section_changes": section_changes,
            }


# ---- Pydantic output models for pipeline stages ----

from pydantic import BaseModel, Field


class ClaimCandidateBatch(BaseModel):
    candidates: list[ClaimCandidate] = Field(default_factory=list, max_length=15)


class AttributionOutput(BaseModel):
    attribution: AuthorAttribution = "uncertain"
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)


class ClassificationOutput(BaseModel):
    claim_type: ClaimType = "empirical_result"
    claim_role: ClaimRole = "sub_claim"
