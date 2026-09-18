# Tool-R1: 工具调用强化学习提升代码生成智能体的推理能力

## Abstract

We introduce Tool-R1, a reinforcement learning method that trains code-generation agents to invoke external tools (executors, linters, and retrievers) during reasoning. Tool-R1 improves pass@1 accuracy on SWE-bench Verified from 38.2% to 51.4% while reducing average wall-clock time by 18%. Unlike prior work that treats tool use as a separate fine-tuning stage, Tool-R1 integrates tool invocation into the policy gradient objective, allowing the model to learn when and how to use tools as part of its reasoning trajectory.

## 1. Introduction

Large language models (LLMs) trained on code show strong generation ability but frequently produce code that fails to execute. Prior work addresses this with post-hoc repair loops or separate tool-use fine-tuning. However, these approaches either require human supervision at inference time or decouple tool use from the main policy objective.

We propose Tool-R1, which augments the rollout process with tool calls and optimizes the policy with a tool-aware reward. The key insight is that tool invocations provide dense intermediate feedback that improves credit assignment during reinforcement learning.

## 2. Method

Tool-R1 samples a reasoning trajectory with interleaved tool calls. Each tool call returns an observation that is appended to the context. The policy is trained with a tool-aware advantage estimator that rewards correct final answers and penalizes unnecessary tool calls (cost regularization).

We use a GRPO-style update with a reference policy for clipping. The tool set includes a Python executor, a shell linter, and a repository retriever.

## 3. Experiments

We evaluate Tool-R1 on SWE-bench Verified and three coding agent benchmarks. With the same base model (Qwen2.5-Coder-32B), Tool-R1 reaches 51.4% pass@1 on SWE-bench Verified, compared to 38.2% for the base model with best-of-4 sampling and 44.1% for a supervised tool-use baseline. Tool-R1 also reduces average wall-clock time by 18% because the model learns to skip unnecessary retrieval.

Ablations show that the cost regularizer contributes 3.1 points and that tool-aware advantage estimation contributes 4.6 points.

## 4. Related Work

Code agents with tool use have been explored in ReAct-style prompting and in AgentCoder-style multi-agent frameworks. Tool-R1 differs by learning tool policy end-to-end via RL rather than prompting or imitation.

## 5. Conclusion

We show that integrating tool calls into the RL objective improves both accuracy and efficiency for code generation agents. Future work includes multi-turn tool use and hardware-aware tool scheduling.
