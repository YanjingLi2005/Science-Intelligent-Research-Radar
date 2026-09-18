"""Skill registry: routing requests to the correct owner skill."""

import hashlib
from uuid import uuid4

from sqlalchemy.orm import Session, sessionmaker

from radar.db import SessionLocal, session_scope
from radar.llm.base import LLMClient
from radar.models import AuditEvent, ModelRun
from radar.skills.base import SkillOutput, SkillProtocol, _skill_prompt


class SkillRegistry:
    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session] = SessionLocal,
        llm_client: LLMClient | None = None,
    ):
        self.session_factory = session_factory
        self.llm_client = llm_client
        self._skills: dict[str, SkillProtocol] = {}

    def register(self, skill: SkillProtocol) -> None:
        self._skills[skill.name] = skill

    def route(self, request: dict) -> str | None:
        for name, skill in self._skills.items():
            if skill.can_handle(request):
                return name
        return None

    def execute(self, skill_name: str, request: dict, *, project_state: dict) -> SkillOutput:
        skill = self._skills.get(skill_name)
        if skill is None:
            raise KeyError(f"unknown skill: {skill_name}")
        return skill.execute(request, project_state=project_state)

    def can_handle(self, skill_name: str, request: dict) -> bool:
        """True when the named skill declares it handles the request."""
        skill = self._skills.get(skill_name)
        if skill is None:
            return False
        try:
            return bool(skill.can_handle(request))
        except Exception:
            return False

    def names(self) -> set[str]:
        """Registered skill names."""
        return set(self._skills)

    def _llm_skill(
        self,
        stage: str,
        prompt_text: str,
        response_model: type,
        case_id: str,
        *,
        artifact_type: str,
        skill_name: str,
    ) -> tuple[dict, dict]:
        if self.llm_client is None:
            raise RuntimeError("llm_not_configured")
        import time
        started = time.perf_counter()
        output = self.llm_client.generate_structured(
            stage=stage,
            prompt=prompt_text,
            response_model=response_model,
        )
        latency_ms = int((time.perf_counter() - started) * 1000)
        receipt = getattr(self.llm_client, "last_receipt", {}) or {}
        usage = receipt.get("usage") or {}
        run_meta = {
            "provider": getattr(self.llm_client, "provider_name", "unknown"),
            "model": getattr(self.llm_client, "model_name", "unknown"),
            "input_tokens": int(usage.get("prompt_tokens", 0)),
            "output_tokens": int(usage.get("completion_tokens", 0)),
            "latency_ms": latency_ms,
            "prompt_hash": hashlib.sha256(prompt_text.encode()).hexdigest(),
        }
        # Every skill execution leaves the same ModelRun + AuditEvent trail
        # as the rest of the pipeline — never a silent call.
        try:
            with session_scope(self.session_factory) as session:
                self.record_skill_run(
                    session,
                    case_id=case_id,
                    skill_name=skill_name,
                    artifact_type=artifact_type,
                    prompt_text=prompt_text,
                    output=output.model_dump(),
                    run_meta=run_meta,
                )
        except Exception:
            # Persistence must never mask a successful skill result.
            pass
        return output.model_dump(), run_meta

    def record_skill_run(
        self,
        session: Session,
        case_id: str,
        skill_name: str,
        artifact_type: str,
        prompt_text: str,
        output: dict,
        run_meta: dict,
    ) -> None:
        session.add(
            ModelRun(
                id=str(uuid4()),
                stage=f"skill_{skill_name}",
                case_id=case_id,
                provider=run_meta["provider"],
                model=run_meta["model"],
                prompt_hash=run_meta["prompt_hash"],
                schema_version=f"{artifact_type}.v1",
                input_refs_json=[],
                raw_response=output.get("__raw", ""),
                parsed_output_json=output,
                validation_json={},
                input_tokens=run_meta["input_tokens"],
                output_tokens=run_meta["output_tokens"],
                latency_ms=run_meta["latency_ms"],
            )
        )
        session.add(
            AuditEvent(
                id=str(uuid4()),
                case_id=case_id,
                event_type=f"skill_{skill_name}_executed",
                object_type=artifact_type,
                object_id=skill_name,
                payload_json={"artifact": artifact_type},
                actor_type="model",
                actor_id=skill_name,
            )
        )


_registry: SkillRegistry | None = None


def get_registry() -> SkillRegistry:
    """Return the process-wide registry with the CCF-A skills registered
    exactly once (import-time lazy), so callers never re-register per request."""
    global _registry
    if _registry is None:
        from radar.skills.auditor import AuditorSkill
        from radar.skills.experiment import ExperimentSkill
        from radar.skills.rebuttal import RebuttalSkill
        from radar.skills.reference_auditor import ReferenceAuditSkill
        from radar.skills.reviewer import ReviewerSkill
        from radar.skills.writer import WriterSkill

        _registry = SkillRegistry()
        for skill in (
            WriterSkill(),
            ReviewerSkill(),
            AuditorSkill(),
            ExperimentSkill(),
            RebuttalSkill(),
            ReferenceAuditSkill(),
        ):
            _registry.register(skill)
    return _registry
