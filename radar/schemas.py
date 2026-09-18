"""Pydantic contracts shared by deterministic and model-assisted stages."""

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class EvidenceSpan(BaseModel):
    quote: str
    locator: str
    source_snapshot_id: str | None = None

    @field_validator("quote", "locator")
    @classmethod
    def not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("evidence fields cannot be blank")
        return value


class EmpiricalClaimContract(BaseModel):
    task: str | None = None
    dataset: str | None = None
    split: str | None = None
    metric: str | None = None
    comparator: str | None = None
    scope: str | None = None


# ---- Solution: Claim Type Expansion ----

ClaimType = Literal[
    "empirical_result",    # 定量结果
    "method_mechanism",    # 方法与机制
    "innovation",          # 创新性
    "hypothesis_relation", # 假设与研究关系
    "generalization",      # 泛化与鲁棒性
    "causal_explanation",  # 因果或解释性主张
    "limitation_boundary", # 局限和适用边界
]

AuthorAttribution = Literal[
    "current_author",    # 当前作者
    "cited_author",      # 被引用作者
    "background_knowledge", # 背景知识
    "assumption",        # 假设
    "future_work",       # 未来工作
    "uncertain",         # 不确定归属
]

ClaimLifecycle = Literal[
    "active", "new", "modified", "deleted", "unchanged", "superseded", "needs_revalidation",
]

ClaimRole = Literal[
    "core_conclusion",   # 核心结论
    "sub_claim",         # 子Claim
    "experimental_support", # 实验支持
    "boundary_condition", # 适用边界
    "method_component",  # 方法组件
    "comparative",       # 比较性主张
]

G0RejectReason = Literal[
    "wrong_attribution", "missing_qualifier", "not_a_claim", "duplicate",
    "too_broad", "unsupported", "wrong_decomposition", "wrong_type",
    "wrong_centrality", "other",
]

# ---- Solution 1: Section-Aware Document Map ----

class SectionInfo(BaseModel):
    section_id: str
    heading: str
    level: int = Field(ge=1, le=4, description="1=top-level section, 2=subsection, etc.")
    start_offset: int
    end_offset: int
    text: str = Field(default="", description="Full text of this section")

class DocumentMap(BaseModel):
    sections: list[SectionInfo] = Field(default_factory=list)
    tables: list[str] = Field(default_factory=list, description="Table captions and references")
    figures: list[str] = Field(default_factory=list, description="Figure captions and references")
    total_length: int = 0
    language: str = "en"

# ---- Solution 3: Manuscript Overview ----

class ManuscriptOverview(BaseModel):
    """Lightweight paper portrait generated before claim extraction."""
    research_problem: str = ""
    domain: str = ""
    main_methods: list[str] = Field(default_factory=list)
    datasets: list[str] = Field(default_factory=list)
    key_variables: list[str] = Field(default_factory=list)
    likely_contributions: list[str] = Field(default_factory=list)
    author_terms: list[str] = Field(default_factory=list, description="Terms that identify the authors' own work (our, proposed, this work)")
    cited_work_markers: list[str] = Field(default_factory=list, description="Terms that identify cited work (et al., previous, existing)")

# ---- Solution 2: Multi-Stage Claim Pipeline Outputs ----

class ClaimCandidate(BaseModel):
    """Raw claim candidate before attribution/classification."""
    statement: str
    source_quote: str
    source_locator: str
    section_id: str = ""

class AttributedCandidate(ClaimCandidate):
    author_attribution: AuthorAttribution = "uncertain"
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)

class ClassifiedCandidate(AttributedCandidate):
    claim_type: ClaimType = "empirical_result"
    claim_role: ClaimRole = "sub_claim"

class QualifiedCandidate(ClassifiedCandidate):
    """Claim with extracted qualifiers / conditions."""
    contract: EmpiricalClaimContract = Field(default_factory=EmpiricalClaimContract)
    qualifiers: list[str] = Field(default_factory=list, description="限定条件: dataset, sample, language, condition, baseline")
    falsifiable_condition: str = ""
    centrality: Literal["core", "major", "minor"] = "major"

class VerifiedCandidate(QualifiedCandidate):
    """Evidence-verified claim ready for G0."""
    source_quote_verified: bool = True
    qualifier_gaps: list[str] = Field(default_factory=list)

# ---- Solution 4: G0 Structured Feedback ----

class G0Feedback(BaseModel):
    claim_revision_id: str = ""
    decision: Literal["confirmed", "edited", "rejected"]
    reject_reason: G0RejectReason | None = None
    edit_fields: list[str] = Field(default_factory=list, description="Which fields were edited")
    before_values: dict[str, str] = Field(default_factory=dict)
    after_values: dict[str, str] = Field(default_factory=dict)
    human_note: str = ""

# ---- Solution 5: Incremental Tracking ----

class ClaimDiff(BaseModel):
    stable_key: str
    previous_version_id: str
    current_version_id: str
    change_type: ClaimLifecycle = "unchanged"
    changed_sections: list[str] = Field(default_factory=list)
    fields_changed: list[str] = Field(default_factory=list)

# ---- Existing schemas unchanged below ----


class ManuscriptClaimProfile(BaseModel):
    stable_key: str
    role: Literal["core", "major", "minor"]
    claim_summary: str
    contract: EmpiricalClaimContract
    boundary_conditions: list[str] = Field(default_factory=list)
    falsification_tests: list[str] = Field(default_factory=list)


class ManuscriptUnderstandingOutput(BaseModel):
    title: str
    research_problem: str
    central_thesis: str
    contributions: list[str]
    methods: list[str]
    datasets: list[str]
    evaluation_protocol: list[str]
    key_findings: list[str]
    limitations: list[str]
    terminology: list[str]
    watch_topics: list[str]
    claim_profiles: list[ManuscriptClaimProfile]


class ActionRecommendation(BaseModel):
    action_type: Literal[
        "team_decision",
        "experiment",
        "data",
        "writing",
        "cite",
        "competitor_response",
        "revalidation",
    ]
    priority: Literal["critical", "high", "medium", "low"]
    title: str
    rationale: str
    checklist: list[str]
    due_label: str
    initial_status: Literal["proposed", "open"] = "proposed"
    advice_source: Literal["llm", "rule"] = "rule"


class ActionAdviceOutput(BaseModel):
    """One concrete project action drafted by the analysis LLM for an impact.

    Categories map to stored ActionItem.action_type values: 补实验
    (experiment), 补数据 (data), 调整写作 (writing), 引用/关注 (cite),
    验证重跑 (revalidation).
    """

    category: Literal["experiment", "data", "writing", "cite", "revalidation"]
    title: str
    rationale: str
    checklist: list[str] = Field(default_factory=list)


class ClaimCandidateOutput(BaseModel):
    statement: str
    claim_type: Literal["empirical_result"] = "empirical_result"
    centrality_suggestion: Literal["core", "major", "minor"]
    contract: EmpiricalClaimContract
    falsifiable_condition: str
    source_quote: str
    source_locator: str


class ClaimCandidateBatch(BaseModel):
    """LLM response wrapper: the model returns claims as one JSON object."""

    candidates: list[ClaimCandidateOutput] = Field(default_factory=list)


class ConditionDifference(BaseModel):
    field: Literal["task", "dataset", "split", "metric", "comparator", "scope"]
    own_value: str | None
    incoming_value: str | None
    status: Literal["match", "compatible_alias", "partial", "mismatch", "unknown"]
    explanation: str


class KeyDifference(BaseModel):
    assumptions: str = ""
    methodology: str = ""
    performance: str = ""


class DefenseStrategy(BaseModel):
    framing_shift: str = ""
    uncovered_limitation: str = ""
    required_experiments: list[str] = Field(default_factory=list)


class AdversarialCritique(BaseModel):
    lethal_reviewer_question: str = ""
    key_difference: KeyDifference = Field(default_factory=KeyDifference)
    defense_strategy: DefenseStrategy = Field(default_factory=DefenseStrategy)


class ImpactAssessmentOutput(BaseModel):
    stance: Literal["supports", "challenges", "neutral", "uncertain"]
    impact_mode: Literal[
        "replication", "boundary_condition", "method_substitution", "prior_art",
        "research_integrity", "no_material_change",
    ]
    comparability: Literal["compatible", "partial", "incompatible", "unknown"]
    condition_differences: list[ConditionDifference]
    evidence_own: EvidenceSpan
    evidence_new: EvidenceSpan
    change_depth: Literal[0, 1, 2, 3, 4]
    suggested_action: Literal[
        "cite", "add_boundary_discussion", "run_comparison", "narrow_claim",
        "team_review", "revalidate", "watch", "no_action",
    ]
    uncertainty_sources: list[str] = Field(default_factory=list)
    # Enhanced peer review fields (Adversarial critique & defense strategy)
    impact_type: Literal[
        "DIRECT_CONFLICT", "BOUNDARY_NARROWING", "EMPIRICAL_DOMINANCE", "COMPLEMENTARY", ""
    ] = ""
    severity: Literal["HIGH", "MEDIUM", "LOW", "critical", "review", "informative", ""] = ""
    confidence_score: float = Field(default=0.95, ge=0.0, le=1.0)
    one_sentence_verdict: str = ""
    adversarial_critique: AdversarialCritique | None = None
    key_difference: KeyDifference | None = None
    defense_strategy: DefenseStrategy | None = None


class MultiLocationEdit(BaseModel):
    section: str = Field(
        description="Section: introduction / related_work / discussion / limitations / methods"
    )
    edit_class: Literal[
        "add_citation", "add_boundary_discussion", "add_limitation",
        "qualify_claim", "experiment_todo",
        "CONSERVATIVE_CITATION", "SCOPE_ADJUSTMENT", "FULL_REWRITE",
    ]
    before_text: str = ""
    after_text: str = ""
    reason: str = Field(default="", description="Why this edit is needed at this location")
    target_location: str = ""
    original_sentence: str = ""
    patched_sentence: str = ""
    patch_type: str = ""

    def model_post_init(self, __context: Any) -> None:
        if not self.before_text and self.original_sentence:
            self.before_text = self.original_sentence
        elif not self.original_sentence and self.before_text:
            self.original_sentence = self.before_text

        if not self.after_text and self.patched_sentence:
            self.after_text = self.patched_sentence
        elif not self.patched_sentence and self.after_text:
            self.patched_sentence = self.after_text

        if not self.target_location:
            self.target_location = self.section
        if not self.patch_type and self.edit_class:
            self.patch_type = str(self.edit_class)


class MultiLocationPatchOutput(BaseModel):
    edits: list[MultiLocationEdit] = Field(default_factory=list, max_length=5)
    global_rationale: str = Field(default="", description="Overall rationale for all edits")
    assertions_added: list[str] = Field(default_factory=list)
    assertions_weakened_or_removed: list[str] = Field(default_factory=list)
    citation_source_ids: list[str] = Field(default_factory=list)


class PatchProposalOutput(BaseModel):
    edit_class: str = ""
    target_locator: str = ""
    before_text: str = ""
    after_text: str = ""
    citation_source_ids: list[str] = Field(default_factory=list)
    assertions_added: list[str] = Field(default_factory=list)
    assertions_weakened_or_removed: list[str] = Field(default_factory=list)
    rationale: str = ""
    target_location: str = ""
    original_sentence: str = ""
    patched_sentence: str = ""
    patch_type: str = ""
    diff_summary: str = ""

    def model_post_init(self, __context: Any) -> None:
        if not self.target_locator and self.target_location:
            self.target_locator = self.target_location
        elif not self.target_location and self.target_locator:
            self.target_location = self.target_locator

        if not self.before_text and self.original_sentence:
            self.before_text = self.original_sentence
        elif not self.original_sentence and self.before_text:
            self.original_sentence = self.before_text

        if not self.after_text and self.patched_sentence:
            self.after_text = self.patched_sentence
        elif not self.patched_sentence and self.after_text:
            self.patched_sentence = self.after_text

        if not self.patch_type and self.edit_class:
            self.patch_type = self.edit_class
        elif not self.edit_class and self.patch_type:
            self.edit_class = self.patch_type

        if not self.edit_class:
            self.edit_class = "add_boundary_discussion"
        if not self.patch_type:
            self.patch_type = self.edit_class or "SCOPE_ADJUSTMENT"


class ParsedSection(BaseModel):
    title: str
    locator: str
    text: str


class ParsedBlock(BaseModel):
    text: str
    locator: str


class ParsedDocument(BaseModel):
    path: Path
    full_text: str
    sections: list[ParsedSection]
    paragraphs: list[ParsedBlock]
    sentences: list[ParsedBlock]
    content_hash: str

class ReportedFinding(BaseModel):
    """One concrete result the incoming paper reports, kept verbatim.

    ``quote`` must appear in the paper's text; it is re-verified before
    reaching suggestion generation, so an unverifiable finding is dropped
    rather than cited.
    """

    quote: str = ""
    locator: str = ""
    conclusion: str = ""


class IncomingResult(EmpiricalClaimContract):
    direction: Literal["supports", "challenges", "neutral", "uncertain"] = "uncertain"
    reported_findings: list[ReportedFinding] = Field(
        default_factory=list, max_length=5
    )


class WatchQuery(BaseModel):
    query: str
    max_results: int = Field(default=50, ge=1, le=100)


class SearchQueryBatch(BaseModel):
    """English arXiv keyword queries distilled from one research question."""

    queries: list[str] = Field(default_factory=list, max_length=5)


class ExtractedReference(BaseModel):
    """One bibliography entry parsed from a manuscript's references section."""

    title: str
    doi: str | None = None
    year: int | None = None
    authors: list[str] = Field(default_factory=list)


class ExtractedReferenceBatch(BaseModel):
    """LLM response wrapper: the model returns references as one JSON object."""

    references: list[ExtractedReference] = Field(default_factory=list, max_length=15)


class ReferenceEntry(BaseModel):
    raw: str = ""
    bibtex_key: str | None = None
    title: str = ""
    authors: list[str] = Field(default_factory=list)
    year: int | None = None
    venue: str | None = None
    doi: str | None = None
    arxiv_id: str | None = None
    format: str = "text"


class ReferenceMatch(BaseModel):
    source_kind: str
    external_id: str
    title: str
    authors: list[str] = Field(default_factory=list)
    year: int | None = None
    venue: str | None = None
    doi: str | None = None
    arxiv_id: str | None = None
    url: str = ""
    matched_fields: list[str] = Field(default_factory=list)
    confidence: float = 0.0


class ReferenceCheckResult(BaseModel):
    entry: ReferenceEntry
    status: Literal["verified", "unverified"]
    confidence: float = 0.0
    reasons: list[str] = Field(default_factory=list)
    matched: ReferenceMatch | None = None
    queried_sources: list[str] = Field(default_factory=list)


class ReferenceCheckBatch(BaseModel):
    results: list[ReferenceCheckResult] = Field(default_factory=list)
    checked_at: str = ""
    summary: dict[str, int] = Field(default_factory=dict)


class CitationSupportJudgeOutput(BaseModel):
    """Structured judgment returned by the citation-support LLM."""

    category: Literal[
        "supported", "partially_supported", "unsupported", "uncertain"
    ] = "uncertain"
    confidence: float = 0.0
    evidence_quote: str = ""
    evidence_locator: str = ""
    reasons: list[str] = Field(default_factory=list)
    recommended_actions: list[str] = Field(default_factory=list)


class SemanticContradictionOutput(BaseModel):
    """One pair-level verdict from the layer-2 self-contradiction judge.

    Every field defaults to the "nothing found" answer so that a truncated or
    malformed model response degrades into a dropped pair rather than a
    reported contradiction.
    """

    is_contradiction: bool = False
    kind: Literal[
        "scope_overreach",
        "requirement_conflict",
        "conclusion_conflict",
        "capability_conflict",
        "none",
    ] = "none"
    confidence: float = 0.0
    # Must appear verbatim in the corresponding sentence; the caller discards
    # the verdict when it does not.
    quote_a: str = ""
    quote_b: str = ""
    reason: str = ""


class CitationSupportInput(BaseModel):
    reference: ReferenceMatch
    citation_sentence: str = ""
    full_text: str | None = None
    abstract: str | None = None


class CitationSupportResult(BaseModel):
    reference: ReferenceMatch
    citation_sentence: str = ""
    normalized_claim: str = ""
    category: Literal[
        "supported", "partially_supported", "unsupported", "uncertain"
    ] = "uncertain"
    confidence: float = 0.0
    evidence_quote: str = ""
    locator: str = ""
    full_text_status: Literal["full_text", "abstract_only", "unavailable"] = (
        "unavailable"
    )
    reasons: list[str] = Field(default_factory=list)
    recommended_actions: list[str] = Field(default_factory=list)


class CitationSupportBatch(BaseModel):
    results: list[CitationSupportResult] = Field(default_factory=list)
    checked_at: str = ""
    summary: dict[str, int] = Field(default_factory=dict)


class HydeAbstractOutput(BaseModel):
    """Hypothetical relevant-paper abstract used as a search/ranking query."""

    abstract: str


class RerankCandidateScore(BaseModel):
    """LLM relevance score for one retrieval-rerank candidate paper."""

    key: str
    score: float = Field(ge=0, le=10)
    reason: str = ""


class RerankBatchOutput(BaseModel):
    """LLM response wrapper: relevance scores for one batch of candidates."""

    scores: list[RerankCandidateScore] = Field(default_factory=list)


class SourceRecord(BaseModel):
    source_kind: str = "unknown"
    external_id: str
    title: str
    authors: list[str]
    abstract: str
    url: str
    published_at: str | None = None
    doi: str | None = None
    arxiv_id: str | None = None
    license: str | None = None
    venue: str | None = None
    publication_type: Literal["preprint", "journal_article", "conference_paper", "other"] = "preprint"
    pdf_url: str | None = None
    cited_by_count: int | None = None
    # CS/AI domain metadata
    arxiv_primary_category: str | None = None
    fields_of_study: list[str] = Field(default_factory=list)
    ccf_rank: str | None = None


class AtomicClaim(BaseModel):
    """A single verifiable atomic sub-claim decomposed from a complex claim."""

    statement: str
    source_quote: str
    source_locator: str
    dependencies: list[str] = Field(default_factory=list)
    verifiable_independently: bool = True


class AtomicClaimBatch(BaseModel):
    atomic_claims: list[AtomicClaim] = Field(default_factory=list, max_length=20)


class ClaimSemanticVerification(BaseModel):
    """Verification result for a claim's statement-quote semantic alignment."""

    claim_stable_key: str
    statement: str
    source_quote: str
    faithful: bool = Field(description="Does the statement faithfully reflect the source_quote?")
    issues: list[str] = Field(default_factory=list)
    suggested_correction: str = Field(default="")


class TrustResult(BaseModel):
    state: Literal["generated", "grounded", "verified", "blocked"]
    errors: list[str] = Field(default_factory=list)


class EvidenceFidelityVerdict(BaseModel):
    """RAGAS-style faithfulness verdict on one impact assessment.

    The judge checks whether the assessment's evidence (verbatim quotes and
    condition differences) actually supports its directional stance — the
    "extracted information is accurate" gate for the modification suggestions
    built on top.
    """

    faithful: bool = Field(
        description="True when the evidence genuinely supports the stance"
    )
    issues: list[str] = Field(
        default_factory=list,
        description="Concrete problems with the evidence-stance alignment",
    )
    reason: str = Field(default="", description="One-sentence justification")


# ---- Deep Research (gpt-researcher style bounded literature synthesis) ----

class DeepResearchSubQuery(BaseModel):
    """One concrete literature-search sub-query in a deep-research plan."""

    query: str = Field(description="Plain-keyword academic search query")
    rationale: str = Field(default="", description="Why this angle matters")
    focus: str = Field(default="", description="What to look for in the results")


class DeepResearchPlan(BaseModel):
    """LLM plan for a deep research run: title + decomposed sub-queries."""

    title: str = Field(default="", description="Short human-readable research title")
    sub_queries: list[DeepResearchSubQuery] = Field(
        default_factory=list, max_length=8
    )


class DeepResearchSourceSummary(BaseModel):
    """Compressed, citation-ready summary of one external paper."""

    source_id: str = Field(default="", description="Stored SourceSnapshot id")
    title: str
    authors: list[str] = Field(default_factory=list)
    year: str | None = None
    venue: str | None = None
    url: str = ""
    doi: str | None = None
    relevance: str = Field(default="", description="Why this paper matters")
    key_findings: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    contradictions: list[str] = Field(
        default_factory=list,
        description="Points where this paper conflicts with the research question",
    )


class DeepResearchSection(BaseModel):
    """One section of the synthesized deep-research brief."""

    heading: str
    findings: list[str] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)


class DeepResearchBrief(BaseModel):
    """Final structured deep-research report with human-auditable sources."""

    title: str = ""
    executive_summary: str = ""
    sections: list[DeepResearchSection] = Field(default_factory=list)
    key_insights: list[str] = Field(default_factory=list)
    contradictions: list[str] = Field(default_factory=list)
    research_gaps: list[str] = Field(default_factory=list)
    recommended_next_steps: list[str] = Field(default_factory=list)
    sources: list[DeepResearchSourceSummary] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
