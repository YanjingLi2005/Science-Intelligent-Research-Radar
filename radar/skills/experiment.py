"""Experiment designer: designs baseline, ablation, and comparison experiments."""

from pydantic import BaseModel, Field

from radar.skills.base import SkillOutput, _skill_prompt


class BaselineConfig(BaseModel):
    name: str
    description: str
    why_comparable: str = Field(description="Why this baseline is comparable to the manuscript claim")


class MetricConfig(BaseModel):
    name: str
    description: str
    primary: bool = True


class StatisticalDesign(BaseModel):
    """Design guardrails that make an experiment executable and falsifiable."""

    sample_size: str = Field(default="", description="Required number of examples, subjects, runs, or seeds")
    power_target: str = Field(default="", description="Target statistical power or why power is not applicable")
    effect_size: str = Field(default="", description="Minimum practically meaningful effect")
    confidence_interval: str = Field(default="", description="Uncertainty interval or reporting rule")
    randomization: str = Field(default="", description="Randomization, blocking, or split protocol")
    seeds: str = Field(default="", description="Number and policy for random seeds")
    analysis_plan: str = Field(default="", description="Pre-specified comparison and aggregation plan")
    unit_checks: list[str] = Field(default_factory=list, description="Units, denominators, or measurement checks")


class ExperimentProtocol(BaseModel):
    title: str = Field(description="Experiment name, max 30 Chinese chars")
    goal: str = Field(description="What this experiment aims to verify")
    baselines: list[BaselineConfig] = Field(default_factory=list)
    metrics: list[MetricConfig] = Field(default_factory=list)
    hypothesis: str = Field(description="Expected outcome if the manuscript claim holds")
    falsification: str = Field(description="What result would challenge the manuscript claim")
    estimated_effort: str = Field(description="hours / days / weeks")
    statistical_design: StatisticalDesign = Field(default_factory=StatisticalDesign)
    execution_checklist: list[str] = Field(default_factory=list)


class ResultTableSpec(BaseModel):
    table_caption: str = Field(description="Table caption for the paper")
    rows: list[str] = Field(description="Row labels (method names)")
    columns: list[str] = Field(description="Column headers (metric names)")


class ExperimentOutput(BaseModel):
    experiments: list[ExperimentProtocol]
    result_table_specs: list[ResultTableSpec] = Field(default_factory=list)
    evidence_requirements: list[str] = Field(default_factory=list)


class ExperimentSkill:
    name = "ccf_experiment_designer"
    description = "Design experiments, baselines, metrics, and result tables from claims and impacts."
    triggers = ["experiment", "design_experiment", "run_comparison", "add_baseline", "revalidate", "data"]
    not_for = ["text_writing", "citation_search", "submission_check"]

    def can_handle(self, request: dict) -> bool:
        action = request.get("action_type", "")
        return action in self.triggers and action not in self.not_for

    def execute(self, request: dict, *, project_state: dict) -> SkillOutput:
        prompt = _skill_prompt("experiment_designer", {
            "project": {
                "title": project_state.get("title", ""),
                "research_question": project_state.get("research_question", ""),
            },
            "manuscript_claims": project_state.get("claims", []),
            "confirmed_impacts": request.get("confirmed_impacts", []),
            "focus": request.get("focus", "design comparison experiments"),
        })
        registry = request.get("registry")
        if registry is None or registry.llm_client is None:
            return SkillOutput(
                skill_name=self.name,
                artifact_type="experiment_plan",
                content={"experiments": [], "result_table_specs": [], "evidence_requirements": []},
                warnings=["llm_not_configured"],
            )
        output, meta_data = registry._llm_skill(
            "skill_experiment_designer",
            prompt,
            ExperimentOutput,
            project_state.get("case_id", "unknown"),
            artifact_type="experiment_design",
            skill_name=self.name,
        )
        return SkillOutput(
            skill_name=self.name,
            artifact_type="experiment_plan",
            content=output,
            warnings=[],
            handoff_suggestions=["ccf_paper_writer"] if output.get("result_table_specs") else [],
        )
