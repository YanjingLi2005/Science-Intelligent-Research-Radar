# Research Radar 全链路构架

## 产品定位

面向正在写作中的科研人员的 AI Agent。不是发表后检测工具，不是全自动生成器。

**核心原则**：AI 做分析建议，人做决策。每条 Claim、每个 Impact、每个修改必须人工确认。修改不自动写回原稿。

---

## 全局概念：谁是谁

| 概念 | 🔵 我的 | 🔴 外部发现 | 说明 |
|------|---------|------------|------|
| **ManuscriptVersion** | 🔵 我 | | 我上传的论文，我能更新版本 |
| **Claim / ClaimRevision** | 🔵 我 | | 从**我的论文** LLM 提取的主张，我能确认/编辑/拒绝 |
| **evidence_own** | 🔵 我 | | 从**我的论文** exact match 的引文段落 |
| **Source + SourceSnapshot** | | 🔴 外部 | arXiv/OpenAlex 搜到的论文全文，不能编辑 |
| **evidence_new** | | 🔴 外部 | 从**外部论文** exact match 的引文段落 |
| **ImpactCandidate** | | | 🔵 我的 Claim ← 比较 → 🔴 外部 Source 的**关系** |
| **ActionItem** | 🔵 我 | | 基于 Impact 生成的**我要做的事** |
| **PatchProposal** | 🔵 我 | | 针对**我的论文**的修改建议 |

---

## 全链路 Workflow

```
  🔵 我的论文 ──────────────────────────────────────── 🔴 外部发现
```

---

### Phase 0: 项目搭建

| 步骤 | 归属 | 说明 |
|------|------|------|
| 创建项目 | 🔵 我 | ResearchCase |
| 上传论文 PDF/LaTeX | **🔵 我的论文** | 解析为 ManuscriptVersion |
| 配置 LLM API | 🔵 我 | Settings 页或 .env |

---

### Phase 1: Claim 提取（G0 人工确认）

| 步骤 | 归属 | 说明 |
|------|------|------|
| LLM 从**我的论文**提取候选 Claim | **🔵 我的论文** → 🔵 Claim | 最多 12 条 |
| 每条 Claim 有: statement, source_quote(我的论文原文), 6-field contract, falsifiable_condition | 🔵 我的 | |
| 原子化分解: 复杂 Claim → 多个可独立验证的子 Claim | 🔵 我的 | |
| 语义验证: statement 是否忠实反映 source_quote | 🔵 我的 | 防止 LLM 夸大 |
| **G0: 人工确认** [确认]/[编辑]/[拒绝] 每条 Claim | 🔵 人操作 | 未确认的 Claim 不参与扫描 |

---

### Phase 2: 文稿理解

| 步骤 | 归属 | 说明 |
|------|------|------|
| LLM 通读**我的论文**全文 | **🔵 我的论文** | 产出结构化 profile |
| 提取: 研究问题、核心论点、方法、数据集、评估协议 | 🔵 我的 | |
| 生成 watch_topics 用于后续搜索 | 🔵 我的 | 告诉搜索引擎搜什么 |

---

### Phase 3: 文献扫描（搜外部论文）

| 步骤 | 归属 | 说明 |
|------|------|------|
| HyDE 假想摘要 + arXiv 关键词查询 | 🔵 我生成的查询词 | 去搜 🔴 外部 |
| arXiv 搜索 | | **🔴 外部** 最新论文 |
| OpenAlex 搜索 | | **🔴 外部** 2.7 亿篇论文 |
| PubMed 搜索 | | **🔴 外部** 生物医学文献（NCBI E-utilities） |
| 引用图谱: 提取**我的论文**参考文献 → 找哪些**外部论文**引用了它们 | 🔵 我的参考文献 → | 🔴 外部引用者 |
| Unpaywall: 获取**外部论文** OA 全文 | | 🔴 外部 |
| Crossref: 检查**外部论文**是否撤稿 | | 🔴 外部 |
| DOI 去重合并 | | 🔴 外部 |

**这个 Phase 全部在搜 🔴 外部论文。**

---

### Phase 4: 混合检索排序

| 步骤 | 归属 | 说明 |
|------|------|------|
| FTS5 关键词预筛 | | 🔴 外部论文池 |
| BM25 统计排序 | | 🔴 外部 |
| ChromaDB 向量语义检索（可选） | | 🔴 外部 |
| LLM Reranker 重排序 | | 🔴 外部 |
| 排序后选出 Top K 篇**外部论文** | | 🔴 外部 |

---

### Phase 5: 影响评估（Claim × 外部论文）

| 步骤 | 归属 | 说明 |
|------|------|------|
| 对每个 (🔵 我的 Claim, 🔴 外部论文) 对: | | |
| 提取**外部论文**的 6 字段合约 | | 🔴 外部 → IncomingResult |
| 程序化条件比较: 🔵 我的 contract vs 🔴 外部 contract | 🔵 vs 🔴 | 6 字段 + 别名表 + 语义 + 数值 |
| LLM 影响评估 | | |
| stance: 外部论文是 supports / challenges 我的 Claim | | 🔴 → 🔵 的关系 |
| evidence_own: 从**我的论文** exact match | **🔵 我的引文** | |
| evidence_new: 从**外部论文** exact match | **🔴 外部证据** | |
| 信任验证: 两条证据都在各自原文中精确存在 | | |
| 竞品检测: 外部论文作者是否在我监控名单中 | | 🔴 外部 |
| 诚信检查: 外部论文是否撤稿 | | 🔴 外部 |

---

### Phase 6: 行动生成（G1 人工确认）

| 步骤 | 归属 | 说明 |
|------|------|------|
| 每个 Impact → ActionItem | 🔵 我要做的事 | |
| 7 种 Action: writing / experiment / data / cite / competitor_response / revalidation / team_decision | 🔵 我的行动 | |
| 每条 Action 带 suggestedSkill | 🔵 我的 | 对应 CCF-A 技能 |
| **G1: 人工确认** Impact [采用]/[不采用] | 🔵 人操作 | 采用后才生成 Action |

---

### Phase 7: AI 技能执行

| 技能 | 触发 | 输入 | 输出 |
|------|------|------|------|
| **Writer** | writing, cite | **🔵 我的论文** + 🔴 impacts | before/after diff |
| **Reviewer** | team_decision | **🔵 我的论文摘要** + 🔴 impacts | 5 维评分 + 风险表 |
| **Auditor** | revalidation | **🔵 我的论文 + Claims** + 🔴 impacts | claim-support 表 + 数字差异 |
| **Experiment** | experiment, data | **🔵 我的 Claims** + 🔴 impacts | 实验协议 + baseline |
| **Rebuttal** | competitor_response | **🔵 我的论文** + 🔴 impacts | 回应文案 + revision |
| **ReferenceAuditor** | audit_references / check_references | **🔵 我的论文** | DOI 合理性、重复、格式、自引、错误引用审计 |

**所有技能输入都包含 🔵 我的 和 🔴 外部 两部分。输出都是针对 🔵 我的。**

---

### Phase 8: 修改审批（G2 人工确认）

| 步骤 | 归属 | 说明 |
|------|------|------|
| Writer 产出的 diff 展示: before_text = **🔵 我的论文原文**, after_text = **🔵 修改建议** | 🔵 我的 | |
| 人工逐条审批: [批准] / [拒绝] | 🔵 人操作 | |
| [应用所有已批准] → PatchService → 7 项验证 | 🔵 我的 | |
| **G2: 最终确认** → 导出到文稿 | 🔵 人操作 | 不自动写回 |

---

### Phase 9: 持续监控

| 步骤 | 归属 | 说明 |
|------|------|------|
| 每周自动/手动触发扫描 | → 搜 | 🔴 外部新论文 |
| 竞品监控: WatchEntity 别名匹配 | | 🔴 外部作者/团队 |
| 诚信告警: 外部论文撤稿/更正 | | 🔴 外部 |
| Claim Health 账本: 追踪**我的**每个 Claim | 🔵 我的 | 的证据状态 |

---

## 数据流总览

```
🔵 ManuscriptVersion           🔴 arXiv / OpenAlex / 引用图
        │                              │
        ▼                              ▼
🔵 Claim (我的主张)              🔴 Source + SourceSnapshot (外部论文)
        │                              │
        │     ┌────────────────────────┘
        │     │
        ▼     ▼
    🔵 vs 🔴 6-field 条件比较
        │
        ▼
    ImpactCandidate (我的 C1 ← 被挑战 → 外部论文 X)
        │
        ▼ G1 人工确认
    🔵 ActionItem (我要做的事: 改写作/补实验/重新验证...)
        │
        ▼ suggestedSkill 路由
    ┌───┼───┬───────┬──────────┐
    │   │   │       │          │
  Writer│Auditor Experiment Rebuttal
    │   │   │       │          │
    ▼   ▼   ▼       ▼          ▼
  针对 🔵 我的论文的修改/评审/审计/实验计划/rebuttal
    │
    ▼ 人工逐条审批
  before/after diff → [批准]/[拒绝]
    │
    ▼ G2 最终确认
  导出到 🔵 我的文稿
```

---

## 三个 Gate 总结

| Gate | 操作 | 对象 | 谁能通过 |
|------|------|------|---------|
| **G0** | 确认 Claim | 🔵 我的 | 通过后才参与扫描 |
| **G1** | 确认 Impact | 🔵 vs 🔴 关系 | 通过后才生成 Action |
| **G2** | 审批 Patch | 🔵 我的修改 | 通过后才导出到文稿 |
