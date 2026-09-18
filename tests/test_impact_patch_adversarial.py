"""Unit tests for adversarial peer review impact assessment and patch diff enhancements."""

import pytest
from uuid import uuid4

from radar.schemas import (
    AdversarialCritique,
    ConditionDifference,
    DefenseStrategy,
    EvidenceSpan,
    ImpactAssessmentOutput,
    KeyDifference,
    MultiLocationEdit,
    MultiLocationPatchOutput,
    PatchProposalOutput,
)
from radar.services.impact_service import ImpactService
from radar.services.patch_service import PatchService
from radar.services.review_service import ReviewService
from radar.models import ImpactCandidate, PatchProposal
from radar.routes.builders import _impact_to_paper_out
from radar.routes.patches import _patch_to_rewrite


def test_impact_assessment_adversarial_schema_serialization():
    critique = AdversarialCritique(
        lethal_reviewer_question="外部论文已在更少约束下取得更高指标，本文的不可替代性贡献在哪里？",
        key_difference=KeyDifference(
            assumptions="在研稿件假设干净无噪声环境，外部论文引入自适应对抗扰动。",
            methodology="在研成果采用单阶段轻量级重排，外部论文采用跨编码探针。",
            performance="在研基准 +5.6 EM，外部在 2% 噪声下退化至 +0.6 EM。",
        ),
        defense_strategy=DefenseStrategy(
            framing_shift="退守至计算预算受限下的标准领域高能效增益，限定适用范围。",
            uncovered_limitation="外部方法带来超 280% 的额外延迟开销，难以在端侧部署。",
            required_experiments=[
                "补充噪声梯度消融实验",
                "增加延迟-精度帕累托前沿对比",
            ],
        ),
    )

    output = ImpactAssessmentOutput(
        stance="challenges",
        impact_mode="boundary_condition",
        comparability="partial",
        condition_differences=[
            ConditionDifference(
                field="scope",
                own_value="RadarNet clean",
                incoming_value="Adversarial noise",
                status="partial",
                explanation="Testing conditions differ in input noise",
            )
        ],
        evidence_own=EvidenceSpan(quote="Own quote", locator="sec:1"),
        evidence_new=EvidenceSpan(quote="Incoming quote", locator="abstract:1"),
        change_depth=2,
        suggested_action="add_boundary_discussion",
        impact_type="BOUNDARY_NARROWING",
        confidence_score=0.95,
        one_sentence_verdict="外部竞品论文揭示了扰动下的脆弱性，压缩了适用边界。",
        adversarial_critique=critique,
    )

    data = output.model_dump()
    assert data["impact_type"] == "BOUNDARY_NARROWING"
    assert data["confidence_score"] == 0.95
    assert data["adversarial_critique"]["lethal_reviewer_question"].startswith("外部论文已在更少约束")
    assert len(data["adversarial_critique"]["defense_strategy"]["required_experiments"]) == 2


def test_impact_service_classify_impact_type():
    assert ImpactService.classify_impact_type("challenges", "boundary_condition", "compatible") == "DIRECT_CONFLICT"
    assert ImpactService.classify_impact_type("challenges", "boundary_condition", "partial") == "BOUNDARY_NARROWING"
    assert ImpactService.classify_impact_type("neutral", "method_substitution", "incompatible") == "EMPIRICAL_DOMINANCE"
    assert ImpactService.classify_impact_type("supports", "replication", "compatible") == "COMPLEMENTARY"


def test_impact_service_build_adversarial_critique_fallback():
    assessment = ImpactAssessmentOutput(
        stance="challenges",
        impact_mode="boundary_condition",
        comparability="partial",
        condition_differences=[],
        evidence_own=EvidenceSpan(quote="quote 1", locator="loc:1"),
        evidence_new=EvidenceSpan(quote="quote 2", locator="loc:2"),
        change_depth=2,
        suggested_action="narrow_claim",
    )

    critique = ImpactService.build_adversarial_critique(
        assessment,
        claim_statement="Reranking achieves 5.6 EM gain under DomainQA benchmark.",
        incoming_title="Adaptive Query Perturbations in RAG",
    )

    assert "Adaptive Query Perturbations in RAG" in critique.lethal_reviewer_question
    assert critique.key_difference.assumptions != ""
    assert critique.defense_strategy.framing_shift != ""
    assert len(critique.defense_strategy.required_experiments) >= 2


def test_patch_proposal_output_sync_diff_fields():
    p1 = PatchProposalOutput(
        edit_class="add_boundary_discussion",
        target_locator="sec:discussion:p:2",
        before_text="Our method achieves consistent gain.",
        after_text="While our method achieves gain [CITATION], boundary differs.",
        citation_source_ids=["source-01"],
    )
    assert p1.target_location == "sec:discussion:p:2"
    assert p1.original_sentence == "Our method achieves consistent gain."
    assert p1.patched_sentence == "While our method achieves gain [CITATION], boundary differs."
    assert p1.patch_type == "add_boundary_discussion"

    p2 = PatchProposalOutput(
        target_location="sec:limitations:p:1",
        original_sentence="Previous sentence.",
        patched_sentence="Patched sentence with [CITATION].",
        patch_type="SCOPE_ADJUSTMENT",
    )
    assert p2.target_locator == "sec:limitations:p:1"
    assert p2.before_text == "Previous sentence."
    assert p2.after_text == "Patched sentence with [CITATION]."
    assert p2.edit_class == "SCOPE_ADJUSTMENT"


def test_multi_location_edit_diff_fields():
    edit = MultiLocationEdit(
        section="Limitations",
        edit_class="SCOPE_ADJUSTMENT",
        original_sentence="Original limitation.",
        patched_sentence="Updated limitation with [CITATION].",
        reason="Expose computational trade-off",
    )
    assert edit.before_text == "Original limitation."
    assert edit.after_text == "Updated limitation with [CITATION]."
    assert edit.target_location == "Limitations"
    assert edit.patch_type == "SCOPE_ADJUSTMENT"


def test_patch_to_rewrite_includes_diff_metadata(db_session_factory, golden_case):
    ReviewService(db_session_factory).confirm_impact("impact-01")
    patch = PatchService(db_session_factory).generate_patch("impact-01")
    rewrite = _patch_to_rewrite(patch, session_factory=db_session_factory)

    assert "patchType" in rewrite
    assert "targetLocation" in rewrite
    assert "originalSentence" in rewrite
    assert "patchedSentence" in rewrite
    assert rewrite["originalSentence"] == patch.before_text
    assert rewrite["patchedSentence"] == patch.after_text


def test_impact_to_paper_out_includes_adversarial_fields(db_session_factory, golden_case):
    with db_session_factory() as session:
        impact = session.get(ImpactCandidate, "impact-01")
        impact.strategy_payload_json = {
            "impact_type": "BOUNDARY_NARROWING",
            "confidence_score": 0.96,
            "one_sentence_verdict": "外部论文压缩了声明的适用边界。",
            "adversarial_critique": {
                "lethal_reviewer_question": "审稿人尖锐质疑：创新性在哪里？",
                "key_difference": {
                    "assumptions": "假设前提差异",
                    "methodology": "方法差异",
                    "performance": "指标差异",
                },
                "defense_strategy": {
                    "framing_shift": "话术退守策略",
                    "uncovered_limitation": "指出对方盲区",
                    "required_experiments": ["实验1", "实验2"],
                },
            },
        }
        session.commit()

        paper_out = _impact_to_paper_out(impact, session_factory=db_session_factory)
        assert paper_out.impactType == "BOUNDARY_NARROWING"
        assert paper_out.confidenceScore == 0.96
        assert paper_out.oneSentenceVerdict == "外部论文压缩了声明的适用边界。"
        assert paper_out.lethalReviewerQuestion == "审稿人尖锐质疑：创新性在哪里？"
        assert paper_out.keyDifference["assumptions"] == "假设前提差异"
        assert len(paper_out.defenseStrategy["required_experiments"]) == 2
