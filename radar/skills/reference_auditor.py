"""Reference auditor skill: checks citations, DOI plausibility, duplicates,
format consistency, and self-citation signals."""

from pydantic import BaseModel, Field

from radar.llm.text_utils import truncate_for_prompt
from radar.skills.base import SkillOutput, _skill_prompt


class ReferenceIssue(BaseModel):
    citation_marker: str = Field(description="Citation marker or reference entry")
    issue: str = Field(
        description=(
            "missing_doi|invalid_doi|duplicate|wrong_claim|retracted|"
            "format_inconsistent|self_citation|other"
        )
    )
    fix_suggestion: str = Field(description="Concrete fix")


class ReferenceAuditOutput(BaseModel):
    reference_count: int = Field(ge=0, description="Number of references found")
    issues: list[ReferenceIssue] = Field(default_factory=list)
    summary: str = Field(description="Short audit summary")
    integrity_score: float = Field(
        ge=0.0, le=1.0, description="Reference-level integrity score"
    )


class ReferenceAuditSkill:
    name = "ccf_reference_auditor"
    description = "Audit citations and references: DOI plausibility, duplicates, format, self-citation."
    triggers = ["audit_references", "check_references", "citation_audit", "reference_check"]
    not_for = ["text_writing", "experiment_design", "literature_search"]

    def can_handle(self, request: dict) -> bool:
        action = request.get("action_type", "")
        return action in self.triggers and action not in self.not_for

    def execute(self, request: dict, *, project_state: dict) -> SkillOutput:
        manuscript = project_state.get("manuscript", {}).get("content", "")
        prompt = _skill_prompt("reference_auditor", {
            "manuscript_tail": truncate_for_prompt(
                manuscript[-40_000:],
                max_chars=40_000,
                purpose="reference audit",
            ),
            "confirmed_impacts": request.get("confirmed_impacts", []),
        })

        registry = request.get("registry")
        if registry is None or registry.llm_client is None:
            return SkillOutput(
                skill_name=self.name,
                artifact_type="reference_audit",
                content={
                    "reference_count": 0,
                    "issues": [],
                    "summary": "LLM 未配置，无法执行引用审计。",
                    "integrity_score": 1.0,
                },
                warnings=["llm_not_configured"],
            )

        output, _ = registry._llm_skill(
            "skill_reference_auditor",
            prompt,
            ReferenceAuditOutput,
            project_state.get("case_id", "unknown"),
            artifact_type="reference_audit",
            skill_name=self.name,
        )
        return SkillOutput(
            skill_name=self.name,
            artifact_type="reference_audit",
            content=output,
            warnings=[],
            handoff_suggestions=["ccf_paper_writer"] if output.get("issues") else [],
        )
