"""Context window management, token budgeting, and intelligent conversation pruning.

Implements multi-tier context retention:
- Tier 0 (Pinned): System instructions, research question, grounding constraints.
- Tier 1 (Grounding): Retrieved paper sources and key evidence snippets.
- Tier 2 (Sliding Window): Latest user turn and recent dialogue turns.
- Tier 3 (Summary Compression): Older turns exceeding token budget are summarized into concise notes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from radar.config import Settings, get_settings
from radar.llm.text_utils import _chars_per_token_for, _cjk_ratio


def estimate_tokens(text: str) -> int:
    """Estimate token count accounting for CJK, ASCII, code and LaTeX blocks."""
    if not text:
        return 0
    # Blend chars-per-token based on CJK ratio
    chars_per_token = _chars_per_token_for(text)
    token_est = int(len(text) / max(chars_per_token, 0.5))
    # Code / LaTeX formulas / whitespace overhead correction
    math_or_code_spans = len(re.findall(r"(\$\$.*?\$\$|```.*?```|\$.*?\$)", text, re.DOTALL))
    return max(token_est + math_or_code_spans * 2, 1)


@dataclass
class ChatTurn:
    """A single turn in a conversation."""

    role: Literal["system", "user", "assistant"]
    content: str
    pinned: bool = False
    tokens: int = field(init=False)

    def __post_init__(self):
        self.tokens = estimate_tokens(self.content)


@dataclass
class ContextBudget:
    """Token budget configuration derived from active model settings."""

    total_context_tokens: int = 32_000
    reserve_output_tokens: int = 4_096
    max_grounding_tokens: int = 8_000
    max_history_tokens: int = 16_000

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> ContextBudget:
        settings = settings or get_settings()
        if settings.llm_context_tokens is not None:
            total = settings.llm_context_tokens
        elif settings.local_llm_model:
            total = 28_000
        else:
            total = 60_000

        output_reserve = min(settings.llm_max_tokens, 8_192)
        grounding = min(int(total * 0.25), 12_000)
        history = max(total - output_reserve - grounding - 2_000, 4_000)
        return cls(
            total_context_tokens=total,
            reserve_output_tokens=output_reserve,
            max_grounding_tokens=grounding,
            max_history_tokens=history,
        )


class ContextManager:
    """Manages dialogue history pruning, token allocation, and prompt formatting."""

    def __init__(self, budget: ContextBudget | None = None):
        self.budget = budget or ContextBudget.from_settings()

    def summarize_older_turns(self, turns: list[ChatTurn]) -> str:
        """Create a compact summary of older turns that cannot fit in the window."""
        if not turns:
            return ""
        topics: list[str] = []
        for turn in turns:
            snippet = " ".join(turn.content.split())
            if len(snippet) > 80:
                snippet = snippet[:77] + "..."
            if turn.role == "user":
                topics.append(f"用户询问: {snippet}")
            elif turn.role == "assistant":
                topics.append(f"助手要点: {snippet}")
        return "；".join(topics)

    def prune_conversation(
        self,
        history: list[dict[str, str]],
        *,
        current_question: str,
        system_prompt: str = "",
        grounding_context: str = "",
    ) -> list[dict[str, str]]:
        """Prune dialogue history to strictly satisfy token budgets while preserving pinned context."""
        # 1. Pinned System + Current Question + Grounding Tokens
        sys_tokens = estimate_tokens(system_prompt) if system_prompt else 0
        current_q_tokens = estimate_tokens(current_question)
        grounding_tokens = estimate_tokens(grounding_context) if grounding_context else 0

        # Available tokens for history
        available_history_budget = (
            self.budget.total_context_tokens
            - self.budget.reserve_output_tokens
            - sys_tokens
            - current_q_tokens
            - grounding_tokens
            - 500  # Safety overhead
        )
        available_history_budget = max(
            min(available_history_budget, self.budget.max_history_tokens), 0
        )

        turns = [
            ChatTurn(
                role=item.get("role", "user"),  # type: ignore
                content=item.get("content", ""),
            )
            for item in history
            if item.get("content")
        ]

        # 2. Sliding window selection from newest to oldest
        kept_turns: list[ChatTurn] = []
        evicted_turns: list[ChatTurn] = []
        consumed_tokens = 0

        for turn in reversed(turns):
            if consumed_tokens + turn.tokens <= available_history_budget:
                kept_turns.insert(0, turn)
                consumed_tokens += turn.tokens
            else:
                evicted_turns.insert(0, turn)

        # 3. If older turns were evicted, inject a concise summary as a bridge turn
        result: list[dict[str, str]] = []
        if evicted_turns:
            summary = self.summarize_older_turns(evicted_turns)
            if summary:
                result.append(
                    {
                        "role": "system",
                        "content": f"[前序对话要点摘要 (共省略 {len(evicted_turns)} 条历史)]: {summary}",
                    }
                )

        for turn in kept_turns:
            result.append({"role": turn.role, "content": turn.content})

        return result

    def format_qa_prompt(
        self,
        *,
        question: str,
        sources_text: str,
        history: list[dict[str, str]] | None = None,
    ) -> str:
        """Format a literature QA prompt with pruned dialogue history and grounding sources."""
        pruned_history = (
            self.prune_conversation(
                history,
                current_question=question,
                grounding_context=sources_text,
            )
            if history
            else []
        )

        sections: list[str] = []
        if pruned_history:
            history_lines: list[str] = ["## 对话历史上下文 (Dialogue Context)"]
            for msg in pruned_history:
                role_label = "用户" if msg["role"] == "user" else ("助手" if msg["role"] == "assistant" else "系统摘要")
                history_lines.append(f"{role_label}: {msg['content']}")
            sections.append("\n".join(history_lines))

        sections.append("## 已检索文献来源与证据 (Retrieved Sources)")
        sections.append(sources_text)

        sections.append("## 当前问题 (Current Question)")
        sections.append(question)

        return "\n\n".join(sections)
