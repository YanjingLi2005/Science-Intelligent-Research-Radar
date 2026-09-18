"""Literature Q&A over the papers already collected in a research case.

This is the "ask your collected literature" capability seen in AI-scholar
products: instead of searching the web again, the service uses the product's
own hybrid retrieval (FTS/BM25/vector) to pull the most relevant stored
snapshots, then answers the user's question with source-grounded citations.

The answer always points back to real stored papers; the LLM is never asked
to invent source ids.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from radar.config import Settings, get_settings
from radar.db import SessionLocal, session_scope
from radar.embeddings.factory import configured_embedding_client
from radar.llm.base import LLMClient
from radar.llm.context_manager import ContextManager
from radar.llm.factory import build_analysis_llm
from radar.llm.provider import ProviderLLMClient
from radar.llm.text_utils import truncate_for_prompt
from radar.models import AuditEvent, ModelRun, ResearchCase, Source, SourceSnapshot
from radar.services.retrieval_service import RetrievalService
from radar.vector_store import build_vector_store

PROMPT_DIR = Path(__file__).parents[1] / "llm" / "prompts"

_QA_SOURCE_CHARS = 6_000
_QA_MAX_SOURCES = 6


class LiteratureQASource(BaseModel):
    source_id: str
    title: str
    url: str = ""
    year: str | None = None
    venue: str | None = None
    evidence_snippets: list[str] = Field(default_factory=list)


class LiteratureQAAnswer(BaseModel):
    answer: str = Field(description="Direct answer to the user's question")
    source_ids: list[str] = Field(
        default_factory=list,
        description="Stored source ids that support the answer",
    )
    evidence_snippets: list[str] = Field(
        default_factory=list,
        description="Short verbatim-ish snippets from the cited papers",
    )
    uncertainty: str = Field(default="", description="What is still unknown or weak")


def _prompt(name: str, payload: dict) -> str:
    instructions = (PROMPT_DIR / name).read_text(encoding="utf-8").strip()
    return f"{instructions}\n\nINPUT JSON:\n{json.dumps(payload, ensure_ascii=False, indent=2)}"


class LiteratureQAService:
    """Answer questions from the case's stored literature corpus."""

    def __init__(
        self,
        session_factory: sessionmaker[Session] = SessionLocal,
        *,
        llm_client: LLMClient | None = None,
        settings: Settings | None = None,
        retrieval: RetrievalService | None = None,
    ):
        self.session_factory = session_factory
        self.settings = settings or get_settings()
        self.llm_client = (
            llm_client
            or build_analysis_llm(self.settings)
            or ProviderLLMClient(self.settings)
        )
        if retrieval is not None:
            self.retrieval = retrieval
        else:
            try:
                embedding_client = configured_embedding_client(self.settings)
            except Exception:
                embedding_client = None
            try:
                vector_store = (
                    build_vector_store(self.settings.vector_store_dir)
                    if self.settings.vector_store_enabled
                    else None
                )
            except Exception:
                vector_store = None
            self.retrieval = RetrievalService(
                session_factory,
                embedding_client=embedding_client,
                vector_store=vector_store,
            )
        self.context_manager = ContextManager()

    def answer(
        self,
        case_id: str,
        question: str,
        *,
        top_k: int = _QA_MAX_SOURCES,
        history: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        """Answer a researcher's question grounded in stored sources with context pruning."""
        question = " ".join(question.split()).strip()
        if not question:
            return {
                "answer": "请输入具体的研究问题。",
                "sources": [],
                "uncertainty": "",
                "warnings": ["empty_question"],
            }

        with session_scope(self.session_factory) as session:
            if session.get(ResearchCase, case_id) is None:
                raise ValueError(f"case not found: {case_id}")

        ranked = self.retrieval.rank_sources(question, top_k=top_k)
        sources = self._load_sources([snapshot_id for snapshot_id, _, _ in ranked])
        if not sources:
            return {
                "answer": (
                    "当前案例还没有足够的已收集论文。请先运行一次文献雷达扫描或深度调研，"
                    "收集与当前研究问题相关的论文后再提问。"
                ),
                "sources": [],
                "uncertainty": "",
                "warnings": ["no_sources"],
            }

        source_cards = [
            {
                "source_id": item["source_id"],
                "title": item["title"],
                "year": item.get("year"),
                "venue": item.get("venue"),
                "url": item.get("url"),
                "text": truncate_for_prompt(
                    item.get("content_text") or item.get("abstract") or "",
                    max_chars=_QA_SOURCE_CHARS,
                    purpose="literature QA source",
                ),
            }
            for item in sources
        ]

        pruned_history = (
            self.context_manager.prune_conversation(
                history,
                current_question=question,
                grounding_context=json.dumps(source_cards, ensure_ascii=False),
            )
            if history
            else []
        )

        qa_payload: dict[str, Any] = {
            "question": question,
            "sources": source_cards,
        }
        if pruned_history:
            qa_payload["dialogue_history"] = pruned_history

        try:
            output = self.llm_client.generate_structured(
                stage="literature_qa",
                prompt=_prompt(
                    "literature_qa.txt",
                    qa_payload,
                ),
                response_model=LiteratureQAAnswer,
            )
        except Exception:
            # Deterministic fallback: answer from the top source's abstract.
            top = sources[0]
            output = LiteratureQAAnswer(
                answer=(
                    f"基于当前已收集论文，最相关的来源是《{top['title']}》。"
                    "（未配置或调用 LLM 失败，以下为摘要级答案，请人工阅读原文。）"
                ),
                source_ids=[top["source_id"]],
                evidence_snippets=[(top.get("abstract") or top.get("content_text") or "")[:400]],
                uncertainty="LLM 不可用，未做综合推理。",
            )

        # Only keep source ids that actually exist in the stored corpus.
        valid_ids = {item["source_id"] for item in sources}
        output.source_ids = [sid for sid in output.source_ids if sid in valid_ids]
        if not output.source_ids and sources:
            output.source_ids = [sources[0]["source_id"]]

        self._record_model_run(case_id, stage="literature_qa", prompt=question, output=output.model_dump())

        return {
            "answer": output.answer,
            "sources": [
                {
                    "source_id": item["source_id"],
                    "title": item["title"],
                    "url": item.get("url", ""),
                    "year": item.get("year"),
                    "venue": item.get("venue"),
                    "evidence_snippets": output.evidence_snippets
                    if item["source_id"] in output.source_ids
                    else [],
                }
                for item in sources
                if item["source_id"] in output.source_ids
            ],
            "uncertainty": output.uncertainty,
            "warnings": ["llm_fallback"] if output.answer.startswith("基于当前已收集论文") else [],
        }

    def answer_stream(
        self,
        case_id: str,
        question: str,
        *,
        top_k: int = _QA_MAX_SOURCES,
        history: list[dict[str, str]] | None = None,
    ):
        """Stream an answer token-by-token with real-time sources emission and context management."""
        question = " ".join(question.split()).strip()
        if not question:
            yield {
                "event": "error",
                "data": {"error": "question_required"},
            }
            return

        with session_scope(self.session_factory) as session:
            if session.get(ResearchCase, case_id) is None:
                yield {
                    "event": "error",
                    "data": {"error": f"case not found: {case_id}"},
                }
                return

        ranked = self.retrieval.rank_sources(question, top_k=top_k)
        sources = self._load_sources([snapshot_id for snapshot_id, _, _ in ranked])
        if not sources:
            yield {
                "event": "sources",
                "data": {"sources": []},
            }
            no_source_msg = "当前案例还没有足够的已收集论文。请先运行一次文献雷达扫描或深度调研。"
            yield {
                "event": "token",
                "data": {"delta": no_source_msg},
            }
            yield {
                "event": "done",
                "data": {
                    "answer": no_source_msg,
                    "sources": [],
                    "uncertainty": "",
                    "warnings": ["no_sources"],
                },
            }
            return

        formatted_sources = [
            {
                "source_id": item["source_id"],
                "title": item["title"],
                "url": item.get("url", ""),
                "year": item.get("year"),
                "venue": item.get("venue"),
                "evidence_snippets": [item.get("abstract", "")[:300]] if item.get("abstract") else [],
            }
            for item in sources
        ]

        # 1. Immediately push the retrieved sources to client
        yield {
            "event": "sources",
            "data": {"sources": formatted_sources},
        }

        source_cards = [
            {
                "source_id": item["source_id"],
                "title": item["title"],
                "year": item.get("year"),
                "venue": item.get("venue"),
                "url": item.get("url"),
                "text": truncate_for_prompt(
                    item.get("content_text") or item.get("abstract") or "",
                    max_chars=_QA_SOURCE_CHARS,
                    purpose="literature QA source",
                ),
            }
            for item in sources
        ]

        pruned_history = (
            self.context_manager.prune_conversation(
                history,
                current_question=question,
                grounding_context=json.dumps(source_cards, ensure_ascii=False),
            )
            if history
            else []
        )

        history_section = ""
        if pruned_history:
            history_section = (
                f"DIALOGUE HISTORY:\n{json.dumps(pruned_history, ensure_ascii=False, indent=2)}\n\n"
            )

        prompt_str = (
            f"You are a scientific literature assistant. Answer the user's question directly and concisely based on the following sources. "
            f"Ground your statements in the cited sources.\n\n"
            f"{history_section}"
            f"QUESTION: {question}\n\n"
            f"SOURCES:\n{json.dumps(source_cards, ensure_ascii=False, indent=2)}\n\n"
            f"ANSWER:"
        )

        accumulated_chunks: list[str] = []
        try:
            for token in self.llm_client.stream_text(stage="literature_qa", prompt=prompt_str):
                accumulated_chunks.append(token)
                yield {
                    "event": "token",
                    "data": {"delta": token},
                }
        except Exception:
            if not accumulated_chunks:
                top = sources[0]
                fallback_msg = (
                    f"基于当前已收集论文，最相关的来源是《{top['title']}》。"
                    "（未配置或调用 LLM 失败，请人工阅读原文。）"
                )
                yield {
                    "event": "token",
                    "data": {"delta": fallback_msg},
                }
                accumulated_chunks.append(fallback_msg)

        full_answer = "".join(accumulated_chunks)
        self._record_model_run(
            case_id,
            stage="literature_qa_stream",
            prompt=question,
            output={"answer": full_answer, "sources": formatted_sources},
        )

        yield {
            "event": "done",
            "data": {
                "answer": full_answer,
                "sources": formatted_sources,
                "uncertainty": "",
                "warnings": [],
            },
        }


    def _load_sources(self, snapshot_ids: list[str]) -> list[dict]:
        if not snapshot_ids:
            return []
        with session_scope(self.session_factory) as session:
            rows = session.execute(
                select(
                    SourceSnapshot.id,
                    SourceSnapshot.title,
                    SourceSnapshot.abstract,
                    SourceSnapshot.content_text,
                    Source.url,
                    Source.venue,
                    Source.published_at,
                )
                .join(Source, Source.id == SourceSnapshot.source_id)
                .where(SourceSnapshot.id.in_(snapshot_ids))
            ).all()
            return [
                {
                    "source_id": row.id,
                    "title": row.title,
                    "abstract": row.abstract,
                    "content_text": row.content_text,
                    "url": row.url,
                    "venue": row.venue,
                    "year": row.published_at.strftime("%Y") if row.published_at else None,
                }
                for row in rows
            ]

    def _record_model_run(self, case_id: str, *, stage: str, prompt: str, output: dict) -> None:
        receipt = getattr(self.llm_client, "last_receipt", {}) or {}
        usage = receipt.get("usage") or {}
        try:
            with session_scope(self.session_factory) as session:
                session.add(
                    ModelRun(
                        id=str(uuid4()),
                        stage=stage,
                        case_id=case_id,
                        provider=getattr(self.llm_client, "provider_name", "unknown"),
                        model=getattr(self.llm_client, "model_name", "unknown"),
                        prompt_hash=hashlib.sha256(prompt.encode()).hexdigest(),
                        schema_version=f"{stage}.v1",
                        input_refs_json=[],
                        raw_response=receipt.get("raw_response", ""),
                        parsed_output_json=output,
                        validation_json={},
                        input_tokens=int(usage.get("prompt_tokens", 0)),
                        output_tokens=int(usage.get("completion_tokens", 0)),
                        latency_ms=int(receipt.get("latency_ms", 0)),
                    )
                )
                session.add(
                    AuditEvent(
                        id=str(uuid4()),
                        case_id=case_id,
                        event_type=f"{stage}_executed",
                        object_type="LiteratureQA",
                        object_id=case_id,
                        payload_json={"stage": stage},
                        actor_type="model",
                        actor_id="literature_qa",
                    )
                )
        except Exception:
            pass
