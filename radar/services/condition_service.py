"""Deterministic and semantic six-field condition comparison with numeric support."""

import math
import re

from radar.schemas import ConditionDifference, EmpiricalClaimContract


FIELDS = ("task", "dataset", "split", "metric", "comparator", "scope")
BLOCKING_FIELDS = {"task", "dataset", "metric", "comparator"}

# Expanded curated alias table covering common NLP/AI naming conventions.
ALIASES: dict[str, set[str]] = {
    "exact match": {"em", "exact-match", "exact match", "exact_match"},
    "macro-f1": {"macro f1", "macro-f1", "macro f-score", "macro f1 score", "macro avg f1"},
    "micro-f1": {"micro f1", "micro-f1", "micro f-score", "micro f1 score"},
    "weighted-f1": {"weighted f1", "weighted-f1", "weighted f-score"},
    "open-domain qa": {"open domain qa", "odqa", "open-domain question answering", "open qa"},
    "reading comprehension": {"reading comprehension", "rc", "machine reading comprehension", "mrc"},
    "median latency": {"p50 latency", "median retrieval latency", "median latency", "50th percentile latency"},
    "p99 latency": {"p99 latency", "99th percentile latency", "tail latency p99"},
    "throughput": {"throughput", "qps", "queries per second", "requests per second", "rps"},
    "squad": {"squad v1.1", "squad 1.1", "squadv1", "squad v1", "stanford question answering dataset"},
    "squad v2.0": {"squad v2.0", "squad v2", "squadv2", "squad 2.0", "squad2"},
    "natural questions": {"natural questions", "nq", "naturalquestions", "google natural questions"},
    "triviaqa": {"triviaqa", "trivia qa", "trivia qa"},
    "hotpotqa": {"hotpotqa", "hotpot qa", "hotpot qa"},
    "glue": {"glue benchmark", "glue", "general language understanding evaluation"},
    "superglue": {"superglue", "super glue", "super-glue"},
    "mnli": {"mnli", "multi-genre natural language inference", "multi nli"},
    "qnli": {"qnli", "question nli", "question-answering nli"},
    "bleu": {"bleu", "bilingual evaluation understudy", "bleu score"},
    "rouge-l": {"rouge-l", "rouge l", "rougel", "rouge lsum"},
    "rouge-1": {"rouge-1", "rouge 1", "rouge1"},
    "rouge-2": {"rouge-2", "rouge 2", "rouge2"},
    "bertscore": {"bertscore", "bert score", "bert-score"},
    "accuracy": {"accuracy", "acc", "top-1 accuracy", "top-1 acc", "top1 accuracy"},
    "exact match rate": {"em rate", "em score", "exact match rate"},
    "map": {"map", "mean average precision"},
    "mrr": {"mrr", "mean reciprocal rank"},
    "ndcg": {"ndcg", "normalized discounted cumulative gain"},
    "ndcg@10": {"ndcg@10", "ndcg at 10", "ndcg_10"},
    "recall@k": {"recall@k", "recall at k", "r@k"},
    "test set": {"test set", "test split", "test", "testing set"},
    "validation set": {"validation set", "val set", "dev set", "development set", "valid set"},
    "train set": {"train set", "training set", "train split"},
    "bert-base": {"bert-base", "bert base", "bert-base-uncased", "bert base uncased"},
    "bert-large": {"bert-large", "bert large", "bert-large-uncased"},
    "roberta-base": {"roberta-base", "roberta base"},
    "roberta-large": {"roberta-large", "roberta large"},
    "t5-base": {"t5-base", "t5 base"},
    "t5-large": {"t5-large", "t5 large"},
    "gpt-4": {"gpt-4", "gpt4", "gpt 4"},
    "gpt-4o": {"gpt-4o", "gpt4o", "gpt 4o"},
    "llama-3": {"llama-3", "llama3", "llama 3", "llama 3.1", "llama-3.1"},
}

# Numeric value extraction for approximate comparison.
_NUMERIC_PATTERN = re.compile(r"(\d+(?:\.\d+)?)\s*%?")


def normalize(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", value.lower())).strip()


def canonical(value: str) -> str:
    normalized = normalize(value)
    for key, values in ALIASES.items():
        if normalized in {normalize(item) for item in values | {key}}:
            return normalize(key)
    return normalized


def _extract_numeric(value: str) -> float | None:
    """Extract the first numeric value from a string, handling percentage and ranges."""
    if not value:
        return None
    cleaned = value.replace(",", "").replace("±", " ").replace("~", " ")
    match = _NUMERIC_PATTERN.search(cleaned)
    if match:
        return float(match.group(1))
    return None


def _numeric_compatible(own: float, incoming: float, tolerance: float = 0.05) -> bool:
    """Check if two numeric values are within relative tolerance."""
    if own == 0 and incoming == 0:
        return True
    if own == 0 or incoming == 0:
        return abs(own - incoming) < tolerance
    return abs(own - incoming) / max(abs(own), abs(incoming)) < tolerance


class ConditionService:
    def __init__(self, *, embedding_client=None):
        self.embedding_client = embedding_client

    def compare(
        self,
        own_contract: EmpiricalClaimContract | dict,
        incoming_contract: EmpiricalClaimContract | dict,
    ) -> list[ConditionDifference]:
        own = (
            own_contract
            if isinstance(own_contract, EmpiricalClaimContract)
            else EmpiricalClaimContract.model_validate(own_contract)
        )
        incoming = (
            incoming_contract
            if isinstance(incoming_contract, EmpiricalClaimContract)
            else EmpiricalClaimContract.model_validate(incoming_contract)
        )
        differences: list[ConditionDifference] = []
        for field in FIELDS:
            own_value = getattr(own, field)
            incoming_value = getattr(incoming, field)
            if not own_value or not incoming_value:
                status = "unknown"
                explanation = "At least one source does not report this condition."
            elif normalize(own_value) == normalize(incoming_value):
                status = "match"
                explanation = "Values match after normalization."
            elif canonical(own_value) == canonical(incoming_value):
                status = "compatible_alias"
                explanation = "Values match through a curated alias."
            elif self._semantic_match(own_value, incoming_value):
                status = "compatible_alias"
                explanation = "Values match through semantic similarity."
            elif self._numeric_match(own_value, incoming_value):
                status = "partial"
                explanation = "Numeric values are within tolerance; scopes may differ."
            elif self._substring_with_context(own_value, incoming_value):
                status = "partial"
                explanation = "Values overlap but scopes are not identical."
            else:
                status = "mismatch"
                explanation = "Reported values differ."
            differences.append(
                ConditionDifference(
                    field=field,
                    own_value=own_value,
                    incoming_value=incoming_value,
                    status=status,
                    explanation=explanation,
                )
            )
        return differences

    def _semantic_match(self, own_value: str, incoming_value: str) -> bool:
        """Check semantic similarity when string matching fails."""
        if self.embedding_client is None:
            return False
        try:
            vectors = self.embedding_client.embed([own_value, incoming_value])
            if vectors.shape[0] != 2:
                return False
            similarity = float(vectors[0] @ vectors[1])
            return similarity > 0.88
        except Exception:
            return False

    def _numeric_match(self, own_value: str, incoming_value: str) -> bool:
        """Check if values are numeric and within tolerance."""
        own_num = _extract_numeric(own_value)
        incoming_num = _extract_numeric(incoming_value)
        if own_num is None or incoming_num is None:
            return False
        return _numeric_compatible(own_num, incoming_num)

    def _substring_with_context(self, own_value: str, incoming_value: str) -> bool:
        """Smarter substring containment that avoids false matches."""
        n_own = normalize(own_value)
        n_incoming = normalize(incoming_value)

        if n_incoming in n_own or n_own in n_incoming:
            shorter = n_own if len(n_own) <= len(n_incoming) else n_incoming
            if len(shorter) >= 4:
                return True
        return False

    def overall_comparability(self, differences: list[ConditionDifference]) -> str:
        if any(
            item.field in BLOCKING_FIELDS and item.status == "mismatch"
            for item in differences
        ):
            return "incompatible"
        required_unknowns = sum(
            item.field in BLOCKING_FIELDS and item.status == "unknown"
            for item in differences
        )
        if required_unknowns >= 2:
            return "unknown"
        if any(
            item.status in {"partial", "unknown", "mismatch"}
            for item in differences
        ):
            return "partial"
        return "compatible"
