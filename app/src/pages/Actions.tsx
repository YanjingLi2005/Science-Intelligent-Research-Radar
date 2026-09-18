import { useMemo, useState } from 'react';
import { BarChart3, FlaskConical, MessageCircleMore, PenLine, SearchCheck } from 'lucide-react';
import { verdictMeta, claimStatusMeta, kindMeta, matrixStatusMeta, sourceLabel } from '../data/mock';
import { Reveal, Stat, Tabs, Quote, Priority, Empty } from '../components/chrome';
import { BenchmarkComparison } from '../components/BenchmarkComparison';
import { Spinner } from '../components/ui/spinner';
import { useProjectId } from '../contexts/ProjectContext';
import { SkillResultView } from '../components/SkillResult';
import ErrorRetry from '../components/ErrorRetry';
import {
  getActions,
  getImpacts,
  getClaims,
  confirmImpact,
  dismissImpact,
  updateActionStatus,
  executeSkill,
  generatePatch,
  approvePatch,
  rejectPatch,
  useApi,
} from '../api';

type ActionState = 'todo' | 'doing' | 'done' | 'dismissed';

function persistedActionState(status: string | undefined): ActionState {
  if (status === 'in_progress') return 'doing';
  if (status === 'done') return 'done';
  if (status === 'dismissed') return 'dismissed';
  return 'todo';
}

interface SkillEdit {
  section: string;
  edit_class: string;
  before_text: string;
  after_text: string;
  reason: string;
}

type SkillArtifact = Record<string, unknown>;

export default function Actions() {
  const caseId = useProjectId();
  const { data: actions, loading: actionsLoading, error: actionsError, refetch: refetchActions } = useApi(
    () => getActions(caseId),
    [caseId],
  );
  const { data: papers, loading: papersLoading, error: papersError, refetch: refetchImpacts } = useApi(
    () => getImpacts(caseId),
    [caseId],
  );
  const { data: claims, loading: claimsLoading, error: claimsError } = useApi(
    () => getClaims(caseId),
    [caseId],
  );

  const loading = actionsLoading || papersLoading || claimsLoading;

  // ---- local UI state ----
  const [tab, setTab] = useState(0);
  const [states, setStates] = useState<Record<string, ActionState>>({});
  const [decisions, setDecisions] = useState<Record<string, 'adopted' | 'rejected'>>({});
  const [expandedActionId, setExpandedActionId] = useState<string | null>(null);
  const [expandedImpactId, setExpandedImpactId] = useState<string | null>(null);
  const [expandedClaimId, setExpandedClaimId] = useState<string | null>(null);
  const [operationError, setOperationError] = useState('');

  // Skill execution + diff review state
  const [skillLoading, setSkillLoading] = useState<string | null>(null);
  const [skillError, setSkillError] = useState('');
  const [skillResults, setSkillResults] = useState<Record<string, SkillEdit[]>>({});
  const [skillArtifacts, setSkillArtifacts] = useState<Record<string, SkillArtifact>>({});

  // ---- derived ----
  const p0 = useMemo(() => {
    if (!actions) return undefined;
    return actions.find(
      (a) =>
        a.priority === 'P0'
        && (states[a.id] ?? persistedActionState(a.status)) !== 'done',
    );
  }, [actions, states]);

  const useful = (papers ?? []).filter((p) => p.verdict !== 'none');
  const affected = (claims ?? []).filter((c) => c.status === 'disputed' || c.status === 'revalidate');

  const setA = (id: string, s: ActionState) => setStates((m) => ({ ...m, [id]: s }));

  const persistAction = async (
    id: string,
    previous: ActionState,
    next: ActionState,
    apiStatus: string,
  ) => {
    setOperationError('');
    setA(id, next);
    try {
      await updateActionStatus(caseId, id, apiStatus);
      refetchActions();
    } catch (e) {
      setA(id, previous);
      setOperationError(e instanceof Error ? e.message : String(e));
    }
  };

  const persistImpact = async (id: string, decision: 'adopted' | 'rejected') => {
    setOperationError('');
    try {
      if (decision === 'adopted') await confirmImpact(caseId, id);
      else await dismissImpact(caseId, id);
      setDecisions((current) => ({ ...current, [id]: decision }));
      refetchImpacts();
    } catch (e) {
      setOperationError(e instanceof Error ? e.message : String(e));
    }
  };

  // Execute writer skill and show diffs
  const handleSkillExecute = async (actionId: string, skillName: string, actionKind: string) => {
    setSkillLoading(actionId);
    setSkillError('');
    setSkillResults((prev) => ({ ...prev, [actionId]: [] }));
    setSkillArtifacts((prev) => {
      const next = { ...prev };
      delete next[actionId];
      return next;
    });
    try {
      // Send the confirmed evidence context (all linked impacts) so the
      // skills run with real material, not an empty list.
      const impactIds = (claims ?? []).flatMap((c) =>
        (c.evidence ?? []).map((e) => e.paperId),
      );
      const result = await executeSkill(caseId, skillName, actionKind, impactIds);
      const edits = (result.content?.edits as SkillEdit[]) ?? [];
      setSkillResults((prev) => ({ ...prev, [actionId]: edits }));
      setSkillArtifacts((prev) => ({ ...prev, [actionId]: result.content as SkillArtifact }));
      setPatchState((prev) => {
        const next = { ...prev };
        delete next[actionId];
        return next;
      });
    } catch (e) {
      setSkillError(e instanceof Error ? e.message : String(e));
    } finally {
      setSkillLoading(null);
    }
  };

  // Patch-level approval, persisted server-side (P1): the skill edits are
  // reviewed as diffs, but 批准/拒绝 operates on a real PatchProposal so the
  // decision survives reloads.
  const [patchIds, setPatchIds] = useState<Record<string, string | null>>({});
  const [patchState, setPatchState] = useState<Record<string, 'approved' | 'rejected' | null>>({});
  const [patchBusy, setPatchBusy] = useState<Record<string, boolean>>({});

  const ensurePatch = async (actionId: string): Promise<string | null> => {
    const existing = patchIds[actionId];
    if (existing) return existing;
    const impact = (papers ?? []).find(
      (p) => p.reviewState === 'confirmed' || p.reviewState === 'edited',
    ) ?? (papers ?? []).find((p) => p.verdict !== 'none');
    if (!impact) {
      setOperationError('没有已确认的影响，无法生成补丁');
      return null;
    }
    const patch = await generatePatch(caseId, impact.id);
    setPatchIds((prev) => ({ ...prev, [actionId]: patch.patchId }));
    return patch.patchId;
  };

  const handleApproveEdits = async (actionId: string) => {
    setPatchBusy((prev) => ({ ...prev, [actionId]: true }));
    setOperationError('');
    try {
      const patchId = await ensurePatch(actionId);
      if (!patchId) return;
      await approvePatch(caseId, patchId);
      setPatchState((prev) => ({ ...prev, [actionId]: 'approved' }));
    } catch (e) {
      setOperationError(e instanceof Error ? e.message : String(e));
    } finally {
      setPatchBusy((prev) => ({ ...prev, [actionId]: false }));
    }
  };

  const handleRejectEdits = async (actionId: string) => {
    setPatchBusy((prev) => ({ ...prev, [actionId]: true }));
    setOperationError('');
    try {
      const patchId = await ensurePatch(actionId);
      if (!patchId) return;
      await rejectPatch(caseId, patchId);
      setPatchState((prev) => ({ ...prev, [actionId]: 'rejected' }));
    } catch (e) {
      setOperationError(e instanceof Error ? e.message : String(e));
    } finally {
      setPatchBusy((prev) => ({ ...prev, [actionId]: false }));
    }
  };

  // ---- metrics ----
  const list = useMemo(() => actions ?? [], [actions]);
  const visibleActions = useMemo(
    () => list.filter((a) => (states[a.id] ?? persistedActionState(a.status)) !== 'dismissed'),
    [list, states],
  );
  const metrics = [
    { v: list.filter((a) => a.priority === 'P0').length, l: '紧急', tone: 'red' as const },
    { v: list.filter((a) => a.kind === 'experiment').length, l: '改实验' },
    { v: list.filter((a) => a.kind === 'data').length, l: '补数据' },
    { v: list.filter((a) => a.kind === 'writing').length, l: '调整写作' },
    { v: list.filter((a) => a.kind === 'competitive').length, l: '竞争预警', tone: 'orange' as const },
    { v: list.filter((a) => a.kind === 'revalidate').length, l: '重新验证' },
  ];

  const skillTiles = [
    { label: 'Writer', desc: '生成可审阅的写作修改', icon: PenLine },
    { label: 'Experiment', desc: '设计补实验与统计护栏', icon: FlaskConical },
    { label: 'Auditor', desc: '核查 Claim—证据对齐', icon: SearchCheck },
    { label: 'Rebuttal', desc: '准备审稿回应与边界说明', icon: MessageCircleMore },
    { label: 'Reviewer', desc: '从审稿人视角复核风险', icon: BarChart3 },
  ];

  const scrollTo = (id: string) => {
    setTimeout(() => {
      document.getElementById(`action-${id}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }, 100);
  };

  const findPaper = (id: string) => (papers ?? []).find((p) => p.id === id);
  const findClaim = (id: string) => (claims ?? []).find((claim) => claim.id === id);

  if (loading) {
    return (
      <div>
        <Reveal>
          <div className="kicker mb-2">Actions</div>
          <h2 className="text-[17px] font-semibold tracking-tight mb-8">把新证据变成行动</h2>
        </Reveal>
        <div aria-busy="true" aria-live="polite" className="flex items-center justify-center py-20">
          <Spinner className="size-8 text-neutral-400" />
        </div>
      </div>
    );
  }

  return (
    <div>
      <Reveal>
        <div className="kicker mb-1.5">Actions</div>
        <h2 className="text-[17px] font-semibold tracking-tight">行动队列</h2>
        <p className="mt-2 mb-8 text-[12.5px] text-neutral-500 dark:text-white/80">
          确认影响证据后，决定下一步补实验、补引用或调整论文表述 —— 每条行动都从雷达证据生成，人工把关。
        </p>
      </Reveal>

      {/* headline banner */}
      <Reveal delay={60}>
        <div className="card p-6 md:p-7 flex flex-col md:flex-row md:items-center gap-6 justify-between">
          <div>
            <div className="kicker mb-3 text-[#B42318] dark:text-red-300">需要马上处理</div>
            <p className="text-[15px] md:text-base font-medium leading-snug max-w-2xl text-ink dark:text-white">
              {p0 ? p0.title : '本周没有 P0 事项，保持监控即可。'}
            </p>
          </div>
          {p0 && (
            <button
              className="btn-primary shrink-0"
              onClick={() => {
                setExpandedActionId(p0.id);
                setTab(0);
                scrollTo(p0.id);
              }}
            >
              查看执行清单
            </button>
          )}
        </div>
      </Reveal>

      {/* metrics */}
      <Reveal delay={100}>
        <div className="grid grid-cols-3 md:grid-cols-6 gap-6 mt-8 pb-8 hairline-b">
          {metrics.map((m) => (
            <Stat key={m.l} value={m.v} label={m.l} tone={m.tone} />
          ))}
        </div>
      </Reveal>

      <Reveal delay={140}>
        <div className="grid grid-cols-2 md:grid-cols-5 gap-3 mt-8">
          {skillTiles.map(({ label, desc, icon: Icon }) => (
            <button
              key={label}
              className="group card card-hover p-4 text-left"
              onClick={() => setTab(0)}
            >
              <Icon size={19} strokeWidth={1.5} className="text-neutral-400 transition-transform duration-300 group-hover:-translate-y-0.5 group-hover:scale-110 group-hover:text-ink dark:text-white/80 dark:group-hover:text-white" />
              <div className="mt-3 text-sm font-semibold text-ink dark:text-white">{label}</div>
              <div className="mt-1 text-[11px] leading-relaxed text-neutral-400 dark:text-white/80">{desc}</div>
            </button>
          ))}
        </div>
      </Reveal>

      <div className="mt-8">
        {(operationError || actionsError || papersError || claimsError) && (
          <div className="mb-4">
            <ErrorRetry message={operationError || actionsError?.message || papersError?.message || claimsError?.message || '数据加载失败'} onRetry={() => { void refetchActions(); void refetchImpacts(); }} />
          </div>
        )}
        {skillError && (
          <div className="mb-4">
            <ErrorRetry message={skillError} />
          </div>
        )}
        <Tabs tabs={['下一步行动', '影响证据', '论文 Claim']} active={tab} onChange={setTab} />

        {/* tab 0 — actions */}
        {tab === 0 && (
          <div className="grid gap-4">
            {visibleActions.length === 0 && (
              <Empty text={list.length === 0 ? '暂无扫描结果，去文献雷达搜一下' : '所有行动建议均已完成或标记不处理'} />
            )}
            {visibleActions.map((a, i) => {
              const st = states[a.id] ?? persistedActionState(a.status);
              const src = findPaper(a.sourcePaperId);
              const isExpanded = expandedActionId === a.id;
              const edits = skillResults[a.id] ?? [];
              const artifact = skillArtifacts[a.id];
              const experiments = Array.isArray(artifact?.experiments)
                ? artifact.experiments as Record<string, unknown>[]
                : [];
              return (
                <Reveal key={a.id} delay={i * 60}>
                  <article id={`action-${a.id}`} className={`card p-6 ${st === 'done' ? 'opacity-55' : ''}`}>
                    <div className="flex flex-wrap items-center gap-3 mb-3">
                      <Priority p={a.priority} />
                      <span className="badge-gray">{kindMeta[a.kind]}</span>
                      <span className="locator">建议期限 · {a.due}</span>
                      <span className="locator ml-auto">{a.claimId}</span>
                    </div>
                    <h2 className={`font-medium text-[15px] leading-relaxed ${st === 'done' ? 'line-through' : ''}`}>
                      {a.title}
                    </h2>
                    {findClaim(a.claimId) && (
                      <p className="mt-2 text-xs text-neutral-500 dark:text-white/85 leading-relaxed">
                        <span className="font-mono text-neutral-400">{a.claimId}</span>{' '}
                        {findClaim(a.claimId)?.text}
                      </p>
                    )}
                    {a.reason && (
                      <p className="mt-2 text-[13px] text-neutral-500 dark:text-white/85 leading-relaxed">{a.reason}</p>
                    )}
                    {src && (
                      <p className="mt-2 text-xs text-neutral-400">
                        证据来源：<span className="font-mono">{sourceLabel(src)}</span> · {src.title.slice(0, 64)}...
                      </p>
                    )}
                    {isExpanded && (
                      <ul className="mt-4 grid md:grid-cols-2 gap-x-8 gap-y-1.5">
                        {a.checklist.map((c, j) => (
                          <li key={j} className="flex gap-2 text-[13px] text-neutral-500 dark:text-white/85">
                            <span className={st === 'done' ? 'text-teal' : 'text-neutral-300'}>
                              {st === 'done' ? '✓' : '□'}
                            </span>
                            {c}
                          </li>
                        ))}
                      </ul>
                    )}

                    {/* ---- AI Skill: Diff Review Panel ---- */}
                    {edits.length > 0 && (
                      <div className="mt-5 pt-4 hairline-t">
                        <div className="mb-3 text-xs uppercase tracking-wide text-neutral-500 dark:text-white/85">
                          AI 修改建议
                        </div>
                        {edits.map((edit, j) => (
                            <div key={j} className="mb-4 last:mb-0 overflow-hidden rounded-lg border border-neutral-100 dark:border-white/10">
                              <div className="flex items-center gap-2 bg-neutral-50 px-4 py-2 text-xs dark:bg-white/[0.04]">
                                <span className="badge-teal">{edit.section}</span>
                                <span className="text-neutral-400 dark:text-white/80">{edit.edit_class}</span>
                                <span className="ml-auto italic text-neutral-500 dark:text-white/85">{edit.reason}</span>
                              </div>
                              <div className="diff-grid text-[13px]">
                                <div className="diff-cell diff-before">
                                  <div className="mb-1 text-[10px] text-red-500 dark:text-red-400">— 删除</div>
                                  <p className="diff-removed">
                                    {edit.before_text}
                                  </p>
                                </div>
                                <div className="diff-cell diff-after">
                                  <div className="mb-1 text-[10px] text-green-600 dark:text-green-400">+ 新增</div>
                                  <p className="diff-added">
                                    {edit.after_text}
                                  </p>
                                </div>
                              </div>
                              <div className="hairline-t px-4 py-2 text-[11px] text-neutral-400 dark:text-white/75">
                                技能生成的修改建议 —— 通过下方「批准/拒绝」落库为正式补丁
                              </div>
                            </div>
                          ))}
                      </div>
                    )}

                    {experiments.length > 0 && (
                      <div className="mt-5 pt-4 hairline-t">
                        <div className="mb-3 text-xs uppercase tracking-wide text-neutral-500 dark:text-white/85">
                          Experiment Card
                        </div>
                        <div className="grid gap-3">
                          {experiments.map((experiment, index) => {
                            const design = (experiment.statistical_design ?? {}) as Record<string, unknown>;
                            const guardrails = [
                              ['样本量', design.sample_size],
                              ['Power', design.power_target],
                              ['最小效应', design.effect_size],
                              ['不确定性', design.confidence_interval],
                              ['随机化 / blocking', design.randomization],
                              ['Seeds', design.seeds],
                            ]
                              .filter(([, value]) => typeof value === 'string' && value.trim())
                              .map(([label, value]) => [String(label), String(value)] as [string, string]);
                            const falsification = typeof experiment.falsification === 'string'
                              ? experiment.falsification
                              : '';
                            return (
                              <div key={index} className="rounded-lg border border-neutral-100 p-4 dark:border-white/10">
                                <div className="text-sm font-medium">{String(experiment.title || `实验 ${index + 1}`)}</div>
                                <p className="mt-1 text-xs text-neutral-500 dark:text-white/85">{String(experiment.goal || '')}</p>
                                <div className="mt-3 grid gap-2 text-xs md:grid-cols-2">
                                  {guardrails.map(([label, value]) => (
                                    <div key={label} className="rounded bg-neutral-50 px-2.5 py-2 dark:bg-white/[0.04]">
                                      <span className="text-neutral-400 dark:text-white/80">{label}</span>
                                      <div className="mt-0.5 text-neutral-700 dark:text-white/90">{String(value)}</div>
                                    </div>
                                  ))}
                                </div>
                                {falsification && (
                                  <p className="mt-3 text-xs text-[#B42318]/80 dark:text-red-300/90">
                                    证伪条件：{falsification}
                                  </p>
                                )}
                              </div>
                            );
                          })}
                        </div>
                      </div>
                    )}

                    {artifact && (
                      <SkillResultView
                        output={artifact}
                        excludeKeys={['experiments', 'edits']}
                        className="mt-5 pt-4 hairline-t"
                        renderRawFallback={false}
                      />
                    )}

                    <div className="mt-5 pt-4 hairline-t flex flex-wrap gap-2">
                      {a.suggestedSkill && edits.length === 0 && experiments.length === 0 && (
                        <button
                          className="btn-primary text-xs"
                          disabled={skillLoading === a.id}
                          onClick={() => handleSkillExecute(a.id, a.suggestedSkill ?? '', a.kind)}
                        >
                          {skillLoading === a.id
                            ? 'AI 生成中...'
                            : a.kind === 'writing' ? 'AI 生成修改建议' : '生成 Experiment Card'}
                        </button>
                      )}
                      {edits.length > 0 && patchState[a.id] === undefined && (
                        <>
                          <button
                            className="btn-primary text-xs"
                            disabled={patchBusy[a.id]}
                            onClick={() => void handleApproveEdits(a.id)}
                          >
                            {patchBusy[a.id] ? '提交中…' : '批准（落库）'}
                          </button>
                          <button
                            className="btn-quiet text-xs"
                            disabled={patchBusy[a.id]}
                            onClick={() => void handleRejectEdits(a.id)}
                          >
                            拒绝（落库）
                          </button>
                        </>
                      )}
                      {patchState[a.id] === 'approved' && (
                        <span className="badge-green badge-dot text-xs">已批准 · 可在改进页导出</span>
                      )}
                      {patchState[a.id] === 'rejected' && (
                        <span className="badge-red badge-dot text-xs">已拒绝</span>
                      )}
                      {st === 'todo' && (
                        <button
                          className="btn-primary"
                          onClick={() => void persistAction(a.id, st, 'doing', 'in_progress')}
                        >
                          开始
                        </button>
                      )}
                      {st !== 'done' && (
                        <button
                          className="btn-primary"
                          onClick={() => void persistAction(a.id, st, 'done', 'done')}
                        >
                          完成
                        </button>
                      )}
                      {st !== 'done' && (
                        <button
                          className="btn-quiet"
                          onClick={() => void persistAction(a.id, st, 'dismissed', 'dismissed')}
                        >
                          不处理
                        </button>
                      )}
                      {st === 'done' && <span className="badge-green badge-dot">已完成</span>}
                    </div>
                  </article>
                </Reveal>
              );
            })}
          </div>
        )}

        {/* tab 1 — impact evidence */}
        {tab === 1 && (
          <div className="grid gap-4">
            {useful.length === 0 && <Empty text="暂无影响证据，先完成一次文献扫描" />}
            {useful.map((p, i) => {
              const d = decisions[p.id]
                ?? (p.reviewState === 'confirmed'
                  ? 'adopted'
                  : p.reviewState === 'dismissed'
                    ? 'rejected'
                    : undefined);
              const isExpanded = expandedImpactId === p.id;
              return (
                <Reveal key={p.id} delay={i * 60}>
                  <article className="card p-6">
                    <div className="flex flex-wrap items-center gap-3 mb-3">
                      <span className={verdictMeta[p.verdict].cls}>{verdictMeta[p.verdict].label}</span>
                      {(p.empiricalDominance || p.strategicFlags?.includes('EMPIRICAL_DOMINANCE')) && (
                        <span className="badge-red text-[10px] font-bold font-mono">EMPIRICAL_DOMINANCE</span>
                      )}
                      <span className="locator">证据来源 · {sourceLabel(p)} · {p.date}</span>
                      <span className="locator ml-auto">{p.claimIds.join(' · ')}</span>
                    </div>
                    <button
                      className="text-left w-full"
                      onClick={() =>
                        setExpandedImpactId((prev) => (prev === p.id ? null : p.id))
                      }
                    >
                      <h2 className="font-medium text-[15px] leading-relaxed hover:text-teal transition-colors">
                        {p.title}
                      </h2>
                    </button>
                    <p className="mt-1 text-xs text-neutral-400">{p.authors.join(', ')}</p>
                    <div className="mt-4">
                      <Quote loc={p.quoteLoc} against={p.verdict === 'challenge'} label="证据引文">
                        {p.quote}
                      </Quote>
                    </div>
                    {p.why && (
                      <p className="mt-3 text-[13px] text-neutral-500 dark:text-white/85 leading-relaxed line-clamp-2">
                        <span className="text-neutral-400 dark:text-white/80">为什么相关：</span> {p.why}
                      </p>
                    )}

                    {isExpanded && (
                      <div className="mt-5 pt-5 hairline-t">
                        {p.matrix.length > 0 && (
                          <div className="mb-5">
                            <div className="locator mb-2 uppercase">条件对比</div>
                            <div className="overflow-x-auto">
                              <table className="table-base min-w-[500px] text-[13px]">
                                <thead>
                                  <tr>
                                    <th>维度</th>
                                    <th>我们的</th>
                                    <th>他们的</th>
                                    <th>状态</th>
                                  </tr>
                                </thead>
                                <tbody>
                                  {p.matrix.map((row) => (
                                    <tr key={row.field}>
                                      <td className="text-neutral-600 dark:text-white/90">{row.field}</td>
                                      <td className="text-neutral-600 dark:text-white/90">{row.ours}</td>
                                      <td className="text-neutral-600 dark:text-white/90">{row.theirs}</td>
                                      <td>
                                        <span className={matrixStatusMeta[row.status].cls}>
                                          {matrixStatusMeta[row.status].label}
                                        </span>
                                      </td>
                                    </tr>
                                  ))}
                                </tbody>
                              </table>
                            </div>
                          </div>
                        )}

                        <div className="mb-5">
                          <div className="locator mb-2 uppercase">量化实验基准对比 (Benchmark Comparison)</div>
                          <BenchmarkComparison data={p.benchmarkComparison} paperTitle={p.title} />
                        </div>

                        {p.why && (
                          <div className="mb-3">
                            <div className="locator mb-1 uppercase">分析</div>
                            <p className="text-[13px] text-neutral-600 dark:text-white/90 leading-relaxed">{p.why}</p>
                          </div>
                        )}
                        {p.suggestion && (
                          <div className="mb-3">
                            <div className="locator mb-1 uppercase">建议</div>
                            <p className="text-[13px] text-neutral-600 dark:text-white/90 leading-relaxed">{p.suggestion}</p>
                          </div>
                        )}
                        {p.uncertainty && (
                          <div>
                            <div className="locator mb-1 uppercase">不确定性</div>
                            <p className="text-[13px] text-neutral-600 dark:text-white/90 leading-relaxed">{p.uncertainty}</p>
                          </div>
                        )}
                      </div>
                    )}

                    <div className="mt-5 pt-4 hairline-t flex flex-wrap items-center gap-2">
                      {!d && (
                        <>
                          <button
                            className="btn-primary"
                            onClick={() => void persistImpact(p.id, 'adopted')}
                          >
                            确认这条证据
                          </button>
                          <button
                            className="btn-quiet"
                            onClick={() => void persistImpact(p.id, 'rejected')}
                          >
                            暂不采用
                          </button>
                        </>
                      )}
                      {d === 'adopted' && <span className="badge-green badge-dot">已确认 · 已生成写作证据</span>}
                      {d === 'rejected' && <span className="badge-gray badge-dot">已标记不采用</span>}
                      <span className="locator ml-auto">{p.sourceUrl || sourceLabel(p)}</span>
                    </div>
                  </article>
                </Reveal>
              );
            })}
          </div>
        )}

        {/* tab 2 — affected claims */}
        {tab === 2 && (
          <div className="grid gap-4">
            {affected.length === 0 && <Empty text="目前没有需要复核的论文 Claim" />}
            {affected.map((c, i) => {
              const isExpanded = expandedClaimId === c.id;
              return (
                <Reveal key={c.id} delay={i * 60}>
                  <article className="card p-6">
                    <div className="flex flex-col md:flex-row md:items-center gap-5">
                      <button
                        className="font-mono text-2xl text-neutral-400 shrink-0 w-14 text-left hover:text-teal transition-colors dark:text-white/80"
                        onClick={() =>
                          setExpandedClaimId((prev) => (prev === c.id ? null : c.id))
                        }
                      >
                        {c.id}
                      </button>
                      <div className="flex-1 min-w-0">
                        <div className="flex flex-wrap items-center gap-2 mb-2">
                          <span className={claimStatusMeta[c.status].cls}>
                            {claimStatusMeta[c.status].label}
                          </span>
                          {c.radarWatch && <span className="badge-teal badge-dot">Radar 关注</span>}
                        </div>
                        <p className="font-medium text-[15px]">{c.text}</p>
                        <p className="mt-2 text-xs text-neutral-400">证伪条件：{c.falsifiable}</p>
                      </div>
                      <div className="shrink-0 text-right">
                        <div className="num-display text-[#B42318] dark:text-red-300">
                          {c.evidence.filter((e) => e.kind === 'challenge').length}
                        </div>
                        <div className="text-xs text-neutral-400 mt-1">挑战证据</div>
                      </div>
                    </div>
                    {isExpanded && (
                      <div className="mt-5 pt-5 hairline-t">
                        <div className="mb-4">
                          <div className="locator mb-2 uppercase">约定细则</div>
                          <div className="grid grid-cols-2 md:grid-cols-3 gap-3 text-[13px]">
                            {Object.entries(c.contract).map(([key, val]) => (
                              <div key={key}>
                                <span className="text-neutral-400">{key}</span>
                                <p className="text-neutral-700 dark:text-white/90 mt-0.5">{val || '—'}</p>
                              </div>
                            ))}
                          </div>
                        </div>
                        <div className="mb-4">
                          <div className="locator mb-1 uppercase">证伪条件</div>
                          <p className="text-[13px] text-neutral-600 dark:text-white/90">{c.falsifiable}</p>
                        </div>
                        <div>
                          <div className="locator mb-2 uppercase">证据链</div>
                          {c.evidence.length === 0 && (
                            <p className="text-[13px] text-neutral-300">—</p>
                          )}
                          <ul className="space-y-2">
                            {c.evidence.map((e) => {
                              const pp = findPaper(e.paperId);
                              return (
                                <li key={e.paperId} className="text-[13px] text-neutral-600 dark:text-white/90 leading-relaxed">
                                  <span className="font-mono text-neutral-400">
                                    {e.kind === 'challenge' ? '⚠' : e.kind === 'support' ? '✓' : '○'}
                                  </span>{' '}
                                  {e.note}
                                  {pp && (
                                    <span className="locator block mt-0.5">
                                      {sourceLabel(pp)} · {pp.title.slice(0, 48)}...
                                    </span>
                                  )}
                                </li>
                              );
                            })}
                          </ul>
                        </div>
                      </div>
                    )}
                  </article>
                </Reveal>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
