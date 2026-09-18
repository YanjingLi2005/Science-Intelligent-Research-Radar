"""Paper writing skill: targeted before/after edits with human confirmation."""

from pydantic import BaseModel, Field

from radar.llm.text_utils import truncate_for_prompt
from radar.skills.base import SkillOutput, _skill_prompt


class WriterEdit(BaseModel):
    section: str
    edit_class: str
    before_text: str
    after_text: str
    reason: str


class WriterOutput(BaseModel):
    edits: list[WriterEdit] = Field(default_factory=list)
    not_adopted: list[str] = Field(default_factory=list)


class WriterSkill:
    name = "ccf_paper_writer"
    description = "基于外部论文影响做最小 before/after 修改，人工确认"
    triggers = ["writing", "cite", "add_boundary_discussion", "narrow_claim", "competitor_response"]

    def can_handle(self, request: dict) -> bool:
        return request.get("action_type", "") in self.triggers

    def execute(self, request: dict, *, project_state: dict) -> SkillOutput:
        manuscript = project_state.get("manuscript", {}).get("content", "")
        prompt = _skill_prompt("paper_writer", {
            # Truncation is marked explicitly so edits in the tail of a long
            # manuscript are not silently invisible to the writer.
            "manuscript": truncate_for_prompt(
                manuscript, max_chars=20_000, purpose="skill paper writer"
            ),
            "confirmed_impacts": request.get("confirmed_impacts", []),
        })

        registry = request.get("registry")
        if registry is None or registry.llm_client is None:
            return SkillOutput(
                skill_name=self.name,
                artifact_type="manuscript_edits",
                content={"edits": [], "not_adopted": []},
                warnings=["llm_not_configured"],
            )

        output, _ = registry._llm_skill(
            "skill_paper_writer",
            prompt,
            WriterOutput,
            project_state.get("case_id", "unknown"),
            artifact_type="manuscript_edits",
            skill_name=self.name,
        )
        return SkillOutput(
            skill_name=self.name,
            artifact_type="manuscript_edits",
            content=output,
            handoff_suggestions=["ccf_integrity_auditor"] if output.get("edits") else [],
        )
