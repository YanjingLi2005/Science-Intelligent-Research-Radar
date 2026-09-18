"""Integrity auditor: verifies claim, citation, number, and figure consistency."""

from pydantic import BaseModel, Field

from radar.skills.base import SkillOutput, _skill_prompt


class ClaimSupport(BaseModel):
    claim_stable_key: str = Field(description="Claim stable key from manuscript")
    support_level: str = Field(description="verified / partial / unverified / contradictory")
    evidence_gaps: list[str] = Field(default_factory=list)
    required_actions: list[str] = Field(default_factory=list)


class CitationIssue(BaseModel):
    citation_marker: str = Field(description="The citation text or tag from the manuscript")
    issue: str = Field(description="What is wrong: missing, wrong_claim, retracted, not_applicable")
    fix_suggestion: str = Field(description="How to fix it")


class NumberConsistency(BaseModel):
    location: str = Field(description="Section or claim where the number appears")
    manuscript_value: str
    evidence_value: str
    discrepancy: str = Field(description="Direction and magnitude of difference")


class AuditorOutput(BaseModel):
    claim_support: list[ClaimSupport] = Field(default_factory=list)
    citation_issues: list[CitationIssue] = Field(default_factory=list)
    number_issues: list[NumberConsistency] = Field(default_factory=list)
    integrity_score: float = Field(ge=0.0, le=1.0, description="Overall integrity score")
    critical_flags: list[str] = Field(default_factory=list)


class AuditorSkill:
    name = "ccf_integrity_auditor"
    description = "Verify claim support, citation accuracy, number consistency, and evidence alignment."
    triggers = ["audit", "verify_claims", "check_citations", "check_numbers", "integrity_check", "audit_integrity", "revalidation"]
    not_for = ["literature_search", "text_writing", "experiment_design"]

    def can_handle(self, request: dict) -> bool:
        action = request.get("action_type", "")
        return action in self.triggers and action not in self.not_for

    def execute(self, request: dict, *, project_state: dict) -> SkillOutput:
        prompt = _skill_prompt("integrity_auditor", {
            "project": {
                "title": project_state.get("title", ""),
            },
            "manuscript": project_state.get("manuscript", {}),
            "confirmed_claims": project_state.get("claims", []),
            "confirmed_impacts": request.get("confirmed_impacts", []),
            "patches_applied": request.get("applied_patches", []),
        })
        registry = request.get("registry")
        if registry is None or registry.llm_client is None:
            return SkillOutput(
                skill_name=self.name,
                artifact_type="integrity_report",
                content={"claim_support": [], "citation_issues": [], "number_issues": [], "integrity_score": 1.0, "critical_flags": []},
                warnings=["llm_not_configured"],
            )
        output, meta_data = registry._llm_skill(
            "skill_integrity_auditor",
            prompt,
            AuditorOutput,
            project_state.get("case_id", "unknown"),
            artifact_type="integrity_audit",
            skill_name=self.name,
        )
        return SkillOutput(
            skill_name=self.name,
            artifact_type="integrity_report",
            content=output,
            warnings=[],
            handoff_suggestions=["ccf_paper_writer"] if output.get("citation_issues") else [],
        )
