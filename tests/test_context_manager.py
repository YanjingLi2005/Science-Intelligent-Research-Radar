"""Unit tests for context window management, token budgeting, and dialogue pruning."""

from radar.llm.context_manager import ContextBudget, ContextManager, estimate_tokens
from radar.models import ResearchCase
from radar.services.literature_qa_service import LiteratureQAService, LiteratureQAAnswer


def test_estimate_tokens_english_and_cjk():
    # English text
    eng = "Retrieval augmented generation improves domain specific question answering."
    eng_tokens = estimate_tokens(eng)
    assert 10 <= eng_tokens <= 30

    # CJK text (denser token density)
    cjk = "文献智能问答系统能够结合知识库与大模型进行事实核查与证据检索。"
    cjk_tokens = estimate_tokens(cjk)
    assert cjk_tokens >= len(cjk) / 2.0

    # LaTeX math formula spans
    latex = "The loss function is defined as $$L_{reg} = \\sum_{i=1}^n (y_i - \\hat{y}_i)^2$$."
    latex_tokens = estimate_tokens(latex)
    assert latex_tokens > 10


def test_context_manager_fits_short_history():
    budget = ContextBudget(total_context_tokens=10_000, max_history_tokens=5_000)
    mgr = ContextManager(budget)

    history = [
        {"role": "user", "content": "论文的主要贡献是什么？"},
        {"role": "assistant", "content": "主要提出了一个新的自注意力机制。"},
    ]

    pruned = mgr.prune_conversation(history, current_question="在哪个数据集上验证的？")
    assert len(pruned) == 2
    assert pruned[0]["content"] == "论文的主要贡献是什么？"
    assert pruned[1]["content"] == "主要提出了一个新的自注意力机制。"


def test_context_manager_pruning_and_summary_compression():
    # Set a budget of 150 tokens to force pruning of older large turns
    budget = ContextBudget(
        total_context_tokens=2_000,
        reserve_output_tokens=500,
        max_grounding_tokens=500,
        max_history_tokens=150,
    )
    mgr = ContextManager(budget)

    # 6 turns of conversation with substantial text (~400 tokens total)
    history = [
        {"role": "user", "content": "第一轮提问：" + "关于大语言模型长文本扩展的详细机制解析" * 10},
        {"role": "assistant", "content": "第一轮回答：" + "通过位置编码插值与注意力压缩实现超长上下文" * 10},
        {"role": "user", "content": "第二轮提问：" + "对比 RoPE 与 ALiBi 在外推性上的优劣" * 10},
        {"role": "assistant", "content": "第二轮回答：" + "ALiBi 具有更线性衰减的偏置，外推鲁棒性更好" * 10},
        {"role": "user", "content": "最近一轮提问：基准数据集表现如何？"},
        {"role": "assistant", "content": "最近一轮回答：在 L-Eval 和 Needle-in-a-Haystack 上均达到 98% 准确率。"},
    ]

    pruned = mgr.prune_conversation(
        history,
        current_question="当前问题：有开源权重发布吗？",
    )

    # Must retain newest turn and have a summary turn for the evicted turns
    roles = [m["role"] for m in pruned]
    assert "system" in roles  # Summary turn was injected
    assert any("[前序对话要点摘要" in m["content"] for m in pruned if m["role"] == "system")
    # Latest assistant turn is preserved
    assert any("98% 准确率" in m["content"] for m in pruned if m["role"] == "assistant")


def test_context_manager_format_qa_prompt():
    mgr = ContextManager()
    prompt = mgr.format_qa_prompt(
        question="该论文的测试集准确率是多少？",
        sources_text="Source 1: 准确率达到 92.4%。",
        history=[
            {"role": "user", "content": "这是关于哪个领域的论文？"},
            {"role": "assistant", "content": "这是关于生物信息学蛋白质折叠的论文。"},
        ],
    )

    assert "## 对话历史上下文" in prompt
    assert "生物信息学蛋白质折叠" in prompt
    assert "## 已检索文献来源与证据" in prompt
    assert "## 当前问题" in prompt


class ScriptedQAContextLLM:
    last_receipt = {
        "raw_response": "{}",
        "usage": {"prompt_tokens": 12, "completion_tokens": 6},
        "latency_ms": 3,
    }

    def generate_structured(self, *, stage, prompt, response_model, max_tokens=None):
        assert stage == "literature_qa"
        assert "dialogue_history" in prompt or "对话历史" in prompt or "question" in prompt
        return LiteratureQAAnswer(
            answer="基于对话历史与证据，该方法在测试集上达到了领先性能。",
            source_ids=[],
            evidence_snippets=[],
            uncertainty="",
        )


def test_literature_qa_service_accepts_history(db_session_factory, golden_case):
    from radar.config import Settings

    settings = Settings(
        _env_file=None,
        llm_provider="deepseek",
        llm_api_key="sk-test-key-000000000000",
        llm_model="deepseek-v4-pro",
        llm_base_url="https://api.deepseek.com",
    )

    service = LiteratureQAService(
        db_session_factory,
        llm_client=ScriptedQAContextLLM(),
        settings=settings,
    )

    history = [
        {"role": "user", "content": "前一个问题：论文提出了什么模型？"},
        {"role": "assistant", "content": "前一个回答：提出了 GraphNet 模型。"},
    ]

    result = service.answer(
        golden_case,
        "在什么数据集上进行了评估？",
        top_k=3,
        history=history,
    )

    assert result["answer"].startswith("基于对话历史与证据")
    assert result["sources"]
