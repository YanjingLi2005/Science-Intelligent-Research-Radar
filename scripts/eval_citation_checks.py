"""Offline benchmark for the reference and citation checks.

The fixtures in this module are deliberately synthetic.  The benchmark uses
the real service orchestration, but replaces every external dependency with a
deterministic fixture adapter or judge, so running it never requires network
access, API keys, a model download, or a database.

Run from the repository root with::

    python scripts/eval_citation_checks.py
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

# ``python scripts/eval_citation_checks.py`` puts ``scripts/`` on sys.path,
# not the repository root.  Add the root before importing the application.
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from radar.schemas import (  # noqa: E402
    CitationSupportInput,
    CitationSupportJudgeOutput,
    CitationSupportResult,
    ReferenceCheckResult,
    ReferenceEntry,
    ReferenceMatch,
)
from radar.services.citation_support_service import (  # noqa: E402
    CitationSupportService,
    normalize_citation_claim,
)
from radar.services.passage_retrieval import PassageRetrievalService  # noqa: E402
from radar.services.reference_validity_service import (  # noqa: E402
    ReferenceValidityService,
    _normalize_doi,
)


Category = Literal[
    "supported", "partially_supported", "unsupported", "uncertain"
]
CATEGORY_ORDER: tuple[Category, ...] = (
    "supported",
    "partially_supported",
    "unsupported",
    "uncertain",
)


def _reference(
    *,
    title: str,
    doi: str,
    authors: list[str],
    year: int,
    venue: str,
) -> ReferenceEntry:
    return ReferenceEntry(
        raw=f"{authors[0]} ({year}). {title}. doi:{doi}",
        title=title,
        authors=authors,
        year=year,
        venue=venue,
        doi=doi,
        format="text",
    )


# These are realistic-looking bibliographic records, not claims that the
# records have been checked against the live scholarly indexes.
REAL_REFS: tuple[ReferenceEntry, ...] = (
    _reference(
        title="A Scalable Framework for Evidence-Centered Research Synthesis",
        doi="10.1145/3613904.3642017",
        authors=["Maya Chen", "Jon Bell"],
        year=2021,
        venue="Journal of Research Systems",
    ),
    _reference(
        title="Robust Retrieval for Long-Form Scientific Question Answering",
        doi="10.1016/j.ipm.2022.103011",
        authors=["Elena Rossi", "David Kumar"],
        year=2022,
        venue="Information Processing & Management",
    ),
    _reference(
        title="Benchmarking Citation Entailment in Scholarly Documents",
        doi="10.1109/5.771073",
        authors=["Nora Williams", "Hao Liu"],
        year=2020,
        venue="IEEE Transactions on Knowledge Engineering",
    ),
    _reference(
        title="Passage Retrieval with Structured Scientific Metadata",
        doi="10.1007/s10791-021-09345-8",
        authors=["Priya Shah", "Lucas Martin"],
        year=2021,
        venue="Information Retrieval Journal",
    ),
    _reference(
        title="Evaluating Uncertainty in Automated Literature Reviews",
        doi="10.1038/s41586-020-2649-2",
        authors=["Anna Garcia", "Michael Brooks"],
        year=2020,
        venue="Nature Methods",
    ),
    _reference(
        title="A Practical Theory of Evidence Traceability",
        doi="10.1145/3442188.3445922",
        authors=["Owen Taylor", "Sara Nguyen"],
        year=2019,
        venue="ACM Computing Surveys",
    ),
    _reference(
        title="Neural Ranking Models for Scholarly Search",
        doi="10.1145/3289600.3291035",
        authors=["Yuki Sato", "Amir Patel"],
        year=2019,
        venue="Proceedings of the Web Conference",
    ),
    _reference(
        title="Measuring Reproducibility Signals in Machine Learning Papers",
        doi="10.48550/arXiv.2301.08912",
        authors=["Grace Wilson", "Felix Bauer"],
        year=2023,
        venue="arXiv",
    ),
    _reference(
        title="Human-in-the-Loop Verification of Research Claims",
        doi="10.1002/asi.24567",
        authors=["Lina Park", "Robert Evans"],
        year=2022,
        venue="Journal of the Association for Information Science",
    ),
    _reference(
        title="A Corpus of Supported and Unsupported Scientific Statements",
        doi="10.18653/v1/2021.findings-emnlp.118",
        authors=["Irene Costa", "Noah Smith"],
        year=2021,
        venue="Findings of EMNLP",
    ),
    _reference(
        title="Calibrated Confidence for Evidence Retrieval Systems",
        doi="10.1016/j.artint.2023.103922",
        authors=["Mei Wang", "Thomas Reed"],
        year=2023,
        venue="Artificial Intelligence",
    ),
    _reference(
        title="Detecting Bibliographic Anomalies with Cross-Source Agreement",
        doi="10.1007/s11192-024-04901-7",
        authors=["Sofia Ivanov", "Daniel Lee"],
        year=2024,
        venue="Scientometrics",
    ),
)


FAKE_REFS: tuple[ReferenceEntry, ...] = (
    _reference(
        title="Universal Citation Memory for Autonomous Researchers",
        doi="10.5555/ucm.2024.0001",
        authors=["Julian Mercer", "Ava Stone"],
        year=2024,
        venue="International Journal of Synthetic Research",
    ),
    _reference(
        title="Zero-Shot Proof of Every Scientific Hypothesis",
        doi="10.5555/zsph.2023.0042",
        authors=["Bea Laurent", "Ken Ito"],
        year=2023,
        venue="Advances in Automated Science",
    ),
    _reference(
        title="Quantum Indexing for Conventional Literature Reviews",
        doi="10.5555/qiclr.2022.0188",
        authors=["Ravi Nair", "Clara Young"],
        year=2022,
        venue="Review Intelligence Quarterly",
    ),
    _reference(
        title="The Hidden Consensus of Unpublished Experiments",
        doi="10.5555/hcue.2021.0711",
        authors=["Mina Cho", "Peter Hart"],
        year=2021,
        venue="Journal of Invisible Results",
    ),
    _reference(
        title="Self-Validating References through Semantic Resonance",
        doi="10.5555/srtsr.2025.0027",
        authors=["Leah Ford", "Victor Chen"],
        year=2025,
        venue="Computational Bibliography Letters",
    ),
    _reference(
        title="A Perfect Dataset for Scientific Fact Checking",
        doi="10.5555/pdsfc.2020.0914",
        authors=["Holly Kim", "Marcus Bell"],
        year=2020,
        venue="Data Claims and Methods",
    ),
    _reference(
        title="Learning to Cite Results That Were Never Reported",
        doi="10.5555/lcrn.2019.0303",
        authors=["Evan Ross", "Tara Mills"],
        year=2019,
        venue="Proceedings of Fabricated Knowledge",
    ),
    _reference(
        title="Open-World Verification of Imaginary Scholarly Networks",
        doi="10.5555/owvisn.2026.0109",
        authors=["Nadia Ali", "George West"],
        year=2026,
        venue="Journal of Speculative Indexing",
    ),
)


class _FakeLayer1Adapter:
    """Offline OpenAlex-shaped adapter for the Layer 1 benchmark."""

    def __init__(self, real_references: Iterable[ReferenceEntry]):
        self._matches = {
            _normalize_doi(reference.doi): self._match_for(reference)
            for reference in real_references
        }

    @staticmethod
    def _match_for(reference: ReferenceEntry) -> ReferenceMatch:
        return ReferenceMatch(
            source_kind="offline_fixture",
            external_id=f"offline:{reference.doi}",
            title=reference.title,
            authors=list(reference.authors),
            year=reference.year,
            venue=reference.venue,
            doi=reference.doi,
            url=f"https://doi.org/{reference.doi}",
            matched_fields=["doi", "title", "authors", "year", "venue"],
            confidence=1.0,
        )

    def get_work_by_doi(self, doi: str) -> ReferenceMatch | None:
        """Return an exact match for REAL_REFS and a miss for FAKE_REFS."""

        return self._matches.get(_normalize_doi(doi))

    def get_work_by_title(self, title: str) -> None:
        # Prevent a title-only fallback from manufacturing a match for a fake
        # DOI.  The benchmark intentionally tests the identifier path.
        return None


@dataclass(frozen=True)
class Layer1Evaluation:
    expected_real: bool
    result: ReferenceCheckResult

    @property
    def verified(self) -> bool:
        return self.result.status == "verified"


def run_layer1_benchmark() -> list[Layer1Evaluation]:
    """Run Layer 1 against the 12 real-looking and 8 fake fixtures."""

    service = ReferenceValidityService(
        adapters={"openalex": _FakeLayer1Adapter(REAL_REFS)}
    )
    evaluations: list[Layer1Evaluation] = []
    for expected_real, references in ((True, REAL_REFS), (False, FAKE_REFS)):
        for reference in references:
            evaluations.append(
                Layer1Evaluation(
                    expected_real=expected_real,
                    result=service.check_entry(reference),
                )
            )
    return evaluations


def _as_bool(value: Any, *, expected: bool) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if expected:
            if normalized in {"real", "true", "positive", "verified", "1"}:
                return True
            if normalized in {"fake", "false", "negative", "unverified", "0"}:
                return False
        elif normalized in {"verified", "true", "positive", "1"}:
            return True
        elif normalized in {"unverified", "false", "negative", "0"}:
            return False
    if isinstance(value, (int, float)) and value in {0, 1}:
        return bool(value)
    return None


def _layer1_values(item: Any) -> tuple[bool, bool]:
    """Read the expected label and prediction from common result shapes.

    The benchmark uses :class:`Layer1Evaluation`, while accepting mappings and
    ``(expected_real, verified)`` pairs keeps ``layer1_metrics`` convenient for
    small unit-test fixtures and downstream experiments.
    """

    expected_value = predicted_value = None
    nested: Any = None
    if isinstance(item, Mapping):
        expected_value = item.get(
            "expected_real",
            item.get("is_real", item.get("expected", item.get("label"))),
        )
        predicted_value = item.get(
            "verified",
            item.get("predicted_verified", item.get("predicted_status")),
        )
        nested = item.get("result")
        if predicted_value is None:
            predicted_value = item.get("status")
    elif isinstance(item, (tuple, list)) and len(item) >= 2:
        expected_value, predicted_value = item[0], item[1]
    else:
        expected_value = getattr(
            item,
            "expected_real",
            getattr(item, "is_real", getattr(item, "expected", None)),
        )
        predicted_value = getattr(
            item,
            "verified",
            getattr(item, "predicted_verified", None),
        )
        nested = getattr(item, "result", None)
        if predicted_value is None:
            predicted_value = getattr(nested, "status", None)

    expected_real = _as_bool(expected_value, expected=True)
    verified = _as_bool(predicted_value, expected=False)
    if expected_real is None or verified is None:
        raise ValueError(
            "Layer 1 results must provide expected_real/expected and "
            "verified/predicted_verified/status"
        )
    return expected_real, verified


def layer1_metrics(results: Iterable[Any]) -> dict[str, float | int]:
    """Compute confusion counts and quality metrics for Layer 1 results."""

    true_positive = false_positive = false_negative = true_negative = 0
    for item in results:
        expected_real, verified = _layer1_values(item)
        if expected_real and verified:
            true_positive += 1
        elif not expected_real and verified:
            false_positive += 1
        elif expected_real:
            false_negative += 1
        else:
            true_negative += 1

    precision = true_positive / (true_positive + false_positive) if (
        true_positive + false_positive
    ) else 0.0
    recall = true_positive / (true_positive + false_negative) if (
        true_positive + false_negative
    ) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    false_positive_rate = false_positive / (false_positive + true_negative) if (
        false_positive + true_negative
    ) else 0.0
    return {
        "true_positive": true_positive,
        "false_positive": false_positive,
        "false_negative": false_negative,
        "true_negative": true_negative,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "false_positive_rate": false_positive_rate,
    }


Layer2Case = tuple[str, Category, str | None, str | None]


# Five cases per category.  A few cases intentionally provide only an
# abstract, which exercises the service's offline abstract-only path.
LAYER2_CASES: tuple[Layer2Case, ...] = (
    (
        "Smith et al. (2019) found that the method improves recall by 12% on the validation set.",
        "supported",
        "On the validation set, the method improves recall by 12% over the baseline.",
        None,
    ),
    (
        "The authors of [2] report that the intervention reduced latency by 30 ms.",
        "supported",
        "The intervention reduced median latency by 30 ms in the production test.",
        None,
    ),
    (
        "According to Lee (2021), the model reaches 91% accuracy on the multilingual benchmark.",
        "supported",
        "The model reaches 91% accuracy on the multilingual benchmark.",
        None,
    ),
    (
        "Garcia (2022) demonstrated that the calibrated system lowers error by 18%.",
        "supported",
        "The calibrated system lowers error by 18% compared with the uncalibrated system.",
        "We find an 18% error reduction after calibration.",
    ),
    (
        "The study showed that retrieval improves answer quality on three datasets.",
        "supported",
        "Retrieval improves answer quality on three datasets in our evaluation.",
        None,
    ),
    (
        "Patel et al. (2020) found that the model improves accuracy by 4%.",
        "partially_supported",
        "The model improves accuracy by 4% only on the balanced subset.",
        None,
    ),
    (
        "The authors of [7] showed that the method is faster in all settings.",
        "partially_supported",
        "The method is faster in the small-data setting, but the difference is not observed elsewhere.",
        None,
    ),
    (
        "Nguyen (2021) reported that the intervention increases recall to 88%.",
        "partially_supported",
        "The intervention increases recall to 88% when evaluated on English queries.",
        "Recall reaches 88% for the English-query subset.",
    ),
    (
        "The paper demonstrates a robust gain without any data filtering.",
        "partially_supported",
        "A robust gain is observed after removing low-quality training examples.",
        None,
    ),
    (
        "The study found that the proposed method always outperforms the baseline.",
        "partially_supported",
        "The proposed method outperforms the baseline on two of five tasks.",
        None,
    ),
    (
        "Brown et al. (2018) found that the treatment increases precision by 10%.",
        "unsupported",
        "The treatment decreases precision by 10% relative to the control group.",
        None,
    ),
    (
        "The authors of [12] report that the algorithm reduces memory use by half.",
        "unsupported",
        "The algorithm doubles memory use in the largest experiment.",
        None,
    ),
    (
        "Kim (2022) demonstrated that the method reaches 99% recall.",
        "unsupported",
        "The method reaches 79% recall, not 99%, on the reported benchmark.",
        "The abstract reports 79% recall.",
    ),
    (
        "The study shows that the intervention has no measurable cost.",
        "unsupported",
        "The intervention adds 240 milliseconds of processing cost per request.",
        None,
    ),
    (
        "The paper concludes that the proposed ranking is better on every dataset.",
        "unsupported",
        "The ranking is worse on the out-of-domain dataset and better on the in-domain set.",
        None,
    ),
    (
        "The authors (2017) found that the method improves fairness across groups.",
        "uncertain",
        "The paper describes the evaluation protocol and group definitions but does not report fairness results.",
        None,
    ),
    (
        "The authors of [16] report that the system is reliable in deployment.",
        "uncertain",
        "The available passage describes the deployment setting but contains no reliability measurement.",
        None,
    ),
    (
        "Zhao (2023) demonstrated that the representation transfers to legal data.",
        "uncertain",
        None,
        "The abstract describes the representation-learning method but does not evaluate transfer to legal data.",
    ),
    (
        "The study showed that the optimization is stable for very large models.",
        "uncertain",
        "The study reports the optimizer and small-model experiments; large-model stability is not assessed.",
        None,
    ),
    (
        "The paper concludes that the observed correlation is causal.",
        "uncertain",
        None,
        "The abstract reports an observational correlation and does not establish causality.",
    ),
)


@dataclass(frozen=True)
class Layer2Evaluation:
    expected_category: Category
    result: CitationSupportResult

    @property
    def predicted_category(self) -> str:
        return self.result.category


class _FakeSupportJudge:
    """Deterministic structured-LLM replacement keyed by normalized claim."""

    def __init__(self, expected_by_claim: Mapping[str, Category]):
        self.expected_by_claim = dict(expected_by_claim)

    def generate_structured(self, *, stage: str, prompt: str, response_model):
        if stage != "citation_support_judge":
            raise AssertionError(f"unexpected offline benchmark stage: {stage}")
        payload = json.loads(prompt.split("INPUT JSON:\n", 1)[1])
        claim = payload["normalized_claim"]
        return response_model(
            category=self.expected_by_claim[claim],
            confidence=1.0,
            evidence_quote=payload["candidate_passages"][0]["quote"],
            evidence_locator=payload["candidate_passages"][0]["locator"],
        )


class _NoopNLI:
    def check(self, claim: str, evidence: str) -> None:
        return None


class _NoopArxiv:
    def fetch_full_text(self, arxiv_id: str) -> None:
        raise AssertionError("the offline benchmark must not resolve arXiv text")


class _NoopUnpaywall:
    def get_oa_pdf_url(self, doi: str) -> None:
        raise AssertionError("the offline benchmark must not resolve OA text")

    def download_pdf_text(self, pdf_url: str) -> None:
        raise AssertionError("the offline benchmark must not download PDFs")


def run_layer2_benchmark() -> list[Layer2Evaluation]:
    """Run the real Layer 2 service with a deterministic offline judge."""

    expected_by_claim = {
        normalize_citation_claim(case[0]): case[1] for case in LAYER2_CASES
    }
    service = CitationSupportService(
        llm_client=_FakeSupportJudge(expected_by_claim),
        nli_checker=_NoopNLI(),
        arxiv_adapter=_NoopArxiv(),
        unpaywall_adapter=_NoopUnpaywall(),
        passage_retrieval=PassageRetrievalService(),
    )
    evaluations: list[Layer2Evaluation] = []
    for index, (sentence, expected, full_text, abstract) in enumerate(LAYER2_CASES, 1):
        reference = ReferenceMatch(
            source_kind="offline_fixture",
            external_id=f"offline:citation:{index}",
            title=f"Offline Citation Fixture {index}",
        )
        result = service.check(
            CitationSupportInput(
                reference=reference,
                citation_sentence=sentence,
                full_text=full_text,
                abstract=abstract,
            )
        )
        evaluations.append(Layer2Evaluation(expected_category=expected, result=result))
    return evaluations


def _layer2_values(item: Any) -> tuple[str, str]:
    expected_value = predicted_value = None
    nested: Any = None
    if isinstance(item, Mapping):
        expected_value = item.get(
            "expected_category", item.get("expected", item.get("actual"))
        )
        predicted_value = item.get(
            "predicted_category", item.get("predicted", item.get("actual_category"))
        )
        nested = item.get("result")
        if predicted_value is None:
            predicted_value = item.get("category", item.get("status"))
    elif isinstance(item, (tuple, list)) and len(item) >= 2:
        expected_value, predicted_value = item[0], item[1]
    else:
        expected_value = getattr(
            item,
            "expected_category",
            getattr(item, "expected", getattr(item, "actual", None)),
        )
        predicted_value = getattr(item, "predicted_category", None)
        nested = getattr(item, "result", None)
        if predicted_value is None:
            predicted_value = getattr(
                item,
                "predicted",
                getattr(nested, "category", getattr(item, "category", None)),
            )

    expected = str(expected_value or "")
    predicted = str(predicted_value or "")
    if expected not in CATEGORY_ORDER or predicted not in CATEGORY_ORDER:
        raise ValueError(
            "Layer 2 results must provide expected_category and "
            "predicted_category/category from the supported category order"
        )
    return expected, predicted


def layer2_metrics(results: Iterable[Any]) -> dict[str, float | int]:
    """Compute plain and ordinal-distance weighted accuracy for Layer 2."""

    total = correct = 0
    weighted_total = 0.0
    for item in results:
        expected, predicted = _layer2_values(item)
        total += 1
        correct += expected == predicted
        distance = abs(CATEGORY_ORDER.index(predicted) - CATEGORY_ORDER.index(expected))
        weighted_total += 1.0 - distance / (len(CATEGORY_ORDER) - 1)

    accuracy = correct / total if total else 0.0
    weighted_accuracy = weighted_total / total if total else 0.0
    return {
        "total": total,
        "correct": correct,
        "accuracy": accuracy,
        "plain_accuracy": accuracy,
        "weighted_accuracy": weighted_accuracy,
    }


def _percent(value: float | int) -> str:
    return f"{float(value) * 100:.1f}%"


def _print_layer1(evaluations: list[Layer1Evaluation]) -> dict[str, float | int]:
    print("Layer 1 — reference validity")
    print("label  expected  status      confidence  title")
    for evaluation in evaluations:
        result = evaluation.result
        label = "real" if evaluation.expected_real else "fake"
        print(
            f"{label:<6} {str(evaluation.expected_real):<8} "
            f"{result.status:<11} {result.confidence:>10.3f}  {result.entry.title}"
        )
    metrics = layer1_metrics(evaluations)
    print("metrics")
    print(
        "  TP={true_positive} FP={false_positive} FN={false_negative} TN={true_negative}".format(
            **metrics
        )
    )
    print(
        "  precision={precision} recall={recall} f1={f1} false_positive_rate={fpr}".format(
            precision=_percent(metrics["precision"]),
            recall=_percent(metrics["recall"]),
            f1=_percent(metrics["f1"]),
            fpr=_percent(metrics["false_positive_rate"]),
        )
    )
    return metrics


def _print_layer2(evaluations: list[Layer2Evaluation]) -> dict[str, float | int]:
    print("\nLayer 2 — citation support")
    print("case  expected              predicted             text_status")
    for index, evaluation in enumerate(evaluations, 1):
        result = evaluation.result
        print(
            f"{index:>4}  {evaluation.expected_category:<20} "
            f"{result.category:<20} {result.full_text_status}"
        )
    metrics = layer2_metrics(evaluations)
    print("metrics")
    print(
        f"  correct={metrics['correct']}/{metrics['total']} "
        f"accuracy={_percent(metrics['accuracy'])} "
        f"weighted_accuracy={_percent(metrics['weighted_accuracy'])}"
    )
    return metrics


def main(argv: Sequence[str] | None = None) -> int:
    """Run both offline benchmarks and return a process exit code."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)
    layer1 = run_layer1_benchmark()
    layer2 = run_layer2_benchmark()
    _print_layer1(layer1)
    _print_layer2(layer2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
