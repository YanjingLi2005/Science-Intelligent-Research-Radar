// ---------- Research Radar demo data ----------

export type Verdict = 'challenge' | 'support' | 'boundary' | 'prior' | 'none';
export type ClaimStatus = 'valid' | 'supported' | 'disputed' | 'revalidate' | 'history';
export type MatrixStatus = 'same' | 'partial' | 'diff';

export interface MatrixRow {
  field: string;
  ours: string;
  theirs: string;
  status: MatrixStatus;
}

export interface Paper {
  id: string;
  title: string;
  authors: string[];
  sourceKind?: string;
  sourceIdentifier?: string;
  sourceUrl?: string;
  doi?: string;
  venue?: string;
  publicationType?: string;
  arxivId: string;
  arxivPrimaryCategory?: string;
  fieldsOfStudy?: string[];
  ccfRank?: string;
  date: string;
  verdict: Verdict;
  urgency: 'urgent' | 'review' | 'info';
  claimIds: string[];
  quote: string;
  quoteLoc: string;
  yourQuote: string;
  yourLoc: string;
  matrix: MatrixRow[];
  why: string;
  suggestion: string;
  uncertainty: string;
  reviewState?: 'candidate' | 'edited' | 'confirmed' | 'dismissed';
  // Enhanced peer review fields
  impactType?: string;
  confidenceScore?: number;
  oneSentenceVerdict?: string;
  lethalReviewerQuestion?: string;
  keyDifference?: Record<string, string>;
  defenseStrategy?: Record<string, unknown>;
  benchmarkComparison?: BenchmarkItem[];
  strategicFlags?: string[];
  empiricalDominance?: boolean;
  authorProfile?: AuthorProfile;
  influenceMetrics?: InfluenceMetrics;
  labTimeline?: LabTimelineItem[];
}

export interface AuthorProfile {
  first_author?: string;
  corresponding_author?: string;
  first_author_h_index?: number;
  corresponding_author_h_index?: number;
  top_h_index?: number;
  affiliations?: string[];
  primary_institution?: string;
  is_top_tier?: boolean;
}

export interface InfluenceMetrics {
  citation_count?: number;
  citation_velocity?: number;
  social_heat?: number;
  is_top_tier_lab?: boolean;
  top_institution?: string;
  top_h_index?: number;
}

export interface LabTimelineItem {
  title: string;
  year: number;
  venue?: string;
  citations?: number;
  url?: string;
  badge?: string;
}

export interface BenchmarkItem {
  dataset: string;
  metric: string;
  our_model: string;
  our_value: number;
  competitor_model: string;
  competitor_value: number;
  delta: number;
  unit?: string;
  higher_is_better?: boolean;
  warning?: boolean;
  warning_level?: 'critical' | 'moderate' | 'advantage' | 'neutral';
  empirical_dominance?: boolean;
  notes?: string;
}

export interface RetrievalReceipt {
  id: string;
  sourceKind: string;
  endpoint: string;
  query: string;
  requestedCount: number;
  retrievedCount: number;
  pages: number;
  responseHash: string;
  status: string;
  warnings: string[];
  fetchedAt: string;
}

export interface CitationHealth {
  manuscriptVersionId: string;
  checkedAt: string;
  referenceEntries: number;
  identifiers: string[];
  identifierCount: number;
  resolvedCount: number;
  metadataCoverage: number;
  citationKeys: number;
  bibliographyKeys: number;
  unresolvedKeys: string[];
  unusedKeys: string[];
  duplicateIdentifiers: string[];
  integrity: Record<string, number>;
  issues: { kind: string; severity: string; identifier: string; message: string }[];
}

export interface Claim {
  id: string;
  text: string;
  status: ClaimStatus;
  confirmed: 'yes' | 'pending' | 'history';
  radarWatch: boolean;
  quote: string;
  loc: string;
  contract: { task: string; dataset: string; split: string; metric: string; baseline: string; scope: string };
  falsifiable: string;
  evidence: { kind: 'support' | 'challenge' | 'completeness'; paperId: string; note: string }[];
}

export interface ActionItem {
  id: string;
  kind: 'experiment' | 'data' | 'writing' | 'competitive' | 'revalidate';
  priority: 'P0' | 'P1' | 'P2';
  title: string;
  due: string;
  claimId: string;
  sourcePaperId: string;
  reason: string;
  checklist: string[];
  status?: 'proposed' | 'open' | 'in_progress' | 'done' | 'dismissed';
  suggestedSkill?: string;
}

export interface VersionRec {
  id?: string;
  v: string;
  date: string;
  file: string;
  claims: number;
  note: string;
}

export interface AuditRec {
  stage: string;
  provider: string;
  model: string;
  duration: string;
  cost: string;
  result: 'pass' | 'warn' | 'fail';
}

export interface Project {
  id: string;
  name: string;
  short: string;
  question: string;
  version: string;
  file: string;
  claimsConfirmed: number;
  claimsTotal: number;
  lastScan: string;
  urgent: number;
  topics: string[];
  claims: Claim[];
  papers: Paper[];
  actions: ActionItem[];
  versions: VersionRec[];
  audit: AuditRec[];
  competitors: { id: string; team: string; aliases: string[] }[];
  profile: {
    question: string;
    thesis: string;
    contributions: string[];
    findings: string[];
    limits: string[];
  };
  rewrite: {
    patchId?: string;
    claimId: string;
    before: string;
    after: string;
    loc: string;
    checks: { label: string; ok: boolean }[];
    patchType?: string;
    targetLocation?: string;
    originalSentence?: string;
    patchedSentence?: string;
  };
  fidelity?: import('../api').FidelityStats | null;
  retrievalReceipts?: RetrievalReceipt[];
  citationHealth?: CitationHealth | null;
}

export function sourceLabel(paper: Pick<Paper, 'sourceKind' | 'sourceIdentifier' | 'arxivId'>): string {
  const kind = paper.sourceKind || (paper.arxivId ? 'arxiv' : 'unknown');
  const labels: Record<string, string> = {
    arxiv: 'arXiv',
    openalex: 'OpenAlex',
    semantic_scholar: 'Semantic Scholar',
    pubmed: 'PubMed',
    pmc: 'PMC',
  };
  const identifier = paper.sourceIdentifier || paper.arxivId;
  return identifier ? `${labels[kind] || kind} · ${identifier}` : labels[kind] || '外部来源';
}

// =================================================================
// Project A — RARE
// =================================================================

const rarePapers: Paper[] = [
  {
    id: 'p1',
    title: 'RobustRAG: Defending LLM Agents Against Retrieval Corruption via Dual-Stage Verification',
    authors: ['Y. Xiang', 'M. Chen', 'D. Wagner', 'et al.'],
    arxivId: '2602.08814',
    date: '2026-02-11',
    verdict: 'challenge',
    urgency: 'urgent',
    claimIds: ['C1'],
    quote:
      'Under adaptive query perturbation, retrieval-aware reranking pipelines degrade by 8.7–11.2 points on NQ-Open, exceeding the 5-point robustness envelope claimed by prior retrieval-aware evaluators.',
    quoteLoc: '§4.2 ¶3 · Table 3',
    yourQuote:
      'Across all six perturbation families, RARE\u2019s accuracy drop remains bounded within 5 points relative to the clean-query setting (Table 2).',
    yourLoc: 'main.tex · L214–218',
    matrix: [
      { field: '任务', ours: '开放域 QA 鲁棒性', theirs: '开放域 QA + Agent 工具调用', status: 'partial' },
      { field: '数据集', ours: 'NQ-Open / TQA / HotpotQA', theirs: 'NQ-Open / FreshQA', status: 'partial' },
      { field: '扰动方式', ours: '6 类静态扰动', theirs: '自适应对抗扰动（迭代生成）', status: 'diff' },
      { field: '指标', ours: 'EM / Accuracy drop', theirs: 'Accuracy drop / ASR', status: 'same' },
      { field: '对比基线', ours: 'RAG-Fusion, Self-RAG', theirs: 'RARE, Self-RAG', status: 'same' },
    ],
    why: '该论文用自适应对抗扰动替代静态扰动集，直接击穿 C1 的 5% 下降包络；NQ-Open 上的 8.7–11.2 点下降与你的 Table 2 结论正面冲突。',
    suggestion: '在相同自适应扰动协议下复现 RARE，更新 C1 的适用范围或在文稿中限定「静态扰动族」。',
    uncertainty: '其扰动预算（迭代轮次）未公开，可复现性待确认；建议先按 §A.3 的默认配置近似。',
    authorProfile: {
      first_author: 'Y. Xiang',
      corresponding_author: 'D. Wagner',
      first_author_h_index: 52,
      corresponding_author_h_index: 71,
      top_h_index: 71,
      affiliations: ['Stanford University', 'Google DeepMind'],
      primary_institution: 'Stanford University',
      is_top_tier: true,
    },
    influenceMetrics: {
      citation_count: 312,
      citation_velocity: 24.6,
      social_heat: 92.3,
      is_top_tier_lab: true,
      top_institution: 'Stanford University',
      top_h_index: 71,
    },
    labTimeline: [
      { title: 'RobustRAG: Defending LLM Agents Against Retrieval Corruption via Dual-Stage Verification', year: 2026, venue: 'Preprint / arXiv', citations: 312, url: 'https://arxiv.org/abs/2602.08814', badge: 'Current Trigger' },
      { title: 'Foundation Architecture & Scaling Study (Y. Xiang et al.)', year: 2025, venue: 'NeurIPS / ICML', citations: 916, url: 'https://arxiv.org', badge: 'Milestone Work' },
      { title: 'Pre-training Optimization & Benchmark Analysis in Stanford University', year: 2024, venue: 'ICLR / ACL', citations: 1742, url: 'https://arxiv.org', badge: 'Early Exploration' },
    ],
  },
  {
    id: 'p2',
    title: 'On the Fragility of Retrieval-Aware Reranking under Typographic Perturbations',
    authors: ['S. Okafor', 'L. Marchetti'],
    arxivId: '2601.19207',
    date: '2026-01-28',
    verdict: 'challenge',
    urgency: 'review',
    claimIds: ['C2'],
    quote:
      'Contrary to recent claims, retrieval-aware rerankers lose most of their advantage over dense-fusion baselines once queries contain even 2% character-level noise (nDCG@10 gap: +3.4 → +0.6).',
    quoteLoc: '§3.1 ¶2 · Fig. 2',
    yourQuote:
      'RARE outperforms RAG-Fusion by at least 3.1 nDCG@10 on five BEIR subsets under perturbed queries.',
    yourLoc: 'main.tex · L236–239',
    matrix: [
      { field: '任务', ours: '检索重排序', theirs: '检索重排序', status: 'same' },
      { field: '数据集', ours: 'BEIR 5 子集', theirs: 'BEIR 8 子集', status: 'partial' },
      { field: '噪声类型', ours: '词级扰动为主', theirs: '字符级排版噪声', status: 'diff' },
      { field: '指标', ours: 'nDCG@10', theirs: 'nDCG@10', status: 'same' },
      { field: '对比基线', ours: 'RAG-Fusion', theirs: 'ColBERTv2, RAG-Fusion', status: 'partial' },
    ],
    why: 'C2 的优势区间在字符级噪声下被压缩到 +0.6，说明 C2 的成立条件比文稿表述更窄。',
    suggestion: '补充字符级噪声实验，或将 C2 改写为「词级扰动下 ≥3.1 nDCG@10」。',
    uncertainty: '对方仅在 8B 以下模型验证；你的 13B 设置是否同样脆弱未知。',
  },
  {
    id: 'p3',
    title: 'Budget-Aware Agentic Retrieval: Adaptive Scheduling for Latency-Constrained RAG',
    authors: ['H. Tanaka', 'R. Iyer', 'P. Novak', 'et al.'],
    arxivId: '2512.30419',
    date: '2025-12-30',
    verdict: 'boundary',
    urgency: 'info',
    claimIds: ['C3'],
    quote:
      'With budget-aware scheduling, the overhead of robustness-oriented reranking drops from 11.8% to 4.2% at 8B scale, suggesting prior overhead estimates assumed a static retrieval budget.',
    quoteLoc: '§5 ¶1 · Table 5',
    yourQuote: 'At 8B scale, RARE introduces less than 12% additional inference latency over the base RAG pipeline.',
    yourLoc: 'main.tex · L260',
    matrix: [
      { field: '任务', ours: '鲁棒重排序', theirs: '延迟受限 RAG 调度', status: 'partial' },
      { field: '模型规模', ours: '8B', theirs: '7B–8B', status: 'same' },
      { field: '开销口径', ours: '静态预算', theirs: '动态预算调度', status: 'diff' },
      { field: '指标', ours: '相对延迟增幅', theirs: '相对延迟增幅', status: 'same' },
    ],
    why: '不冲突但改变语境：C3 的 <12% 开销在动态预算下可以被压到 4.2%，你的开销声明仍是上界，但不再是紧界。',
    suggestion: '在 Limitations 中承认静态预算假设，引用该文作为开销可进一步压缩的方向。',
    uncertainty: '其调度器引入额外的规划 LLM 调用，端到端成本口径可能不同。',
  },
  {
    id: 'p4',
    title: 'REAR: Retrieval-Enhanced Adversarial Robustness for Open-Domain Question Answering',
    authors: ['J. Alvarez', 'M. Fontaine', 'K. Sato'],
    arxivId: '2406.11733',
    date: '2024-06-17',
    verdict: 'prior',
    urgency: 'review',
    claimIds: ['C2', 'C4'],
    quote:
      'We propose retrieval-enhanced adversarial training that couples query perturbation with retriever-in-the-loop fine-tuning, improving robustness across both sparse (BM25) and dense retrievers.',
    quoteLoc: '§1 ¶2 · §2',
    yourQuote:
      'To our knowledge, RARE is the first framework to evaluate retrieval-aware robustness jointly across sparse and dense retrievers.',
    yourLoc: 'main.tex · L58–60',
    matrix: [
      { field: '核心思想', ours: '鲁棒性评估协议 + 重排序', theirs: '对抗训练 + 检索在环微调', status: 'diff' },
      { field: '检索器覆盖', ours: 'BM25 + 稠密', theirs: 'BM25 + 稠密', status: 'same' },
      { field: '目标', ours: '评估与诊断', theirs: '训练期防御', status: 'diff' },
    ],
    why: '「first to … jointly across sparse and dense retrievers」这一新颖性表述与 REAR 的 §2 重叠，存在被审稿人指为遗漏在先工作的风险。',
    suggestion: '改写新颖性表述为「首个面向评估协议（而非训练防御）的检索在环鲁棒性框架」，并在 Related Work 引用 REAR。',
    uncertainty: 'REAR 未做系统性扰动族基准，差异化定位成立，但措辞必须收窄。',
  },
  {
    id: 'p5',
    title: 'Are Query Perturbation Benchmarks Saturating? A Meta-Analysis of 40 Robustness Studies',
    authors: ['T. Bergström', 'A. El-Amin', 'et al.'],
    arxivId: '2603.04471',
    date: '2026-03-08',
    verdict: 'support',
    urgency: 'info',
    claimIds: ['C5'],
    quote:
      'Six perturbation families—typographic, paraphrastic, adversarial, dialectal, temporal, and compositional—account for 91% of the failure modes observed in real-world query logs (n = 2.1M).',
    quoteLoc: '§6.1 ¶1 · Table 7',
    yourQuote:
      'The six perturbation families in RARE are chosen to cover the dominant failure modes of real-world query distribution shift.',
    yourLoc: 'main.tex · L112–114',
    matrix: [
      { field: '研究对象', ours: '6 类扰动族', theirs: '40 项研究的元分析', status: 'partial' },
      { field: '覆盖率证据', ours: '定性论证', theirs: '2.1M 真实查询日志', status: 'partial' },
      { field: '结论方向', ours: '覆盖充分', theirs: '6 类覆盖 91% 失败模式', status: 'same' },
    ],
    why: '为 C5 提供大规模实证背书：你的六类扰动族选择与 2.1M 真实查询日志的失败模式分布一致。',
    suggestion: '采纳为支持证据，在 C5 论证处引用该文（覆盖率 91%）。',
    uncertainty: '其日志来源为英文搜索场景，多语言覆盖未验证。',
  },
  {
    id: 'p6',
    title: 'SAGE: Self-Attentive Guardrails for Agentic Retrieval Pipelines',
    authors: ['Z. Wei', 'C. Duarte', 'N. Kowalski', 'et al.'],
    arxivId: '2603.11098',
    date: '2026-03-16',
    verdict: 'none',
    urgency: 'urgent',
    claimIds: ['C1', 'C4'],
    quote:
      'SAGE achieves state-of-the-art robustness on five retrieval-agent benchmarks, and we release a leaderboard tracking adversarial query robustness across 14 systems, updated weekly.',
    quoteLoc: '§1 ¶3 · §7',
    yourQuote: '—',
    yourLoc: '—',
    matrix: [
      { field: '定位', ours: '评估协议', theirs: '防御机制 + 周更榜单', status: 'diff' },
      { field: '检索器', ours: 'BM25 + 稠密', theirs: '稠密为主', status: 'partial' },
      { field: '公开 artifact', ours: '代码 + 协议', theirs: '代码 + 榜单', status: 'partial' },
    ],
    why: '未直接挑战任何 Claim，但其周更榜单将成为审稿人核对「SOTA 鲁棒性」的新参照系；你监控名单中的 Kowalski 组参与其中。',
    suggestion: '将 RARE 提交至其榜单以锁定相对位置；在 Related Work 预留引用位。',
    uncertainty: '榜单评测协议与 RARE 协议的可比性尚未核实。',
  },
];

const rareClaims: Claim[] = [
  {
    id: 'C1',
    text: '我们的方法在扰动查询下准确率下降不超过 5%',
    status: 'disputed',
    confirmed: 'yes',
    radarWatch: true,
    quote: 'Across all six perturbation families, RARE’s accuracy drop remains bounded within 5 points.',
    loc: 'main.tex · L214',
    contract: {
      task: '开放域问答',
      dataset: 'NQ-Open / TQA / HotpotQA',
      split: 'dev（官方划分）',
      metric: 'EM，相对干净查询的绝对下降点数',
      baseline: 'Vanilla RAG, Self-RAG',
      scope: '6 类静态扰动族；8B–13B 模型',
    },
    falsifiable: '任一静态扰动族下 EM 下降 > 5 点即证伪',
    evidence: [
      { kind: 'challenge', paperId: 'p1', note: '自适应扰动下下降 8.7–11.2 点，超出包络' },
      { kind: 'support', paperId: 'p5', note: '6 类扰动族覆盖 91% 真实失败模式' },
      { kind: 'completeness', paperId: 'p6', note: 'SAGE 周更榜单成为新的外部参照系' },
    ],
  },
  {
    id: 'C2',
    text: '检索感知重排序在 BEIR 子集上优于 RAG-Fusion ≥ 3.1 nDCG@10',
    status: 'disputed',
    confirmed: 'yes',
    radarWatch: true,
    quote: 'RARE outperforms RAG-Fusion by at least 3.1 nDCG@10 on five BEIR subsets.',
    loc: 'main.tex · L236',
    contract: {
      task: '检索重排序',
      dataset: 'BEIR 5 子集（SciFact, FiQA, NFCorpus, TREC-COVID, ArguAna）',
      split: 'test',
      metric: 'nDCG@10',
      baseline: 'RAG-Fusion, monoT5',
      scope: '词级扰动；查询长度 ≤ 32 tokens',
    },
    falsifiable: '任一 BEIR 子集上差距 < 3.1 nDCG@10 即证伪',
    evidence: [
      { kind: 'challenge', paperId: 'p2', note: '字符级噪声下优势缩至 +0.6' },
      { kind: 'completeness', paperId: 'p4', note: 'REAR 在先覆盖稀疏+稠密检索场景' },
    ],
  },
  {
    id: 'C3',
    text: '方法在 8B 参数规模下推理开销增加 < 12%',
    status: 'valid',
    confirmed: 'yes',
    radarWatch: false,
    quote: 'At 8B scale, RARE introduces less than 12% additional inference latency.',
    loc: 'main.tex · L260',
    contract: {
      task: '端到端 RAG 推理',
      dataset: 'NQ-Open dev',
      split: 'dev',
      metric: '相对延迟增幅（A100-80G，bs=1）',
      baseline: 'Vanilla RAG',
      scope: '静态检索预算（top-20 → top-5）',
    },
    falsifiable: '相同硬件口径下开销 ≥ 12% 即证伪',
    evidence: [{ kind: 'completeness', paperId: 'p3', note: '动态预算下开销可压至 4.2%，需收窄表述' }],
  },
  {
    id: 'C4',
    text: '鲁棒性收益不依赖特定检索器（BM25 与稠密检索均成立）',
    status: 'revalidate',
    confirmed: 'yes',
    radarWatch: true,
    quote: 'The robustness gains of RARE are retriever-agnostic, holding for both BM25 and dense retrieval.',
    loc: 'main.tex · L248',
    contract: {
      task: '开放域问答',
      dataset: 'NQ-Open',
      split: 'dev',
      metric: 'EM 下降点数（两种检索器分别报告）',
      baseline: 'BM25 + DPR',
      scope: '检索器冻结，不重训',
    },
    falsifiable: '任一检索器下收益消失（< 1 点）即证伪',
    evidence: [
      { kind: 'challenge', paperId: 'p4', note: 'REAR 在先工作覆盖相同双检索器场景' },
      { kind: 'completeness', paperId: 'p6', note: 'SAGE 榜单以稠密检索为主，需补充对比' },
    ],
  },
  {
    id: 'C5',
    text: '6 类扰动族足以代表真实分布偏移',
    status: 'supported',
    confirmed: 'pending',
    radarWatch: false,
    quote: 'The six perturbation families cover the dominant failure modes of real-world query distribution shift.',
    loc: 'main.tex · L112',
    contract: {
      task: '扰动族覆盖性论证',
      dataset: '—（论证性 Claim）',
      split: '—',
      metric: '失败模式覆盖率',
      baseline: '—',
      scope: '英文开放域查询',
    },
    falsifiable: '存在覆盖率显著缺失的第 7 类失败模式即证伪',
    evidence: [{ kind: 'support', paperId: 'p5', note: '2.1M 查询日志：6 类覆盖 91% 失败模式' }],
  },
];

const rareActions: ActionItem[] = [
  {
    id: 'a1',
    kind: 'experiment',
    priority: 'P0',
    title: '用自适应对抗扰动协议复现 RARE，验证 C1 包络',
    due: '3 天内',
    claimId: 'C1',
    sourcePaperId: 'p1',
    reason: '该论文用自适应对抗扰动替代静态扰动集，直接击穿 C1 的 5% 下降包络。',
    checklist: ['按 p1 §A.3 默认预算实现扰动器', '跑 NQ-Open dev 全量', '对比 Table 2 的 5 点包络', '记录偏差并回填 Claim 账本'],
  },
  {
    id: 'a2',
    kind: 'writing',
    priority: 'P0',
    title: '收窄新颖性表述，补引 REAR 在先工作',
    due: '投稿前',
    claimId: 'C2',
    sourcePaperId: 'p4',
    reason: 'REAR 在先工作覆盖了与 C2 相同的双检索器场景，需要收窄新颖性表述。',
    checklist: ['改写 L58–60「first to …」句式', 'Related Work 新增 REAR 段落', '强调「评估协议 vs 训练防御」差异'],
  },
  {
    id: 'a3',
    kind: 'experiment',
    priority: 'P1',
    title: '补字符级排版噪声实验（2% 噪声率）',
    due: '本周内',
    claimId: 'C2',
    sourcePaperId: 'p2',
    reason: '字符级噪声下 C2 的 nDCG 优势缩至 +0.6，需要补做实验。',
    checklist: ['实现字符级噪声生成器', 'BEIR 5 子集回归', 'nDCG@10 差距是否仍 ≥ 3.1'],
  },
  {
    id: 'a4',
    kind: 'data',
    priority: 'P1',
    title: '登记 p5 元分析数据集为支持证据',
    due: '本周内',
    claimId: 'C5',
    sourcePaperId: 'p5',
    reason: '2.1M 查询日志显示 6 类扰动族覆盖 91% 失败模式，登记为支持证据。',
    checklist: ['下载其 40 研究元分析表', '核对 91% 覆盖率口径', '写入 Discussion Evidence Brief'],
  },
  {
    id: 'a5',
    kind: 'competitive',
    priority: 'P1',
    title: '将 RARE 提交至 SAGE 周更鲁棒性榜单',
    due: '两周内',
    claimId: 'C4',
    sourcePaperId: 'p6',
    reason: 'SAGE 周更榜单将成为审稿人核对鲁棒性的新参照系。',
    checklist: ['核对其评测协议可比性', '准备 submission 脚本', '记录排名截图存档'],
  },
  {
    id: 'a6',
    kind: 'revalidate',
    priority: 'P2',
    title: '在 Limitations 补充静态预算假设（引用 p3）',
    due: '下轮修改',
    claimId: 'C3',
    sourcePaperId: 'p3',
    reason: '动态预算下推理开销可压至 4.2%，需要在 Limitation 中补充说明。',
    checklist: ['引用 Budget-Aware Scheduling', '说明 12% 为上界非紧界'],
  },
];

// =================================================================
// Project B — E-Agent
// =================================================================

const eagentPapers: Paper[] = [
  {
    id: 'q1',
    title: 'MM-Navigator: Multimodal Retrieval Planning with Tool-Use Verification for GUI Agents',
    authors: ['L. Zhao', 'F. Adeyemi', 'et al.'],
    arxivId: '2602.14772',
    date: '2026-02-19',
    verdict: 'challenge',
    urgency: 'urgent',
    claimIds: ['C1'],
    quote:
      'With tool-use verification loops, planning accuracy on WebArena-VM reaches 61.4%, surpassing retrieval-planned baselines by 6.8 points without any multimodal index.',
    quoteLoc: '§4 ¶1 · Table 2',
    yourQuote: 'E-Agent improves multimodal retrieval planning accuracy by 5.5 points over text-only planners (54.9% vs 49.4%).',
    yourLoc: 'agent.tex · L301',
    matrix: [
      { field: '任务', ours: '多模态检索规划', theirs: 'GUI 工具使用规划', status: 'partial' },
      { field: '基准', ours: 'WebArena-VM 子集', theirs: 'WebArena-VM 全量', status: 'partial' },
      { field: '机制', ours: '多模态索引检索', theirs: '工具调用验证环', status: 'diff' },
      { field: '指标', ours: '规划准确率', theirs: '规划准确率', status: 'same' },
    ],
    why: '不依赖多模态索引即在全量基准上超过你的增益，动摇 C1「多模态检索是必要的」这一隐含前提。',
    suggestion: '消融：在 E-Agent 中加入工具验证环，分离「检索贡献」与「验证贡献」。',
    uncertainty: '其 61.4% 使用了 GPT-4o 级别验证器，成本口径不同。',
    authorProfile: {
      first_author: 'L. Zhao',
      corresponding_author: 'F. Adeyemi',
      first_author_h_index: 47,
      corresponding_author_h_index: 55,
      top_h_index: 55,
      affiliations: ['UC Berkeley', 'Meta AI'],
      primary_institution: 'UC Berkeley',
      is_top_tier: true,
    },
    influenceMetrics: {
      citation_count: 188,
      citation_velocity: 18.2,
      social_heat: 81.4,
      is_top_tier_lab: true,
      top_institution: 'UC Berkeley',
      top_h_index: 55,
    },
    labTimeline: [
      { title: 'MM-Navigator: Multimodal Retrieval Planning with Tool-Use Verification for GUI Agents', year: 2026, venue: 'Preprint / arXiv', citations: 188, url: 'https://arxiv.org/abs/2602.14772', badge: 'Current Trigger' },
      { title: 'Scaling Multimodal Memory for Long-Horizon Web Agents', year: 2025, venue: 'ICML', citations: 540, url: 'https://arxiv.org', badge: 'Milestone Work' },
      { title: 'Web Agent Planning with Tool-Use Verification (L. Zhao et al.)', year: 2024, venue: 'ICLR', citations: 1020, url: 'https://arxiv.org', badge: 'Early Exploration' },
    ],
  },
  {
    id: 'q2',
    title: 'Scaling Multimodal Memory for Long-Horizon Web Agents',
    authors: ['D. Kim', 'S. Laurent', 'A. Rossi'],
    arxivId: '2601.08341',
    date: '2026-01-12',
    verdict: 'support',
    urgency: 'info',
    claimIds: ['C2'],
    quote:
      'Episodic multimodal memory reduces redundant page revisits by 38%, corroborating earlier findings that retrieval over visual traces is key to long-horizon efficiency.',
    quoteLoc: '§5.2 ¶2',
    yourQuote: 'Multimodal retrieval memory cuts redundant navigation steps by 31% on long-horizon tasks.',
    yourLoc: 'agent.tex · L322',
    matrix: [
      { field: '机制', ours: '多模态检索记忆', theirs: '情景式多模态记忆', status: 'same' },
      { field: '收益', ours: '冗余步骤 −31%', theirs: '重复访问 −38%', status: 'same' },
      { field: '任务长度', ours: '≥ 20 步', theirs: '≥ 30 步', status: 'partial' },
    ],
    why: '独立复现并放大你的效率结论，为 C2 提供跨团队支持证据。',
    suggestion: '采纳为支持证据，在 Related Work 并引。',
    uncertainty: '其任务集与你的长程子集重叠度未披露。',
  },
  {
    id: 'q3',
    title: 'Voyager-2: Open-Ended Embodied Planning with Retrieval-Augmented Skill Libraries',
    authors: ['G. Wang', 'I. Petrov', 'et al.'],
    arxivId: '2405.02219',
    date: '2024-05-04',
    verdict: 'prior',
    urgency: 'review',
    claimIds: ['C3'],
    quote:
      'A retrieval-augmented skill library lets the planner compose previously seen multimodal trajectories, a precursor to retrieval-planned web agents.',
    quoteLoc: '§2 ¶4',
    yourQuote: 'E-Agent is the first to couple multimodal retrieval with explicit planning for web agents.',
    yourLoc: 'agent.tex · L64',
    matrix: [
      { field: '组合方式', ours: '多模态检索 + 显式规划', theirs: '技能库检索 + 组合', status: 'partial' },
      { field: '域', ours: 'Web 导航', theirs: '具身/游戏环境', status: 'diff' },
    ],
    why: '「first to couple …」表述存在在先工作风险，Voyager-2 已提出检索增强的技能组合规划。',
    suggestion: '收窄为「首个面向 Web 导航的多模态检索-规划耦合」。',
    uncertainty: '域差异（Web vs 具身）是否足以支撑新颖性，取决于审稿人口径。',
  },
  {
    id: 'q4',
    title: 'WebVoyager-X: An Enterprise-Scale Multimodal Web Agent Benchmark',
    authors: ['M. Haddad', 'Y. Komura', 'et al.'],
    arxivId: '2603.07712',
    date: '2026-03-10',
    verdict: 'boundary',
    urgency: 'review',
    claimIds: ['C1', 'C2'],
    quote:
      'On enterprise workflows with dynamic SSO and canvas-heavy UIs, all evaluated agents—including retrieval-planned variants—fall below 35% task success.',
    quoteLoc: '§3.3 ¶1 · Table 4',
    yourQuote: 'E-Agent generalizes to unseen websites without per-site fine-tuning.',
    yourLoc: 'agent.tex · L345',
    matrix: [
      { field: '场景', ours: '公开网站', theirs: '企业 SSO + canvas 重交互', status: 'diff' },
      { field: '结论', ours: '无需逐站微调可泛化', theirs: '所有 agent < 35% 成功率', status: 'partial' },
    ],
    why: '不直接冲突，但划出边界：企业级重交互 UI 是「无需微调泛化」表述的反例空间。',
    suggestion: '将 C1/C2 适用范围限定为公开网站；把企业场景列为 future work。',
    uncertainty: '该基准尚未公开可跑环境，结论暂不可复现。',
  },
];

const eagentClaims: Claim[] = [
  {
    id: 'C1',
    text: '多模态检索规划较纯文本规划器提升 5.5 点准确率',
    status: 'disputed',
    confirmed: 'yes',
    radarWatch: true,
    quote: 'E-Agent improves multimodal retrieval planning accuracy by 5.5 points over text-only planners.',
    loc: 'agent.tex · L301',
    contract: {
      task: '多模态 Web 导航规划',
      dataset: 'WebArena-VM 子集（412 任务）',
      split: 'test',
      metric: '规划准确率（step-level）',
      baseline: '文本-only Planner, WebGPT-style',
      scope: '公开网站；GPT-4o 后端',
    },
    falsifiable: '同等验证器配置下增益 < 2 点即证伪',
    evidence: [
      { kind: 'challenge', paperId: 'q1', note: '工具验证环无需多模态索引即 +6.8' },
      { kind: 'completeness', paperId: 'q4', note: '企业重交互场景下全部 agent < 35%' },
    ],
  },
  {
    id: 'C2',
    text: '多模态检索记忆减少 31% 冗余导航步骤',
    status: 'supported',
    confirmed: 'yes',
    radarWatch: false,
    quote: 'Multimodal retrieval memory cuts redundant navigation steps by 31% on long-horizon tasks.',
    loc: 'agent.tex · L322',
    contract: {
      task: '长程 Web 任务（≥20 步）',
      dataset: '自建长程子集（86 任务）',
      split: 'test',
      metric: '冗余步骤占比',
      baseline: '无记忆 E-Agent',
      scope: ' episodic 记忆容量 ≤ 200 条',
    },
    falsifiable: '冗余步骤降幅 < 10% 即证伪',
    evidence: [{ kind: 'support', paperId: 'q2', note: '独立复现：重复访问 −38%' }],
  },
  {
    id: 'C3',
    text: '首个将多模态检索与显式规划耦合的 Web Agent',
    status: 'revalidate',
    confirmed: 'pending',
    radarWatch: true,
    quote: 'E-Agent is the first to couple multimodal retrieval with explicit planning for web agents.',
    loc: 'agent.tex · L64',
    contract: { task: '新颖性声明', dataset: '—', split: '—', metric: '—', baseline: '—', scope: 'Web 导航域' },
    falsifiable: '存在更早的 Web 域多模态检索-规划系统即证伪',
    evidence: [{ kind: 'challenge', paperId: 'q3', note: 'Voyager-2：检索增强技能库（具身域在先）' }],
  },
  {
    id: 'C4',
    text: '在未见过的网站上无需逐站微调即可泛化',
    status: 'valid',
    confirmed: 'yes',
    radarWatch: false,
    quote: 'E-Agent generalizes to unseen websites without per-site fine-tuning.',
    loc: 'agent.tex · L345',
    contract: {
      task: '跨站泛化',
      dataset: '30 个留出站点',
      split: 'held-out',
      metric: '任务成功率',
      baseline: '逐站微调上界',
      scope: '公开网站，非 SSO/重 canvas',
    },
    falsifiable: '留出站点成功率低于文本基线即证伪',
    evidence: [{ kind: 'completeness', paperId: 'q4', note: '企业 SSO 场景为已知反例空间' }],
  },
];

const eagentActions: ActionItem[] = [
  {
    id: 'b1',
    kind: 'experiment',
    priority: 'P0',
    title: '消融：分离「多模态检索」与「工具验证环」的贡献',
    due: '3 天内',
    claimId: 'C1',
    sourcePaperId: 'q1',
    reason: '工具调用验证环不依赖多模态索引即超过你的增益，动摇 C1 前提。',
    checklist: ['实现验证环变体', '同验证器配置对比', '重测 5.5 点增益'],
  },
  {
    id: 'b2',
    kind: 'writing',
    priority: 'P1',
    title: '收窄 C3 新颖性表述至 Web 导航域',
    due: '本周内',
    claimId: 'C3',
    sourcePaperId: 'q3',
    reason: 'Voyager-2 在更早时间点提出了类似的内存整合方法。',
    checklist: ['改写 L64', '补引 Voyager-2', 'Related Work 加一段'],
  },
  {
    id: 'b3',
    kind: 'writing',
    priority: 'P1',
    title: '为 C2 添加跨团队支持引文',
    due: '本周内',
    claimId: 'C2',
    sourcePaperId: 'q2',
    reason: '跨团队独立验证了多模态内存的有效性，为 C2 提供独立支持。',
    checklist: ['引用 Scaling Multimodal Memory', '对比 31% / 38% 口径'],
  },
  {
    id: 'b4',
    kind: 'data',
    priority: 'P2',
    title: '跟踪 WebVoyager-X 环境开放情况',
    due: '两周内',
    claimId: 'C4',
    sourcePaperId: 'q4',
    reason: 'WebVoyager-X 的企业 SSO 场景是已知反例空间，需跟踪环境开放。',
    checklist: ['订阅其项目页', '环境开放后跑留出站点'],
  },
];

// =================================================================
// shared assembly
// =================================================================

const rareAudit: AuditRec[] = [
  { stage: 'PDF 全文提取', provider: '本地', model: 'grobid-0.8.1', duration: '41s', cost: '¥0.00', result: 'pass' },
  { stage: '混合检索', provider: '共享 API', model: 'text-embedding-3-large', duration: '18s', cost: '¥0.00', result: 'pass' },
  { stage: '逐项比较 ×6', provider: '共享 API', model: 'deepseek-v3.2', duration: '3m 12s', cost: '¥1.86', result: 'pass' },
  { stage: '证据定位校验', provider: '共享 API', model: 'deepseek-v3.2', duration: '58s', cost: '¥0.42', result: 'warn' },
  { stage: '改写草案生成', provider: '共享 API', model: 'qwen3-235b', duration: '1m 04s', cost: '¥0.31', result: 'pass' },
];

export const projects: Project[] = [
  {
    id: 'rare',
    name: 'RARE: Retrieval-Aware Robustness Evaluation',
    short: 'RARE',
    question: '检索感知的重排序能否在真实查询扰动下保持鲁棒增益？',
    version: 'v4',
    file: 'main.tex',
    claimsConfirmed: 4,
    claimsTotal: 5,
    lastScan: '2026-07-22 21:40',
    urgent: 2,
    topics: ['retrieval-augmented generation', 'query perturbation robustness', 'reranking', 'adversarial queries', 'open-domain QA'],
    claims: rareClaims,
    papers: rarePapers,
    actions: rareActions,
    versions: [
      { v: 'v4', date: '2026-07-20', file: 'main.tex', claims: 5, note: '补充 C4 双检索器实验' },
      { v: 'v3', date: '2026-06-28', file: 'main_v3.tex', claims: 5, note: '重排 Related Work' },
      { v: 'v2', date: '2026-05-30', file: 'rare_v2.pdf', claims: 4, note: '新增 C5 覆盖性论证' },
      { v: 'v1', date: '2026-04-12', file: 'rare_draft.pdf', claims: 3, note: '初稿导入' },
    ],
    audit: rareAudit,
    competitors: [
      { id: 'w-kowalski', team: 'Kowalski Lab (MILA)', aliases: ['N. Kowalski', 'Nina Kowalski', 'Kowalski et al.'] },
      { id: 'w-sato', team: 'Sato NLP Group', aliases: ['K. Sato', 'Kei Sato'] },
    ],
    profile: {
      question: '检索感知重排序在查询扰动下的鲁棒性边界在哪里？',
      thesis: '鲁棒性应作为检索在环系统的一阶评估维度，而非事后诊断。',
      contributions: ['六族扰动评估协议', '检索在环鲁棒重排序器 RARE', '双检索器泛化证据'],
      findings: ['静态扰动下 EM 下降 ≤ 5 点', 'BEIR 5 子集 +3.1 nDCG@10', '8B 开销 < 12%'],
      limits: ['未覆盖自适应对抗扰动', '静态检索预算假设', '仅英文开放域'],
    },
    rewrite: {
      claimId: 'C1',
      loc: 'main.tex · L214–218',
      before:
        'Across all six perturbation families, RARE’s accuracy drop remains bounded within 5 points relative to the clean-query setting, demonstrating robustness to query distribution shift.',
      after:
        'Across all six static perturbation families, RARE’s accuracy drop remains bounded within 5 points relative to the clean-query setting. Under adaptive adversarial perturbation (Xiang et al., 2026), the drop can exceed this envelope, which we analyze in §5.3.',
      checks: [
        { label: '影响已人工采用', ok: true },
        { label: '改写前文本精确定位', ok: true },
        { label: '引用来源已登记', ok: true },
        { label: '未引入数字编号引用', ok: true },
        { label: '原文数字保持不变', ok: true },
        { label: '原论文文件未被修改', ok: true },
      ],
    },
  },
  {
    id: 'eagent',
    name: 'E-Agent: 多模态检索规划',
    short: 'E-Agent',
    question: '多模态检索记忆能否替代逐站微调，实现 Web Agent 的跨站泛化？',
    version: 'v2',
    file: 'agent.tex',
    claimsConfirmed: 3,
    claimsTotal: 4,
    lastScan: '2026-07-21 09:15',
    urgent: 1,
    topics: ['multimodal web agent', 'retrieval planning', 'episodic memory', 'WebArena', 'GUI grounding'],
    claims: eagentClaims,
    papers: eagentPapers,
    actions: eagentActions,
    versions: [
      { v: 'v2', date: '2026-07-02', file: 'agent.tex', claims: 4, note: '新增 C4 泛化实验' },
      { v: 'v1', date: '2026-05-18', file: 'agent_v1.pdf', claims: 3, note: '初稿导入' },
    ],
    audit: [
      { stage: 'PDF 全文提取', provider: '本地', model: 'grobid-0.8.1', duration: '33s', cost: '¥0.00', result: 'pass' },
      { stage: '混合检索', provider: '共享 API', model: 'text-embedding-3-large', duration: '11s', cost: '¥0.00', result: 'pass' },
      { stage: '逐项比较 ×4', provider: '共享 API', model: 'deepseek-v3.2', duration: '2m 20s', cost: '¥1.12', result: 'pass' },
    ],
    competitors: [{ id: 'w-zhao', team: 'Zhao HCI Group', aliases: ['L. Zhao', 'Lu Zhao'] }],
    profile: {
      question: '多模态检索记忆能否替代逐站微调？',
      thesis: '视觉轨迹的检索式记忆是 Web Agent 跨站泛化的关键机制。',
      contributions: ['多模态检索-规划耦合架构', '长程任务记忆协议', '412 任务评测子集'],
      findings: ['规划准确率 +5.5 点', '冗余步骤 −31%', '30 站留出泛化成立'],
      limits: ['企业 SSO 场景未覆盖', '依赖 GPT-4o 级后端'],
    },
    rewrite: {
      claimId: 'C3',
      loc: 'agent.tex · L64',
      before: 'E-Agent is the first to couple multimodal retrieval with explicit planning for web agents.',
      after:
        'E-Agent is, to our knowledge, the first system to couple multimodal retrieval with explicit planning in the web navigation domain, extending retrieval-augmented skill composition (Wang et al., 2024) beyond embodied settings.',
      checks: [
        { label: '影响已人工采用', ok: true },
        { label: '改写前文本精确定位', ok: true },
        { label: '引用来源已登记', ok: true },
        { label: '未引入数字编号引用', ok: false },
        { label: '原文数字保持不变', ok: true },
        { label: '原论文文件未被修改', ok: true },
      ],
    },
  },
];

// ---------- lookup helpers ----------

export const verdictMeta: Record<Verdict, { label: string; cls: string }> = {
  challenge: { label: '挑战', cls: 'badge-red' },
  support: { label: '支持', cls: 'badge-green' },
  boundary: { label: '边界条件', cls: 'badge-orange' },
  prior: { label: '在先工作', cls: 'badge-blue' },
  none: { label: '无材料性变化', cls: 'badge-gray' },
};

export const claimStatusMeta: Record<ClaimStatus, { label: string; cls: string }> = {
  valid: { label: '有效', cls: 'badge-green' },
  supported: { label: '已被支持', cls: 'badge-teal' },
  disputed: { label: '存在争议', cls: 'badge-red' },
  revalidate: { label: '需要重新验证', cls: 'badge-orange' },
  history: { label: '历史', cls: 'badge-gray' },
};

export const matrixStatusMeta: Record<MatrixStatus, { label: string; cls: string }> = {
  same: { label: '一致', cls: 'badge-green' },
  partial: { label: '部分一致', cls: 'badge-orange' },
  diff: { label: '不一致', cls: 'badge-red' },
};

export const kindMeta: Record<ActionItem['kind'], string> = {
  experiment: '改实验',
  data: '补数据',
  writing: '调整写作',
  competitive: '竞争预警',
  revalidate: '重新验证',
};
