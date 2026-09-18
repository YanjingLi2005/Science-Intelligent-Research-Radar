from radar.schemas import EmpiricalClaimContract
from radar.services.condition_service import ConditionService
from radar.services.impact_service import ImpactService


def test_condition_match():
    service = ConditionService()
    contract = EmpiricalClaimContract(
        task="open-domain QA", dataset="DomainQA", split="test",
        metric="exact match", comparator="BM25", scope="RadarNet",
    )
    differences = service.compare(contract, contract)
    assert len(differences) == 6
    assert all(item.status == "match" for item in differences)
    assert service.overall_comparability(differences) == "compatible"


def test_condition_unknown_not_inferred():
    service = ConditionService()
    differences = service.compare(
        {"task":"QA", "dataset":"DomainQA", "metric":"exact match", "comparator":"BM25"},
        {"task":"QA", "dataset":None, "metric":None, "comparator":"BM25"},
    )
    statuses = {item.field: item.status for item in differences}
    assert statuses["dataset"] == "unknown"
    assert statuses["metric"] == "unknown"
    assert service.overall_comparability(differences) == "unknown"


def test_hard_mismatch_blocks_challenge():
    service = ConditionService()
    differences = service.compare(
        {"task":"QA", "dataset":"DomainQA", "metric":"exact match", "comparator":"BM25"},
        {"task":"QA", "dataset":"OtherSet", "metric":"exact match", "comparator":"BM25"},
    )
    comparability = service.overall_comparability(differences)
    assert comparability == "incompatible"
    assert ImpactService.enforce_stance("challenges", comparability) == "uncertain"


def test_unknown_comparability_blocks_directional_stance():
    assert ImpactService.enforce_stance("supports", "unknown") == "uncertain"
    assert ImpactService.enforce_stance("challenges", "unknown") == "uncertain"
    assert ImpactService.enforce_stance("supports", "incompatible") == "uncertain"
    assert ImpactService.enforce_stance("supports", "partial") == "uncertain"
    assert ImpactService.enforce_stance("challenges", "partial") == "uncertain"


def test_competitor_flag_does_not_change_stance():
    stance = ImpactService.enforce_stance("neutral", "compatible")
    assert stance == "neutral"
    assert ImpactService.severity(
        centrality="major", stance=stance, comparability="compatible",
        impact_mode="prior_art", change_depth=1, strategic_flags=["competitor"],
    ) == "review"
