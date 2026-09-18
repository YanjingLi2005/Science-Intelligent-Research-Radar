"""One-time full-manuscript understanding with a reusable structured profile."""

import hashlib
import json
import time
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from radar.config import Settings, estimate_llm_cost_usd, get_settings
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
)
from radar.schemas import ManuscriptUnderstandingOutput


PROMPT_PATH = (
    Path(__file__).parents[1] / "llm" / "prompts" / "manuscript_understanding.txt"
)


class ManuscriptUnderstandingService:
    def __init__(
        self,
        session_factory: sessionmaker[Session] = SessionLocal,
        *,
        llm_client: LLMClient | None = None,
        settings: Settings | None = None,
    ):
        self.session_factory = session_factory
        self.settings = settings or get_settings()
        profile_request_settings = self.settings.model_copy(
            update={
                "llm_thinking": "disabled",
                "llm_max_tokens": max(self.settings.llm_max_tokens, 8192),
            }
        )
        # Profile requests disable thinking and need a larger token budget;
        # the factory still decides local-first vs remote on these settings.
        # No unconditional ProviderLLMClient fallback: with no LLM configured
        # the deterministic profile path applies (None) and the route reports
        # "LLM not configured" instead of a RuntimeError 500.
        self.llm_client = llm_client or build_analysis_llm(
            profile_request_settings,
            timeout_seconds=self.settings.llm_manuscript_timeout_seconds,
        )

    def analyze(self, case_id: str) -> tuple[str, ManuscriptUnderstandingOutput]:
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
                raise ValueError("manuscript_missing")
            rows = list(
                session.execute(
                    select(Claim, ClaimRevision)
                    .join(ClaimRevision, ClaimRevision.claim_id == Claim.id)
                    .where(
                        Claim.case_id == case_id,
                        ClaimRevision.review_state == "confirmed",
                        ClaimRevision.manuscript_version_id == manuscript.id,
                    )
                    .order_by(Claim.stable_key)
                )
            )
            if not rows:
                raise ValueError("confirmed_claim_required")
            manuscript_id = manuscript.id
            manuscript_text = manuscript.content_text
            input_refs = [manuscript.id, *[revision.id for _, revision in rows]]
            confirmed_claims = [
                {
                    "stable_key": claim.stable_key,
                    "statement": revision.statement,
                    "centrality": revision.centrality,
                    "source_locator": revision.source_locator,
                }
                for claim, revision in rows
            ]

        instructions = PROMPT_PATH.read_text(encoding="utf-8").strip()
        payload = {
            "case": {
                "title": research_case.title,
                "research_question": research_case.research_question,
                "field": research_case.field,
            },
            "confirmed_claims": confirmed_claims,
            "full_manuscript": truncate_for_prompt(
                manuscript_text,
                purpose="full manuscript background",
            ),
        }
        prompt = (
            f"{instructions}\n\nINPUT JSON — FULL MANUSCRIPT INCLUDED:\n"
            f"{json.dumps(payload, ensure_ascii=False, indent=2)}"
        )
        if self.llm_client is None:
            # No LLM configured: report 400, never AttributeError 500.
            raise ValueError("llm_not_configured")
        started = time.perf_counter()
        expected_keys = {item["stable_key"] for item in confirmed_claims}
        output = None
        returned_keys: set[str] = set()
        missing: list[str] = []
        extra: list[str] = []
        active_prompt = prompt
        for attempt in range(2):
            output = self.llm_client.generate_structured(
                stage="manuscript_understanding",
                prompt=active_prompt,
                response_model=ManuscriptUnderstandingOutput,
            )
            returned_keys = {item.stable_key for item in output.claim_profiles}
            if returned_keys == expected_keys:
                break
            missing = sorted(expected_keys - returned_keys)
            extra = sorted(returned_keys - expected_keys)
            if attempt == 0:
                active_prompt = (
                    f"{prompt}\n\nYOUR PREVIOUS OUTPUT WAS BLOCKED BY PROGRAMMATIC "
                    f"CHECKS: claim_profiles must cover exactly the confirmed claim "
                    f"stable_keys {sorted(expected_keys)}; missing={missing}; "
                    f"unexpected={extra}. Generate a corrected profile."
                )
        if output is None or returned_keys != expected_keys:
            raise ValueError(
                f"manuscript_profile_claim_keys_invalid:missing={missing}:extra={extra}"
            )
        latency_ms = int((time.perf_counter() - started) * 1000)
        enriched_keys = self.enrich_claim_contracts(case_id, output)

        receipt = getattr(self.llm_client, "last_receipt", {}) or {}
        usage = receipt.get("usage") or {}
        raw_response = receipt.get("raw_response") or output.model_dump_json()
        input_tokens = int(usage.get("prompt_tokens", 0))
        output_tokens = int(usage.get("completion_tokens", 0))
        model_name = self.settings.llm_model or "injected-test-model"
        model_run_id = str(uuid4())
        with session_scope(self.session_factory) as session:
            session.add(
                ModelRun(
                    id=model_run_id,
                    stage="manuscript_understanding",
                    case_id=case_id,
                    provider=self.settings.llm_provider
                    or self.llm_client.__class__.__name__,
                    model=model_name,
                    prompt_hash=hashlib.sha256(prompt.encode()).hexdigest(),
                    schema_version="ManuscriptUnderstandingOutput.v1",
                    input_refs_json=input_refs,
                    raw_response=raw_response,
                    parsed_output_json=output.model_dump(),
                    validation_json={
                        "pydantic": True,
                        "all_confirmed_claims_profiled": True,
                        "full_manuscript_included": True,
                    },
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    estimated_cost=estimate_llm_cost_usd(
                        model_name, input_tokens, output_tokens
                    ),
                    latency_ms=int(receipt.get("latency_ms", latency_ms)),
                )
            )
            session.add(
                AuditEvent(
                    id=str(uuid4()),
                    case_id=case_id,
                    event_type="manuscript_understanding_completed",
                    object_type="ManuscriptVersion",
                    object_id=manuscript_id,
                    payload_json={
                        "model_run_id": model_run_id,
                        "confirmed_claims": len(confirmed_claims),
                        "full_manuscript_chars": len(manuscript_text),
                        "enriched_claim_contracts": enriched_keys,
                    },
                    actor_type="system",
                    actor_id="manuscript_understanding_service",
                )
            )
        return model_run_id, output

    def enrich_claim_contracts(
        self,
        case_id: str,
        profile: ManuscriptUnderstandingOutput,
    ) -> list[str]:
        """Fill blank deterministic contracts from the full-manuscript profile."""

        profile_by_key = {item.stable_key: item for item in profile.claim_profiles}
        enriched: list[str] = []
        with session_scope(self.session_factory) as session:
            manuscript = session.scalar(
                select(ManuscriptVersion).where(
                    ManuscriptVersion.case_id == case_id,
                    ManuscriptVersion.is_current.is_(True),
                )
            )
            rows = list(
                session.execute(
                    select(Claim, ClaimRevision)
                    .join(ClaimRevision, ClaimRevision.claim_id == Claim.id)
                    .where(
                        Claim.case_id == case_id,
                        ClaimRevision.review_state == "confirmed",
                        ClaimRevision.manuscript_version_id == manuscript.id
                        if manuscript
                        else False,
                    )
                )
            )
            for claim, revision in rows:
                claim_profile = profile_by_key.get(claim.stable_key)
                if claim_profile is None or any(revision.contract_json.values()):
                    continue
                revision.contract_json = claim_profile.contract.model_dump()
                enriched.append(claim.stable_key)
            if enriched:
                session.add(
                    AuditEvent(
                        id=str(uuid4()),
                        case_id=case_id,
                        event_type="claim_contracts_enriched",
                        object_type="ResearchCase",
                        object_id=case_id,
                        payload_json={"stable_keys": sorted(enriched)},
                        actor_type="system",
                        actor_id="manuscript_understanding_service",
                    )
                )
        return sorted(enriched)

    @classmethod
    def latest_profile(
        cls,
        case_id: str,
        session_factory: sessionmaker[Session] | None = None,
    ) -> ManuscriptUnderstandingOutput | None:
        """Return the newest manuscript-understanding profile recorded for a case."""
        active_factory = session_factory or SessionLocal
        with session_scope(active_factory) as session:
            run = session.scalar(
                select(ModelRun)
                .where(
                    ModelRun.stage == "manuscript_understanding",
                    ModelRun.case_id == case_id,
                )
                .order_by(ModelRun.created_at.desc())
                .limit(1)
            )
            if run is None:
                return None
            return ManuscriptUnderstandingOutput.model_validate(
                run.parsed_output_json
            )
