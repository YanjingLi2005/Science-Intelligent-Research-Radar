"""PaperQA2 adapter for contradiction detection and evidence retrieval.

Uses the SAME LLM configuration as the rest of the product (user-provided
DeepSeek / OpenAI / Ollama API), not a hard-coded model.

Requires: pip install paper-qa
When paper-qa is not installed, degrades gracefully to a no-op.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class PaperQA2Result:
    answer: str
    contexts: list[dict] = field(default_factory=list)
    evidence_chunks: list[str] = field(default_factory=list)
    contradiction_score: float = 0.0
    supports_claim: bool | None = None
    raw_output: dict = field(default_factory=dict)


class PaperQA2Adapter:
    """Integration with PaperQA2 using the user's own LLM configuration.

    The analysis model configured via Settings (LOCAL_LLM_MODEL or the
    remote LLM_PROVIDER/LLM_MODEL/LLM_BASE_URL/LLM_API_KEY triple) is reused
    here via litellm-compatible model names, so the whole product speaks to
    exactly one provider the user chose.
    """

    def __init__(
        self,
        *,
        settings: Any | None = None,
        embedding_model: str = "",
        paper_directory: str | Path | None = None,
        extra_settings: dict | None = None,
    ):
        from radar.config import get_settings
        self._settings = settings or get_settings()
        self._paper_dir = Path(paper_directory) if paper_directory else None
        self._extra = extra_settings or {}

        # Resolve the user's LLM into a litellm model name
        self._llm_name, self._llm_config = self._resolve_user_llm(self._settings)
        # Embedding: reuse the configured embedding provider if present
        self._embedding, self._embedding_config = self._resolve_user_embedding(self._settings)
        if embedding_model:
            self._embedding = embedding_model

    @property
    def available(self) -> bool:
        try:
            import paperqa  # noqa: F401
            return True
        except ImportError:
            return False

    @property
    def llm_configured(self) -> bool:
        """True when the user has a usable LLM config for PaperQA2."""
        return bool(self._llm_name) and bool(self._llm_config)

    @staticmethod
    def _resolve_user_llm(settings) -> tuple[str | None, dict]:
        """Map the product Settings to a litellm model name + config.

        Local Ollama wins (privacy-first), then remote OpenAI-compatible.
        Returns (None, {}) when nothing is configured so the adapter stays
        inert instead of silently using a different provider.
        """
        if settings.local_llm_model:
            return (
                f"ollama/{settings.local_llm_model}",
                {
                    "model_list": [{
                        "model_name": f"ollama/{settings.local_llm_model}",
                        "litellm_params": {
                            "model": f"ollama/{settings.local_llm_model}",
                            "api_base": settings.local_llm_base_url,
                        },
                    }],
                },
            )
        if settings.llm_model and settings.llm_base_url and settings.llm_api_key:
            provider = (settings.llm_provider or "").strip().lower()
            # litellm needs a provider prefix to route non-standard model names.
            # DeepSeek and OpenAI-compatible endpoints are prefixed explicitly.
            llm_name = settings.llm_model
            if provider in {"deepseek", "openai"}:
                llm_name = f"{provider}/{settings.llm_model}"
            return (
                llm_name,
                {
                    "model_list": [{
                        "model_name": llm_name,
                        "litellm_params": {
                            "model": llm_name,
                            "api_base": settings.llm_base_url,
                            "api_key": settings.llm_api_key,
                        },
                    }],
                },
            )
        return None, {}

    @staticmethod
    def _resolve_user_embedding(settings) -> tuple[str | None, dict]:
        """Reuse the user's embedding provider for PaperQA2 semantic search."""
        if settings.embedding_provider and settings.embedding_model:
            if settings.embedding_provider == "ollama":
                return (
                    f"ollama/{settings.embedding_model}",
                    {
                        "model_list": [{
                            "model_name": f"ollama/{settings.embedding_model}",
                            "litellm_params": {
                                "model": f"ollama/{settings.embedding_model}",
                                "api_base": settings.embedding_base_url,
                            },
                        }],
                    },
                )
            if settings.embedding_base_url and settings.embedding_api_key:
                return (
                    settings.embedding_model,
                    {
                        "model_list": [{
                            "model_name": settings.embedding_model,
                            "litellm_params": {
                                "model": settings.embedding_model,
                                "api_base": settings.embedding_base_url,
                                "api_key": settings.embedding_api_key,
                            },
                        }],
                    },
                )
        # No embedding configured: fall back to the same provider as the LLM
        return None, {}

    def _build_settings(self):
        """Build paperqa Settings from the user's LLM/embedding config."""
        from paperqa import Settings as PQSettings

        pq_kwargs: dict[str, Any] = {}
        if self._llm_name:
            pq_kwargs["llm"] = self._llm_name
            pq_kwargs["llm_config"] = self._llm_config
            # The agent that selects tools must use the same user LLM
            pq_kwargs["agent"] = {
                "agent_llm": self._llm_name,
                "agent_llm_config": self._llm_config,
            }
        if self._embedding:
            pq_kwargs["embedding"] = self._embedding
            if self._embedding_config:
                pq_kwargs["embedding_config"] = self._embedding_config
        pq_kwargs.update(self._extra)
        return PQSettings(**pq_kwargs)

    @staticmethod
    def _collect_texts(
        paper_paths: list[str] | None, manuscript_text: str
    ) -> list[tuple[str, str]]:
        """Gather (name, content) pairs to feed PaperQA2 as its document library."""
        texts: list[tuple[str, str]] = []
        if manuscript_text and manuscript_text.strip():
            texts.append(("incoming-paper", manuscript_text))
        for path in paper_paths or []:
            try:
                content = Path(path).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if content.strip():
                texts.append((Path(path).name, content))
        return texts

    def _run_query(
        self,
        query: str,
        max_sources: int,
        texts: list[tuple[str, str]],
    ) -> Any | None:
        """Run one agent query over the supplied texts.

        The texts are loaded into a paperqa ``Docs`` object so the agent
        searches the incoming paper's actual content instead of an empty
        library. Returns the answer response, or None when the run failed
        (paper-qa missing, LLM/embedding misconfigured, or an error) — a None
        return means "feature unavailable", never "no evidence found".
        """
        try:
            import asyncio
            from paperqa import Doc, Docs, Text, agent_query
            settings = self._build_settings()

            async def run() -> Any:
                docs = Docs()
                for name, content in texts:
                    doc = Doc(
                        docname=name,
                        dockey=f"incoming:{name}",
                        citation=f"(Incoming paper: {name})",
                    )
                    await docs.aadd_texts(
                        [Text(text=content, name=name, doc=doc)],
                        doc=doc,
                        settings=settings,
                    )
                return await agent_query(
                    query=query,
                    settings=settings,
                    docs=docs,
                    agent_type="fake",  # hard-coded path: search → evidence → answer
                )

            return asyncio.run(run())
        except Exception:
            return None

    def _search(
        self,
        claim: str,
        texts: list[tuple[str, str]],
        *,
        contradict: bool,
        max_sources: int,
    ) -> list[PaperQA2Result]:
        """Run one directed search over the given texts."""
        if not self.available or not self.llm_configured:
            return []

        if contradict:
            query = (
                f"Find papers that CONTRADICT or CHALLENGE this claim: {claim}. "
                f"Look for evidence that opposes or weakens the claim."
            )
        else:
            query = (
                f"Find papers that SUPPORT or provide evidence FOR this claim: {claim}. "
                f"Look for evidence that confirms or strengthens the claim."
            )

        response = self._run_query(query, max_sources, texts)
        if response is None:
            return []  # unavailable — the run failed
        answer = getattr(response, "answer", "") or ""
        if not answer:
            return []  # ran but found nothing — a real negative
        return [
            PaperQA2Result(
                answer=answer,
                contexts=getattr(response, "contexts", []) or [],
                contradiction_score=(
                    self._estimate_contradiction(answer, claim) if contradict else 0.0
                ),
                supports_claim=not contradict,
                raw_output={"query": query, "answer": answer},
            )
        ]

    def search_contradictions(
        self,
        claim: str,
        *,
        paper_paths: list[str] | None = None,
        manuscript_text: str = "",
        max_sources: int = 5,
    ) -> list[PaperQA2Result]:
        """Search the given papers for content that contradicts the claim."""
        return self._search(
            claim,
            self._collect_texts(paper_paths, manuscript_text),
            contradict=True,
            max_sources=max_sources,
        )

    def search_supporting(
        self,
        claim: str,
        *,
        paper_paths: list[str] | None = None,
        manuscript_text: str = "",
        max_sources: int = 5,
    ) -> list[PaperQA2Result]:
        """Search the given papers for content that supports the claim."""
        return self._search(
            claim,
            self._collect_texts(paper_paths, manuscript_text),
            contradict=False,
            max_sources=max_sources,
        )

    def assess_claim(
        self,
        claim: str,
        *,
        manuscript_text: str = "",
        max_sources: int = 10,
    ) -> dict[str, Any]:
        """Comprehensive claim assessment: support, contradiction, evidence quality.

        ``manuscript_text`` is the incoming paper's content: it is loaded into
        PaperQA2's document library so the assessment reads the paper instead
        of querying an empty index.
        """
        if not self.available or not self.llm_configured:
            return {
                "contradictions": [],
                "supporting": [],
                "verdict": "unavailable",
                "evidence_quality": "unknown",
            }

        texts = self._collect_texts(None, manuscript_text)
        contradictions = self._search(claim, texts, contradict=True, max_sources=max_sources)
        supporting = self._search(claim, texts, contradict=False, max_sources=max_sources)

        # Verdicts must not come from mere keyword presence: an answer like
        # "Evidence supports the claim" to the CONTRADICT query, or "No
        # evidence found" to the SUPPORT query, would otherwise flip the
        # verdict. Require an explicit negative to suppress, and a
        # contradiction score at or above the weak-word floor to count.
        contradiction_items = [
            {"answer": r.answer, "score": r.contradiction_score}
            for r in contradictions
            if r.contradiction_score >= 0.3
            and not self._is_explicit_negative(r.answer)
        ]
        supporting_items = [
            {"answer": r.answer}
            for r in supporting
            if not self._is_explicit_negative(r.answer)
        ]

        return {
            "contradictions": contradiction_items,
            "supporting": supporting_items,
            "verdict": (
                "challenged"
                if contradiction_items
                else "supported"
                if supporting_items
                else "unverified"
            ),
            "evidence_quality": (
                "high" if (contradiction_items or supporting_items) else "low"
            ),
        }

    @staticmethod
    def _is_explicit_negative(answer: str) -> bool:
        """True when the answer itself states no contradicting/supporting
        evidence exists, so the keyword heuristic must not treat it as one."""
        lowered = answer.lower()
        negatives = (
            "no evidence", "did not find", "could not find", "found no",
            "no paper", "no contradiction", "does not contradict",
            "does not challenge", "not find any", "none of the papers",
        )
        return any(phrase in lowered for phrase in negatives)

    @staticmethod
    def _estimate_contradiction(answer: str, claim: str) -> float:
        if not answer:
            return 0.0
        answer_lower = answer.lower()
        strong_contradiction_words = [
            "contradict", "refute", "disprove", "inconsistent", "fails to reproduce",
            "opposite", "does not hold", "invalid", "wrong", "incorrect",
        ]
        weak_contradiction_words = [
            "does not fully support", "partially", "limited evidence",
            "alternative explanation", "boundary condition",
        ]

        strong_count = sum(1 for w in strong_contradiction_words if w in answer_lower)
        weak_count = sum(1 for w in weak_contradiction_words if w in answer_lower)

        score = min(1.0, strong_count * 0.3 + weak_count * 0.1)
        return score
