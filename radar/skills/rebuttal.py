"""Rebuttal writer: generates reviewer responses and revision plans."""

from pydantic import BaseModel, Field

from radar.skills.base import SkillOutput, _skill_prompt


class RebuttalPoint(BaseModel):
    reviewer_comment_summary: str = Field(description="Summarized reviewer concern")
    response: str = Field(description="Polite, evidence-backed response")
    manuscript_change: str = Field(description="What to change in the manuscript")
    new_evidence: str = Field(default="", description="New experiment or citation to add")


class RevisionEntry(BaseModel):
    action_id: str = Field(description="Linked to action item")
    change_description: str
    section_affected: str
    status: str = Field(default="planned", description="planned / in_progress / completed")


class RebuttalOutput(BaseModel):
    rebuttal_points: list[RebuttalPoint]
    revision_plan: list[RevisionEntry] = Field(default_factory=list)
    strength_assessment: str = Field(description="Overall rebuttal strength: strong / adequate / weak")


class RebuttalSkill:
    name = "ccf_rebuttal_writer"
    description = "Write rebuttals, response letters, and revision ledgers for reviewer feedback."
    triggers = ["rebuttal", "respond_to_reviewer", "revision_plan", "response_letter", "competitor_response"]
    not_for = ["initial_drafting", "literature_search", "experiment_design"]

    def can_handle(self, request: dict) -> bool:
        action = request.get("action_type", "")
        return action in self.triggers and action not in self.not_for

    def execute(self, request: dict, *, project_state: dict) -> SkillOutput:
        prompt = _skill_prompt("rebuttal_writer", {
            "project": {
                "title": project_state.get("title", ""),
                "research_question": project_state.get("research_question", ""),
            },
            "manuscript_summary": project_state.get("manuscript_summary", {}),
            "impacts_as_reviewer_concerns": request.get("confirmed_impacts", []),
            "actions_taken": request.get("completed_actions", []),
        })
        registry = request.get("registry")
        if registry is None or registry.llm_client is None:
            return SkillOutput(
                skill_name=self.name,
                artifact_type="rebuttal",
                content={"rebuttal_points": [], "revision_plan": [], "strength_assessment": "weak"},
                warnings=["llm_not_configured"],
            )
        output, meta_data = registry._llm_skill(
            "skill_rebuttal_writer",
            prompt,
            RebuttalOutput,
            project_state.get("case_id", "unknown"),
            artifact_type="rebuttal_letter",
            skill_name=self.name,
        )
        return SkillOutput(
            skill_name=self.name,
            artifact_type="rebuttal",
            content=output,
            warnings=[],
            handoff_suggestions=["ccf_paper_writer"] if output.get("revision_plan") else [],
        )
