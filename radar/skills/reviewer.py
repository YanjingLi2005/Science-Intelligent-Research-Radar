"""Paper reviewer skill: evaluates manuscript against incoming evidence."""

from pydantic import BaseModel, Field

from radar.skills.base import SkillOutput, _skill_prompt


class ReviewDimension(BaseModel):
    name: str = Field(description="Dimension: novelty, soundness, clarity, significance, evidence_strength")
    score: int = Field(ge=1, le=5, description="1-5 score")
    comment: str = Field(description="Specific evidence-based feedback")


class RiskAssessment(BaseModel):
    risk_type: str = Field(description="Type: claim_invalidated, prior_art, competitor, boundary_issue, integrity_concern")
    severity: str = Field(description="critical / review / informative")
    description: str = Field(description="What could go wrong and why")
    recommendation: str = Field(description="Actionable mitigation")


class ReviewerOutput(BaseModel):
    overall_score: int = Field(ge=1, le=5, description="Overall paper quality score")
    dimensions: list[ReviewDimension]
    risks: list[RiskAssessment] = Field(default_factory=list)
    action_priorities: list[str] = Field(default_factory=list, description="Prioritized list of suggested actions")


class ReviewerSkill:
    name = "ccf_paper_reviewer"
    description = "Review manuscript for scientific soundness, novelty risk, and submission readiness."
    triggers = ["review", "assess_risk", "evaluate_impact", "submission_readiness", "novelty_check", "team_decision"]
    not_for = ["text_writing", "experiment_execution", "format_check"]

    def can_handle(self, request: dict) -> bool:
        action = request.get("action_type", "")
        return action in self.triggers and action not in self.not_for

    def execute(self, request: dict, *, project_state: dict) -> SkillOutput:
        prompt = _skill_prompt("paper_reviewer", {
            "project": {
                "title": project_state.get("title", ""),
                "research_question": project_state.get("research_question", ""),
            },
            "manuscript_summary": project_state.get("manuscript_summary", {}),
            "confirmed_impacts": request.get("confirmed_impacts", []),
            "competitor_papers": request.get("competitor_papers", []),
            "focus": request.get("focus", "assess impact on submission readiness"),
        })
        registry = request.get("registry")
        if registry is None or registry.llm_client is None:
            return SkillOutput(
                skill_name=self.name,
                artifact_type="review_report",
                content={"overall_score": 3, "dimensions": [], "risks": [], "action_priorities": []},
                warnings=["llm_not_configured"],
            )
        output, meta_data = registry._llm_skill(
            "skill_paper_reviewer",
            prompt,
            ReviewerOutput,
            project_state.get("case_id", "unknown"),
            artifact_type="paper_review",
            skill_name=self.name,
        )
        return SkillOutput(
            skill_name=self.name,
            artifact_type="review_report",
            content=output,
            warnings=[],
            handoff_suggestions=["ccf_paper_writer"] if output.get("action_priorities") else [],
        )
