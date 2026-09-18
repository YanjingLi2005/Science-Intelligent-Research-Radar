"""Deterministic NLI second opinion for directional impact stances (P2).

The LLM's supports/challenges judgment is a hallucination risk; this module
provides an offline entailment/contradiction check of the incoming evidence
quote against the claim statement, using a cross-encoder NLI model via
transformers directly (no sentence-transformers dependency). The model
downloads on first use; any failure degrades to ``available == False`` and
callers skip the check — the LLM judgment stands alone, and the fidelity
stats record the check as unavailable.
"""

import logging

logger = logging.getLogger(__name__)

# Cross-encoder NLI model; label order is (contradiction, entailment, neutral).
DEFAULT_NLI_MODEL = "cross-encoder/nli-deberta-v3-base"


class NLIContradictionChecker:
    """Check whether an incoming evidence quote contradicts or entails a claim."""

    def __init__(self, model_name: str = DEFAULT_NLI_MODEL):
        self.model_name = model_name
        self._model = None
        self._tokenizer = None

    def _ensure_loaded(self) -> None:
        if self._model is not None:
            return
        from transformers import AutoModelForSequenceClassification, AutoTokenizer

        self._tokenizer = AutoTokenizer.from_pretrained(self.model_name)
        self._model = AutoModelForSequenceClassification.from_pretrained(
            self.model_name
        )
        self._model.eval()

    def check(self, claim: str, evidence: str) -> dict | None:
        """NLI verdict of the evidence against the claim.

        premise = incoming evidence, hypothesis = claim statement. Returns
        ``{"label": "contradiction"|"entailment"|"neutral", "scores": {...}}``
        or None when the model is unavailable (not installed, no network for
        the first download, model error).
        """
        try:
            self._ensure_loaded()
        except Exception as exc:
            logger.warning("NLI model unavailable (%s): %s", self.model_name, exc)
            return None
        import torch

        inputs = self._tokenizer(
            [(evidence, claim)],
            return_tensors="pt",
            truncation=True,
            max_length=512,
        )
        with torch.no_grad():
            logits = self._model(**inputs).logits
            probs = torch.softmax(logits, dim=-1)[0]
        labels = ["contradiction", "entailment", "neutral"]
        scores = {label: float(prob) for label, prob in zip(labels, probs)}
        label = labels[int(probs.argmax())]
        return {"label": label, "scores": scores}
