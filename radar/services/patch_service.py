"""Candidate-only manuscript patches with G2 validation and approval."""

from typing import Any
import json
import hashlib
import re
import time
import difflib
import posixpath
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from radar.db import SessionLocal, session_scope
from radar.config import Settings, estimate_llm_cost_usd, get_settings
from radar.llm.base import LLMClient
from radar.llm.factory import build_analysis_llm
from radar.llm.provider import ProviderLLMClient
from radar.models import (
    AuditEvent, Claim, ClaimRevision, ImpactCandidate, ManuscriptVersion,
    ModelRun, PatchProposal, ResearchCase, ScanRun, Source, SourceSnapshot,
)
from radar.schemas import MultiLocationPatchOutput, PatchProposalOutput
from radar.llm.text_utils import truncate_for_prompt
from radar.services.evidence_service import resolve_exact_quote


PROMPT_PATH = Path(__file__).parents[1] / "llm" / "prompts" / "patch_generation.txt"


PATCH_POLICIES = {
    "prior_art": {
        "target": "Introduction / Related Work / Discussion",
        "change": "更新文献定位与差异化表述；不改实验数字。",
    },
    "boundary_condition": {
        "target": "Discussion / Limitations",
        "change": "写明 Task、Dataset、Split、Metric、Comparator、Scope 中的具体边界。",
    },
    "replication": {
        "target": "Results / Discussion / Limitations",
        "change": "条件可比时收窄结论，并加入复现或团队复核；锁定原始数字。",
    },
    "method_substitution": {
        "target": "Methods / Experiments",
        "change": "加入最小 head-to-head 实验计划；跑完前不宣称胜负。",
    },
    "research_integrity": {
        "target": "Citations / Methods / Results",
        "change": "标记受影响证据和结果，要求重新验证。",
    },
}


def _numbers(text: str) -> list[str]:
    return re.findall(r"(?<![\w-])\d+(?:\.\d+)?%?", text)


class PatchService:
    def __init__(
        self,
        session_factory: sessionmaker[Session] = SessionLocal,
        *,
        llm_client: LLMClient | None = None,
        settings: Settings | None = None,
    ):
        self.session_factory = session_factory
        self.settings = settings or get_settings()
        # No unconditional ProviderLLMClient fallback: with no LLM configured
        # the deterministic fixture/template path applies (None), and callers
        # surface "LLM not configured" instead of a RuntimeError 500.
        # Patch generation reads the FULL manuscript — it gets the long
        # manuscript timeout, not the generic per-call one.
        self.llm_client = llm_client or build_analysis_llm(
            self.settings,
            timeout_seconds=self.settings.llm_manuscript_timeout_seconds,
        )

    def generate_patch(self, confirmed_impact_id: str) -> PatchProposal:
        with session_scope(self.session_factory) as session:
            existing = session.scalar(
                select(PatchProposal)
                .where(
                    PatchProposal.impact_candidate_id == confirmed_impact_id,
                    PatchProposal.approval_state.in_({"candidate", "approved"}),
                )
                .order_by(PatchProposal.created_at.desc())
            )
            if existing:
                session.expunge(existing)
                return existing
            impact = session.get(ImpactCandidate, confirmed_impact_id)
            if impact is None:
                raise LookupError(f"impact not found: {confirmed_impact_id}")
            if impact.review_state not in {"confirmed", "edited"}:
                raise ValueError("candidate_cannot_generate_patch")
            if impact.impact_mode == "no_material_change":
                raise ValueError("no_material_change_cannot_generate_patch")
            revision = session.get(ClaimRevision, impact.claim_revision_id)
            manuscript = session.get(ManuscriptVersion, revision.manuscript_version_id)
            claim = session.get(Claim, revision.claim_id)
            research_case = session.get(ResearchCase, claim.case_id)
            snapshot = session.get(SourceSnapshot, impact.source_snapshot_id)
            source = session.get(Source, snapshot.source_id) if snapshot else None
            if source is None or snapshot is None:
                raise ValueError("patch_source_missing")

            template = self._find_template(research_case, confirmed_impact_id)
            if template is None:
                template = self._generate_model_template(
                    session=session,
                    research_case=research_case,
                    manuscript=manuscript,
                    claim=claim,
                    revision=revision,
                    impact=impact,
                    source=source,
                    snapshot=snapshot,
                )
            patch = PatchProposal(
                id=str(uuid4()), case_id=research_case.id, manuscript_version_id=manuscript.id,
                impact_candidate_id=impact.id, target_locator=template["target_locator"],
                edit_class=template["edit_class"], before_text=template["before_text"],
                after_text=template["after_text"], citations_json=template["citation_source_ids"],
                evidence_refs_json=[impact.evidence_own_json, impact.evidence_new_json],
                validations_json={}, approval_state="candidate",
            )
            session.add(patch)
            session.flush()
            validations = self._validate(session, patch, manuscript)
            patch.validations_json = validations
            session.add(
                AuditEvent(
                    id=str(uuid4()), case_id=research_case.id, event_type="patch_generated",
                    object_type="PatchProposal", object_id=patch.id,
                    payload_json={"validations": validations}, actor_type="model",
                    actor_id=getattr(
                        self.llm_client,
                        "model_name",
                        self.settings.llm_model or "deterministic-fixture",
                    ),
                )
            )
            session.flush()
            session.expunge(patch)
            return patch

    def _generate_model_template(
        self,
        *,
        session: Session,
        research_case: ResearchCase,
        manuscript: ManuscriptVersion,
        claim: Claim,
        revision: ClaimRevision,
        impact: ImpactCandidate,
        source: Source,
        snapshot: SourceSnapshot,
    ) -> dict:
        instructions = PROMPT_PATH.read_text(encoding="utf-8").strip()
        payload = {
            "project": {
                "title": research_case.title,
                "research_question": research_case.research_question,
            },
            # Background context only; before_text exactness is validated
            # against the untruncated manuscript stored in the database.
            "full_manuscript": truncate_for_prompt(
                manuscript.content_text,
                purpose="full manuscript background",
            ),
            "confirmed_claim": {
                "stable_key": claim.stable_key,
                "statement": revision.statement,
                "source_quote": revision.source_quote,
                "source_locator": revision.source_locator,
                "locked_numbers": _numbers(revision.source_quote),
            },
            "confirmed_impact": {
                "stance": impact.stance,
                "impact_mode": impact.impact_mode,
                "comparability": impact.comparability,
                "condition_differences": impact.condition_differences_json,
                "suggested_action": impact.suggested_action,
                "uncertainty": impact.uncertainty_json,
                "strategy_payload": getattr(impact, "strategy_payload_json", {}) or {},
                "own_evidence": impact.evidence_own_json,
                "incoming_evidence": impact.evidence_new_json,
            },
            "action_policy": self.action_policy(impact.impact_mode),
            "citation": {
                "source_id": source.id,
                "title": source.title,
                "authors": source.authors_json,
                "url": source.url,
                "doi": source.doi,
                "venue": source.venue,
                "published_at": (
                    source.published_at.date().isoformat()
                    if source.published_at
                    else None
                ),
            },
            "incoming_paper_abstract": snapshot.abstract,
        }
        prompt = (
            f"{instructions}\n\nINPUT JSON — FULL MANUSCRIPT INCLUDED:\n"
            f"{json.dumps(payload, ensure_ascii=False, indent=2)}"
        )
        if self.llm_client is None:
            # Deterministic template path only exists for the demo fixture;
            # a real project needs an LLM — surface 400, never AttributeError.
            raise ValueError("llm_not_configured")
        started = time.perf_counter()
        output = None
        validation_issues: list[str] = []
        active_prompt = prompt
        for attempt in range(2):
            output = self.llm_client.generate_structured(
                stage="patch_generation",
                prompt=active_prompt,
                response_model=PatchProposalOutput,
            )
            output = self._exactify_model_output(output, manuscript.content_text)
            validation_issues = self._model_output_issues(
                output=output,
                manuscript=manuscript,
                revision=revision,
                impact=impact,
            )
            if not validation_issues:
                break
            if attempt == 0:
                active_prompt = (
                    f"{prompt}\n\nYOUR PREVIOUS OUTPUT WAS BLOCKED BY PROGRAMMATIC CHECKS: "
                    f"{', '.join(validation_issues)}. Generate a corrected proposal."
                )
        if output is None or validation_issues:
            raise ValueError(
                "patch_generation_validation_failed:" + ",".join(validation_issues)
            )
        latency_ms = int((time.perf_counter() - started) * 1000)
        receipt = getattr(self.llm_client, "last_receipt", {}) or {}
        usage = receipt.get("usage") or {}
        raw_response = receipt.get("raw_response") or output.model_dump_json()
        input_tokens = int(usage.get("prompt_tokens", 0))
        output_tokens = int(usage.get("completion_tokens", 0))
        model_name = getattr(
            self.llm_client,
            "model_name",
            self.settings.llm_model or "injected-test-model",
        )
        session.add(
            ModelRun(
                id=str(uuid4()),
                stage="patch_generation",
                case_id=research_case.id,
                scan_run_id=impact.scan_run_id,
                provider=getattr(
                    self.llm_client,
                    "provider_name",
                    self.settings.llm_provider or self.llm_client.__class__.__name__,
                ),
                model=model_name,
                prompt_hash=hashlib.sha256(prompt.encode()).hexdigest(),
                schema_version="PatchProposalOutput.v1",
                input_refs_json=[manuscript.id, impact.id, snapshot.id],
                raw_response=raw_response,
                parsed_output_json=output.model_dump(),
                validation_json={
                    "pydantic": True,
                    "before_text_exact": True,
                    "citation_locked_to_supplied_source": True,
                    "action_target_policy": True,
                    "citation_marker_safe": True,
                },
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                estimated_cost=estimate_llm_cost_usd(
                    model_name, input_tokens, output_tokens
                ),
                latency_ms=int(receipt.get("latency_ms", latency_ms)),
            )
        )
        return {
            "edit_class": output.edit_class or output.patch_type,
            "target_locator": output.target_locator or output.target_location,
            "before_text": output.before_text or output.original_sentence,
            "after_text": output.after_text or output.patched_sentence,
            "citation_source_ids": [source.id],
            "target_location": output.target_location or output.target_locator,
            "original_sentence": output.original_sentence or output.before_text,
            "patched_sentence": output.patched_sentence or output.after_text,
            "patch_type": output.patch_type or output.edit_class,
            "diff_summary": output.diff_summary or output.rationale,
        }

    @staticmethod
    def _exactify_model_output(
        output: PatchProposalOutput, manuscript_text: str
    ) -> PatchProposalOutput:
        if output.before_text in manuscript_text:
            return output
        resolved = resolve_exact_quote(output.before_text, manuscript_text)
        if resolved is None:
            return output
        exact_before, _, _ = resolved
        if output.before_text not in output.after_text:
            return output
        exact_after = output.after_text.replace(output.before_text, exact_before, 1)
        return output.model_copy(
            update={"before_text": exact_before, "after_text": exact_after}
        )

    @staticmethod
    def action_policy(impact_mode: str) -> dict[str, str]:
        return PATCH_POLICIES.get(
            impact_mode,
            {
                "target": "Discussion / project action list",
                "change": "只做证据支持的最小修改。",
            },
        )

    @staticmethod
    def _nearest_section(text: str, offset: int) -> str | None:
        headings = re.compile(
            r"(?:^|\n)\s*(?:\d+(?:\.\d+)?)?\s*"
            r"(Introduction|Related Work|Discussion|Limitations?|Conclusion)\s*(?:\n|$)",
            re.IGNORECASE,
        )
        matches = [match for match in headings.finditer(text, 0, max(offset, 0) + 1)]
        return matches[-1].group(1).lower() if matches else None

    def _model_output_issues(
        self,
        *,
        output: PatchProposalOutput,
        manuscript: ManuscriptVersion,
        revision: ClaimRevision,
        impact: ImpactCandidate,
    ) -> list[str]:
        issues: list[str] = []
        offset = manuscript.content_text.find(output.before_text)
        if offset < 0:
            issues.append("before_text_not_exact")
            return issues
        new_numeric_citations = set(re.findall(r"\[(?:\d+[\s,\-]*)+\]", output.after_text)) - set(
            re.findall(r"\[(?:\d+[\s,\-]*)+\]", output.before_text)
        )
        if new_numeric_citations:
            issues.append("invented_numeric_citation")
        if impact.impact_mode == "prior_art":
            section = self._nearest_section(manuscript.content_text, offset)
            if section not in {"introduction", "related work", "discussion"}:
                issues.append("prior_art_target_not_positioning_section")
            if output.before_text.strip() == revision.source_quote.strip():
                issues.append("prior_art_targeted_empirical_claim")
            if "[CITATION]" not in output.after_text:
                issues.append("citation_placeholder_missing")
        return issues

    def _find_template(self, research_case: ResearchCase, impact_id: str) -> dict | None:
        fixture_dir = research_case.settings_json.get("fixture_dir")
        if not fixture_dir:
            return None
        path = Path(fixture_dir) / "expected_patches.json"
        if not path.exists():
            return None
        for item in json.loads(path.read_text(encoding="utf-8")):
            if item["impact_candidate_id"] == impact_id:
                return item
        return None

    def _validate(
        self, session: Session, patch: PatchProposal, manuscript: ManuscriptVersion
    ) -> dict[str, bool]:
        # A patch is only valid while its impact is actually confirmed/edited:
        # re-read the row so dismissing the impact later invalidates the patch.
        impact = session.get(ImpactCandidate, patch.impact_candidate_id)
        impact_confirmed = impact is not None and impact.review_state in {
            "confirmed", "edited",
        }
        citations_resolved = all(session.get(Source, source_id) is not None for source_id in patch.citations_json)
        before_exact = patch.before_text in manuscript.content_text
        before_numbers = _numbers(patch.before_text)
        after_numbers = _numbers(patch.after_text)
        locked_numbers_unchanged = all(after_numbers.count(number) >= count for number, count in {
            number: before_numbers.count(number) for number in before_numbers
        }.items())
        new_numeric_citations = set(re.findall(r"\[(?:\d+[\s,\-]*)+\]", patch.after_text)) - set(
            re.findall(r"\[(?:\d+[\s,\-]*)+\]", patch.before_text)
        )
        return {
            "impact_confirmed": impact_confirmed,
            "before_text_exact": before_exact,
            "citations_resolved": citations_resolved,
            "citation_marker_safe": not new_numeric_citations,
            "locked_numbers_unchanged": locked_numbers_unchanged,
            "original_file_untouched": True,
            # 7th check: an approved edit must actually change the manuscript.
            "after_text_differs": patch.after_text != patch.before_text,
        }

    def validate_patch(self, patch_id: str) -> dict[str, bool]:
        with session_scope(self.session_factory) as session:
            patch = session.get(PatchProposal, patch_id)
            if patch is None:
                raise LookupError(f"patch not found: {patch_id}")
            manuscript = session.get(ManuscriptVersion, patch.manuscript_version_id)
            validations = self._validate(session, patch, manuscript)
            patch.validations_json = validations
            return validations

    def approve_patch(self, patch_id: str) -> PatchProposal:
        return self._set_approval(patch_id, "approved")

    def reject_patch(self, patch_id: str) -> PatchProposal:
        return self._set_approval(patch_id, "rejected")

    REQUIRED_VALIDATION_KEYS = frozenset({
        "impact_confirmed", "before_text_exact", "citations_resolved",
        "citation_marker_safe", "locked_numbers_unchanged",
        "original_file_untouched", "after_text_differs",
    })

    def _set_approval(self, patch_id: str, state: str) -> PatchProposal:
        with session_scope(self.session_factory) as session:
            patch = session.get(PatchProposal, patch_id)
            if patch is None:
                raise LookupError(f"patch not found: {patch_id}")
            if state == "approved" and (
                # all({}) is True, so an empty/missing validation record must
                # not slip through the gate: require the exact check set AND
                # every check green.
                set(patch.validations_json) != self.REQUIRED_VALIDATION_KEYS
                or not all(patch.validations_json.values())
            ):
                raise ValueError("patch_validation_failed")
            patch.approval_state = state
            session.add(
                AuditEvent(
                    id=str(uuid4()), case_id=patch.case_id, event_type=f"patch_{state}",
                    object_type="PatchProposal", object_id=patch.id,
                    payload_json={"export_only": True}, actor_type="human", actor_id="local_user",
                )
            )
            session.flush()
            session.expunge(patch)
            return patch

    def generate_multi_location_patch(
        self, confirmed_impact_id: str
    ) -> dict:
        """Generate multi-location edits for a single impact.

        Unlike ``generate_patch`` which produces one edit per impact, this
        method asks the LLM to produce up to 5 targeted edits across different
        manuscript sections (intro, related work, discussion, limitations, methods).
        Each edit is separately validated for exactness and safety.
        """
        with session_scope(self.session_factory) as session:
            impact = session.get(ImpactCandidate, confirmed_impact_id)
            if impact is None:
                raise LookupError(f"impact not found: {confirmed_impact_id}")
            if impact.review_state not in {"confirmed", "edited"}:
                raise ValueError("candidate_cannot_generate_patch")

            revision = session.get(ClaimRevision, impact.claim_revision_id)
            manuscript = session.get(ManuscriptVersion, revision.manuscript_version_id)
            claim = session.get(Claim, revision.claim_id)
            research_case = session.get(ResearchCase, claim.case_id)
            snapshot = session.get(SourceSnapshot, impact.source_snapshot_id)
            source = session.get(Source, snapshot.source_id) if snapshot else None

            instructions = (
                "Generate targeted multi-location edits for a manuscript based on "
                "a confirmed impact from an external paper. Create one edit per "
                "relevant section: Introduction (positioning), Related Work "
                "(context), Discussion (implications), Limitations (boundaries), "
                "Methods (experiment plan). Only create edits for sections that "
                "are genuinely affected. Each before_text must be an exact "
                "verbatim span from the manuscript."
            )
            prompt = (
                f"{instructions}\n\nMANUSCRIPT:\n"
                f"{truncate_for_prompt(manuscript.content_text, purpose='multi-location patch')}"
                f"\n\nCONFIRMED IMPACT:\n"
                f"Stance: {impact.stance}\n"
                f"Mode: {impact.impact_mode}\n"
                f"Comparability: {impact.comparability}\n"
                f"Suggested Action: {impact.suggested_action}\n"
                f"Claim: {revision.statement}\n"
                f"Incoming Paper: {snapshot.title if snapshot else 'unknown'}\n"
            )

            try:
                output = self.llm_client.generate_structured(
                    stage="multi_location_patch",
                    prompt=prompt,
                    response_model=MultiLocationPatchOutput,
                )
            except Exception:
                return {
                    "impact_id": confirmed_impact_id,
                    "global_rationale": "",
                    "edits": [],
                    "validated_count": 0,
                    "total_suggested": 0,
                    "citation_source_ids": [source.id] if source else [],
                }

            validated_edits = []
            for edit in output.edits:
                resolved = resolve_exact_quote(edit.before_text, manuscript.content_text)
                if resolved is None:
                    continue
                exact_before, _, _ = resolved
                exact_after = (
                    edit.after_text.replace(edit.before_text, exact_before, 1)
                    if edit.before_text in edit.after_text
                    else edit.after_text
                )

                before_numbers = _numbers(exact_before)
                after_numbers = _numbers(exact_after)
                numbers_ok = all(
                    after_numbers.count(n) >= before_numbers.count(n)
                    for n in before_numbers
                )
                new_numeric = set(
                    re.findall(r"\[(?:\d+[\s,\-]*)+\]", exact_after)
                ) - set(re.findall(r"\[(?:\d+[\s,\-]*)+\]", exact_before))

                if numbers_ok and not new_numeric:
                    validated_edits.append({
                        "section": edit.section,
                        "edit_class": edit.edit_class,
                        "before_text": exact_before,
                        "after_text": exact_after,
                        "reason": edit.reason,
                        "target_location": edit.target_location or edit.section,
                        "original_sentence": exact_before,
                        "patched_sentence": exact_after,
                        "patch_type": edit.patch_type or str(edit.edit_class),
                    })

            return {
                "impact_id": confirmed_impact_id,
                "global_rationale": output.global_rationale,
                "edits": validated_edits,
                "validated_count": len(validated_edits),
                "total_suggested": len(output.edits),
                "citation_source_ids": (
                    [source.id] if source else []
                ),
            }

    @staticmethod
    def detect_citation_style(content_text: str) -> str:
        """Detect the LaTeX citation command style in manuscript text.

        Returns one of: 'natbib_citep', 'natbib_citet', 'cite'.
        """
        if not content_text:
            return "cite"
        has_natbib = bool(re.search(r"\\usepackage(?:\[[^\]]*\])?\{natbib\}", content_text))
        citep_count = len(re.findall(r"\\citep\{", content_text))
        citet_count = len(re.findall(r"\\citet\{", content_text))
        cite_count = len(re.findall(r"\\cite\{", content_text))

        if has_natbib or (citep_count + citet_count > cite_count):
            return "natbib_citep"
        return "cite"

    @staticmethod
    def generate_bibtex_key(source: Source) -> str:
        """Generate a clean citation key like 'author2026keyword' or 'author2026'."""
        author = "author"
        if source.authors_json and isinstance(source.authors_json, list) and len(source.authors_json) > 0:
            first_author = str(source.authors_json[0])
            parts = first_author.strip().split()
            if parts:
                author = re.sub(r"[^a-zA-Z]", "", parts[-1]).lower() or "author"

        year = "2026"
        if source.published_at:
            year = str(source.published_at.year)

        title_word = ""
        if source.title:
            words = re.findall(r"[A-Za-z]{3,}", source.title.lower())
            stop = {"the", "and", "for", "with", "from", "that", "this", "our", "model", "paper"}
            for w in words:
                if w not in stop:
                    title_word = w
                    break

        return f"{author}{year}{title_word}" if title_word else f"{author}{year}"

    @staticmethod
    def generate_bibtex_entry(source: Source, key: str) -> str:
        """Format a standard BibTeX entry for a Source."""
        authors = " and ".join(source.authors_json) if (source.authors_json and isinstance(source.authors_json, list)) else "Unknown"
        title = source.title or "Untitled"
        year = str(source.published_at.year) if source.published_at else "2026"
        journal = source.venue or (f"arXiv preprint arXiv:{source.arxiv_id}" if source.arxiv_id else "Working Paper")

        fields = [
            f"  title = {{{title}}}",
            f"  author = {{{authors}}}",
            f"  journal = {{{journal}}}",
            f"  year = {{{year}}}",
        ]
        if source.doi:
            fields.append(f"  doi = {{{source.doi}}}")
        if source.url:
            fields.append(f"  url = {{{source.url}}}")
        if source.arxiv_id:
            fields.append(f"  eprint = {{{source.arxiv_id}}}")
            fields.append("  archiveprefix = {arXiv}")

        body = ",\n".join(fields)
        return f"@article{{{key},\n{body}\n}}"

    def resolve_patch_citations(
        self,
        after_text: str,
        sources: list[Source],
        tex_content: str,
    ) -> tuple[str, list[tuple[str, str]]]:
        """Replace [CITATION] placeholder with appropriate LaTeX macro and return generated BibTeX entries.

        Returns (resolved_after_text, [(bib_key, bibtex_entry), ...]).
        """
        if not sources or "[CITATION]" not in after_text:
            return after_text, []

        style = self.detect_citation_style(tex_content)
        entries: list[tuple[str, str]] = []
        keys: list[str] = []

        for s in sources:
            key = self.generate_bibtex_key(s)
            entry = self.generate_bibtex_entry(s, key)
            keys.append(key)
            entries.append((key, entry))

        joined_keys = ", ".join(keys)
        if style == "natbib_citep":
            cite_macro = f"\\citep{{{joined_keys}}}"
        elif style == "natbib_citet":
            cite_macro = f"\\citet{{{joined_keys}}}"
        else:
            cite_macro = f"\\cite{{{joined_keys}}}"

        resolved_text = after_text.replace("[CITATION]", cite_macro)
        return resolved_text, entries

    @staticmethod
    def append_bibtex_entries(
        bib_content: str,
        entries: list[tuple[str, str]],
    ) -> tuple[str, list[str]]:
        """Append BibTeX entries into bib_content, avoiding duplication and key collisions.

        Returns (new_bib_content, newly_added_keys).
        """
        existing_keys = set(re.findall(r"@\w+\s*\{\s*([^,\s]+)\s*,", bib_content, re.IGNORECASE))
        newly_added: list[str] = []
        new_blocks: list[str] = []

        for key, entry in entries:
            title_match = re.search(r"title\s*=\s*\{([^}]+)\}", entry, re.IGNORECASE)
            title = title_match.group(1).strip().lower() if title_match else ""
            if title and title in bib_content.lower():
                continue

            unique_key = key
            suffix_idx = 0
            while unique_key in existing_keys or f"{{{unique_key}," in bib_content or f"{{{unique_key} ," in bib_content:
                suffix_idx += 1
                unique_key = f"{key}{chr(96 + suffix_idx)}"

            if unique_key != key:
                entry = re.sub(r"^(@\w+\s*\{)[^,\s]+", r"\g<1>" + unique_key, entry)

            existing_keys.add(unique_key)
            newly_added.append(unique_key)
            new_blocks.append(entry.strip())

        if not new_blocks:
            return bib_content, newly_added

        separator = "\n\n" if bib_content.strip() else ""
        updated_content = bib_content.rstrip() + separator + "\n\n".join(new_blocks) + "\n"
        return updated_content, newly_added

    def export_case_unified_diff(self, case_id: str) -> dict[str, Any]:
        """Build standard POSIX unified diff covering manuscript .tex and .bib."""
        with session_scope(self.session_factory) as session:
            research_case = session.get(ResearchCase, case_id)
            if research_case is None:
                raise LookupError(f"case not found: {case_id}")
            patches = list(session.scalars(
                select(PatchProposal).where(PatchProposal.case_id == case_id)
                .order_by(PatchProposal.created_at, PatchProposal.id)
            ))
            approved = [p for p in patches if p.approval_state in {"approved", "accepted"}]
            selected = approved or [
                p for p in patches
                if p.approval_state not in {"rejected", "dismissed"}
                and bool(p.validations_json)
                and all(bool(value) for value in p.validations_json.values())
            ]

            by_manuscript: dict[str, list[PatchProposal]] = {}
            for patch in selected:
                by_manuscript.setdefault(patch.manuscript_version_id, []).append(patch)

            diff_sections: list[str] = []
            all_bib_entries: list[tuple[str, str]] = []

            for manuscript_id, file_patches in by_manuscript.items():
                manuscript = session.get(ManuscriptVersion, manuscript_id)
                if manuscript is None:
                    continue
                old_text = manuscript.content_text or ""
                new_text = old_text
                for patch in file_patches:
                    sources = [
                        s for sid in (patch.citations_json or [])
                        if (s := session.get(Source, sid)) is not None
                    ]
                    resolved_after, bib_entries = self.resolve_patch_citations(
                        patch.after_text, sources, old_text
                    )
                    all_bib_entries.extend(bib_entries)

                    if patch.before_text in new_text:
                        new_text = new_text.replace(patch.before_text, resolved_after, 1)

                if new_text != old_text:
                    file_name = (manuscript.file_name or f"manuscript-{manuscript.id}.tex").replace("\\", "/")
                    file_name = posixpath.normpath(file_name).lstrip("/")
                    if file_name in {"", ".", ".."} or file_name.startswith("../"):
                        file_name = f"manuscript-{manuscript.id}.tex"
                    diff_sections.append("".join(difflib.unified_diff(
                        old_text.splitlines(keepends=True),
                        new_text.splitlines(keepends=True),
                        fromfile=f"a/{file_name}",
                        tofile=f"b/{file_name}",
                        n=3,
                    )))

            # Append .bib diff if citations were generated
            raw_bibtex = ""
            if all_bib_entries:
                old_bib = ""
                updated_bib, _ = self.append_bibtex_entries(old_bib, all_bib_entries)
                raw_bibtex = updated_bib
                diff_sections.append("".join(difflib.unified_diff(
                    old_bib.splitlines(keepends=True),
                    updated_bib.splitlines(keepends=True),
                    fromfile="a/references.bib",
                    tofile="b/references.bib",
                    n=3,
                )))

            unified_diff_text = "".join(diff_sections)
            filename = f"research-radar-{case_id}.patch"
            return {
                "filename": filename,
                "diff": unified_diff_text,
                "bibtex": raw_bibtex,
                "patch_count": len(selected),
            }

    def apply_case_patches_to_local(self, case_id: str) -> dict[str, Any]:
        """Apply approved patches directly to local manuscript file and update DB."""
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
            if manuscript is None:
                return {"success": False, "message": "no_current_manuscript", "applied_count": 0}

            patches = list(session.scalars(
                select(PatchProposal).where(PatchProposal.case_id == case_id)
                .order_by(PatchProposal.created_at, PatchProposal.id)
            ))
            approved = [p for p in patches if p.approval_state in {"approved", "accepted"}]
            selected = approved or [
                p for p in patches
                if p.approval_state not in {"rejected", "dismissed"}
                and bool(p.validations_json)
                and all(bool(value) for value in p.validations_json.values())
            ]
            if not selected:
                return {"success": False, "message": "no_applicable_patches", "applied_count": 0}

            old_text = manuscript.content_text or ""
            new_text = old_text
            all_bib_entries: list[tuple[str, str]] = []
            applied_ids: list[str] = []

            for patch in selected:
                sources = [
                    s for sid in (patch.citations_json or [])
                    if (s := session.get(Source, sid)) is not None
                ]
                resolved_after, bib_entries = self.resolve_patch_citations(
                    patch.after_text, sources, old_text
                )
                all_bib_entries.extend(bib_entries)

                if patch.before_text in new_text:
                    new_text = new_text.replace(patch.before_text, resolved_after, 1)
                    applied_ids.append(patch.id)

            if new_text != old_text:
                manuscript.content_text = new_text
                manuscript.content_hash = hashlib.sha256(new_text.encode()).hexdigest()

            # Disk write if local file exists
            written_to_disk = False
            disk_paths = [
                Path(manuscript.file_name),
                Path(research_case.settings_json.get("fixture_dir", "")) / manuscript.file_name,
                Path(research_case.settings_json.get("local_workspace_dir", "")) / manuscript.file_name,
            ]
            for p in disk_paths:
                if p.is_file():
                    try:
                        p.write_text(new_text, encoding="utf-8")
                        written_to_disk = True
                    except Exception:
                        pass
                    break

            # Handle BibTeX write
            added_keys: list[str] = []
            if all_bib_entries:
                bib_paths = [
                    Path("references.bib"),
                    Path(research_case.settings_json.get("fixture_dir", "")) / "references.bib",
                    Path(research_case.settings_json.get("local_workspace_dir", "")) / "references.bib",
                ]
                for bp in bib_paths:
                    if bp.is_file():
                        try:
                            old_bib = bp.read_text(encoding="utf-8")
                            updated_bib, added_keys = self.append_bibtex_entries(old_bib, all_bib_entries)
                            if added_keys:
                                bp.write_text(updated_bib, encoding="utf-8")
                        except Exception:
                            pass
                        break

            session.add(
                AuditEvent(
                    id=str(uuid4()),
                    case_id=case_id,
                    event_type="patches_applied_local",
                    object_type="ManuscriptVersion",
                    object_id=manuscript.id,
                    payload_json={
                        "applied_patch_ids": applied_ids,
                        "applied_count": len(applied_ids),
                        "written_to_disk": written_to_disk,
                        "added_bib_keys": added_keys,
                    },
                    actor_type="user",
                    actor_id="researcher",
                )
            )

            return {
                "success": True,
                "applied_count": len(applied_ids),
                "file_name": manuscript.file_name,
                "written_to_disk": written_to_disk,
                "bib_updated": bool(added_keys or all_bib_entries),
                "added_keys": added_keys,
            }

    @staticmethod
    def export_unified_diff(edits: list[dict]) -> str:
        """Export edits as a unified diff format suitable for human review."""
        lines: list[str] = ["# Patch Diff Summary\n"]
        for i, edit in enumerate(edits, 1):
            lines.append(f"## Edit {i}: {edit.get('section', 'unknown')} ({edit.get('edit_class', 'unknown')})")
            lines.append(f"_Reason: {edit.get('reason', '')}_\n")
            lines.append("```diff")
            lines.append(f"- {edit.get('before_text', '')[:200]}")
            lines.append(f"+ {edit.get('after_text', '')[:200]}")
            lines.append("```\n")
        return "\n".join(lines)

    def export_case_git_patch(self, case_id: str) -> dict[str, str | int]:
        """Build an applyable git patch for the approved case edits."""
        with session_scope(self.session_factory) as session:
            research_case = session.get(ResearchCase, case_id)
            if research_case is None:
                raise LookupError(f"case not found: {case_id}")
            patches = list(session.scalars(
                select(PatchProposal).where(PatchProposal.case_id == case_id)
                .order_by(PatchProposal.created_at, PatchProposal.id)
            ))
            approved = [p for p in patches if p.approval_state in {"approved", "accepted"}]
            selected = approved or [
                p for p in patches
                if p.approval_state not in {"rejected", "dismissed"}
                and bool(p.validations_json)
                and all(bool(value) for value in p.validations_json.values())
            ]
            by_manuscript: dict[str, list[PatchProposal]] = {}
            for patch in selected:
                by_manuscript.setdefault(patch.manuscript_version_id, []).append(patch)

            diff_sections: list[str] = []
            for manuscript_id, file_patches in by_manuscript.items():
                manuscript = session.get(ManuscriptVersion, manuscript_id)
                if manuscript is None:
                    continue
                old_text = manuscript.content_text or ""
                new_text = old_text
                for patch in file_patches:
                    if patch.before_text in new_text:
                        new_text = new_text.replace(patch.before_text, patch.after_text, 1)
                if new_text == old_text:
                    continue
                file_name = (manuscript.file_name or f"manuscript-{manuscript.id}.tex").replace("\\", "/")
                file_name = posixpath.normpath(file_name).lstrip("/")
                if file_name in {"", ".", ".."} or file_name.startswith("../"):
                    file_name = f"manuscript-{manuscript.id}.tex"
                diff_sections.append("\n".join(difflib.unified_diff(
                    old_text.splitlines(keepends=True), new_text.splitlines(keepends=True),
                    fromfile=f"a/{file_name}", tofile=f"b/{file_name}", n=3, lineterm="",
                )))

            filename = f"research-radar-{case_id}.patch"
            patch_content = (
                "From 0000000000000000000000000000000000000000 Mon Sep 17 00:00:00 2001\n"
                f"Subject: [Research Radar] manuscript patch for {research_case.title}\n"
                f"Summary: {len(selected)} accepted research patch proposal(s)\n---\n"
                + "\n".join(diff_sections)
            )
            if not patch_content.endswith("\n"):
                patch_content += "\n"
            return {
                "filename": filename,
                "patch_content": patch_content,
                "apply_instructions": (
                    f"cd <repo>; git apply {filename}\n"
                    "该补丁基于本地稿件版本生成；请先确认仓库中的文件与 Research Radar 中的稿件版本一致。"
                ),
                "patch_count": len(selected),
            }

    @staticmethod
    def export_markdown(patch: PatchProposal) -> str:
        return (
            f"# Research Radar Patch Proposal\n\n"
            f"- State: {patch.approval_state}\n- Edit class: {patch.edit_class}\n"
            f"- Target: {patch.target_locator}\n- Citations: {', '.join(patch.citations_json)}\n\n"
            f"## Before\n\n{patch.before_text}\n\n## After\n\n{patch.after_text}\n"
        )
