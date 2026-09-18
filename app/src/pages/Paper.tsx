import { useState } from 'react';
import { useNavigate } from 'react-router';
import { claimStatusMeta, type Claim, type VersionRec } from '../data/mock';
import { Reveal, SectionHead, Stat, Tabs, Quote, Empty } from '../components/chrome';
import { useProject } from '../contexts/ProjectContext';
import {
  useApi,
  getProfile,
  analyzeProfile,
  getCompetitors,
  addCompetitor,
  removeCompetitor,
  confirmClaim,
  rejectClaim,
  splitClaim,
  decomposeClaim,
  verifyClaimSemantics,
  editClaim,
  trackClaimChanges,
  uploadManuscript,
  reExtractCurrentClaims,
  getConsistency,
  type AtomicClaim,
  type ClaimChangesResult,
  type ConsistencyFinding,
} from '../api';
import ErrorRetry from '../components/ErrorRetry';

// ---------- local types ----------

interface CompetitorItem {
  id: string;
  team: string;
  aliases: string[];
}

// ---------- constants ----------

const CONTRACT_LABELS: [string, keyof Claim['contract']][] = [
  ['任务', 'task'],
  ['数据集', 'dataset'],
  ['数据划分', 'split'],
  ['指标', 'metric'],
  ['对比基线', 'baseline'],
  ['适用范围', 'scope'],
];

// ---------- Paper page ----------

export default function Paper() {
  const navigate = useNavigate();
  const {
    project,
    loading: projectLoading,
    error: projectError,
    projectId,
    refreshProject,
  } = useProject();
  const [tab, setTab] = useState(0);

  // ---- data hooks ----

  const {
    data: profile,
    loading: profileLoading,
    error: profileError,
    refetch: refetchProfile,
  } = useApi(() => getProfile(projectId), [projectId]);

  const {
    data: competitors,
    loading: competitorsLoading,
    error: competitorsError,
    refetch: refetchCompetitors,
  } = useApi(
    () => getCompetitors(projectId),
    [projectId],
  );

  // Layer 2 loads a model and spends tokens, so it never runs on page load.
  // The user turns it on, and only then does the request include it.
  const [semanticOn, setSemanticOn] = useState(false);
  const {
    data: consistency,
    loading: consistencyLoading,
    error: consistencyError,
    refetch: refetchConsistency,
  } = useApi(() => getConsistency(projectId, { semantic: semanticOn }), [projectId, semanticOn]);

  // ---- local state ----

  const [confirm, setConfirm] = useState<Record<string, 'yes' | 'no'>>({});
  const [synced, setSynced] = useState(false);
  const [team, setTeam] = useState('');
  const [alias, setAlias] = useState('');
  const [actionError, setActionError] = useState<string | null>(null);
  const [editingClaim, setEditingClaim] = useState<string | null>(null);
  const [editDraft, setEditDraft] = useState<{
    statement: string;
    falsifiable_condition: string;
    contract: Claim['contract'];
  } | null>(null);
  const [editError, setEditError] = useState<string | null>(null);
  const [savingEdit, setSavingEdit] = useState(false);
  const [previousVersionId, setPreviousVersionId] = useState('');
  const [currentVersionId, setCurrentVersionId] = useState('');
  const [claimChanges, setClaimChanges] = useState<ClaimChangesResult | null>(null);
  const [trackingChanges, setTrackingChanges] = useState(false);
  const [trackError, setTrackError] = useState<string | null>(null);

  // ---- derived values ----

  const confirmedCount = project
    ? project.claims.filter(
        (c) =>
          (confirm[c.id] ??
            (c.confirmed === 'yes'
              ? 'yes'
              : c.status === 'revalidate'
                ? 'no'
                : c.confirmed === 'pending'
                  ? undefined
                  : 'no')) === 'yes',
      ).length
    : 0;

  // ---- action handlers ----

  const handleConfirm = async (revId: string) => {
    try {
      setActionError(null);
      const updated = await confirmClaim(projectId, revId);
      setConfirm((m) => ({ ...m, [revId]: 'yes' }));
      if (updated.confirmed === 'yes') {
        setConfirm((m) => ({ ...m, [revId]: 'yes' }));
      }
      await refreshProject();
    } catch (e) {
      setActionError(e instanceof Error ? e.message : '确认失败');
    }
  };

  const beginEdit = (claim: Claim) => {
    setEditingClaim(claim.id);
    setEditError(null);
    setEditDraft({
      statement: claim.text,
      falsifiable_condition: claim.falsifiable,
      contract: { ...claim.contract },
    });
  };

  const cancelEdit = () => {
    setEditingClaim(null);
    setEditDraft(null);
    setEditError(null);
  };

  const handleSaveEdit = async (revId: string) => {
    if (!editDraft) return;
    if (!editDraft.statement.trim()) {
      setEditError('主张内容不能为空');
      return;
    }
    setSavingEdit(true);
    setEditError(null);
    try {
      await editClaim(projectId, revId, {
        statement: editDraft.statement.trim(),
        centrality: 'major',
        contract: editDraft.contract,
        falsifiable_condition: editDraft.falsifiable_condition.trim(),
      });
      await refreshProject();
      cancelEdit();
    } catch (e) {
      setEditError(e instanceof Error ? e.message : '保存编辑失败');
    } finally {
      setSavingEdit(false);
    }
  };

  const handleTrackChanges = async () => {
    const versions = project?.versions ?? [];
    const previous = previousVersionId || versions[1]?.id || '';
    const current = currentVersionId || versions[0]?.id || '';
    if (!previous || !current) {
      setTrackError('当前版本数据缺少版本 ID，无法调用 Claim 对比接口');
      return;
    }
    if (previous === current) {
      setTrackError('请选择两个不同版本');
      return;
    }
    setTrackingChanges(true);
    setTrackError(null);
    try {
      const result = await trackClaimChanges(projectId, previous, current);
      setClaimChanges(result);
    } catch (e) {
      setTrackError(e instanceof Error ? e.message : '版本对比失败');
    } finally {
      setTrackingChanges(false);
    }
  };

  const handleReject = async (revId: string) => {
    try {
      setActionError(null);
      await rejectClaim(projectId, revId);
      setConfirm((m) => ({ ...m, [revId]: 'no' }));
      await refreshProject();
    } catch (e) {
      setActionError(e instanceof Error ? e.message : '拒绝失败');
    }
  };

  // ---- split / decompose / verify (P2: wire the dormant claim tools) ----

  const [splitting, setSplitting] = useState<Record<string, boolean>>({});
  const [splitText, setSplitText] = useState<Record<string, string>>({});
  const [decompose, setDecompose] = useState<Record<string, AtomicClaim[] | null>>({});
  const [decomposing, setDecomposing] = useState<Record<string, boolean>>({});
  const [verify, setVerify] = useState<Record<string, { faithful: boolean | null; issues: string[]; verified: string } | null>>({});
  const [verifying, setVerifying] = useState<Record<string, boolean>>({});

  const handleSplit = async (revId: string) => {
    const text = splitText[revId] ?? '';
    const statements = text.split('\n').map((s) => s.trim()).filter(Boolean);
    if (statements.length < 2) {
      setActionError('拆分至少需要两句话，每行一句');
      return;
    }
    setSplitting((m) => ({ ...m, [revId]: true }));
    setActionError(null);
    try {
      await splitClaim(projectId, revId, statements);
      setSplitText((m) => ({ ...m, [revId]: '' }));
      await refreshProject();
    } catch (e) {
      setActionError(e instanceof Error ? e.message : '拆分失败');
    } finally {
      setSplitting((m) => ({ ...m, [revId]: false }));
    }
  };

  const handleDecompose = async (revId: string) => {
    setDecomposing((m) => ({ ...m, [revId]: true }));
    setActionError(null);
    try {
      const result = await decomposeClaim(projectId, revId);
      setDecompose((m) => ({ ...m, [revId]: result.atomic_claims }));
    } catch (e) {
      setActionError(e instanceof Error ? e.message : '原子化失败');
    } finally {
      setDecomposing((m) => ({ ...m, [revId]: false }));
    }
  };

  const handleVerify = async (revId: string) => {
    setVerifying((m) => ({ ...m, [revId]: true }));
    setActionError(null);
    try {
      const result = await verifyClaimSemantics(projectId, revId);
      setVerify((m) => ({
        ...m,
        [revId]: {
          faithful: typeof result.faithful === 'boolean' ? result.faithful : null,
          issues: Array.isArray(result.issues) ? result.issues : [],
          verified: typeof result.verified === 'string' ? result.verified : 'unknown',
        },
      }));
    } catch (e) {
      setActionError(e instanceof Error ? e.message : '语义验证失败');
    } finally {
      setVerifying((m) => ({ ...m, [revId]: false }));
    }
  };

  const handleUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    try {
      setActionError(null);
      await uploadManuscript(projectId, file);
      await refreshProject();
      setSynced(true);
    } catch (err) {
      setActionError(err instanceof Error ? err.message : '上传失败');
    }
  };

  const [reExtracting, setReExtracting] = useState(false);

  const handleReExtract = async () => {
    if (!project || project.claims.some((c) => c.confirmed === 'yes' || c.confirmed === 'history')) return;
    setReExtracting(true);
    setActionError(null);
    try {
      await reExtractCurrentClaims(projectId);
      await refreshProject();
      setSynced(true);
    } catch (err) {
      setActionError(err instanceof Error ? err.message : '重新提取失败');
    } finally {
      setReExtracting(false);
    }
  };

  const handleAddCompetitor = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!team.trim()) return;
    try {
      setActionError(null);
      await addCompetitor(
        projectId,
        team.trim(),
        alias.split(/[,，]/).map((s) => s.trim()).filter(Boolean),
      );
      setTeam('');
      setAlias('');
      refetchCompetitors();
    } catch (err) {
      setActionError(err instanceof Error ? err.message : '添加监控失败');
    }
  };

  const handleRemoveCompetitor = async (item: CompetitorItem) => {
    if (!item.id) {
      setActionError('缺少监控条目 ID，无法移除');
      return;
    }
    try {
      setActionError(null);
      await removeCompetitor(projectId, item.id);
      refetchCompetitors();
    } catch (err) {
      setActionError(err instanceof Error ? err.message : '移除监控失败');
    }
  };

  const handleReanalyze = async () => {
    try {
      setActionError(null);
      await analyzeProfile(projectId);
      refetchProfile();
    } catch (err) {
      setActionError(err instanceof Error ? err.message : '重新分析失败');
    }
  };

  // ---- loading / error states ----

  if (projectLoading) {
    return (
      <div className="py-20 text-center">
        <div className="text-3xl font-light text-neutral-300 select-none dark:text-white/60">…</div>
        <div className="mt-3 text-sm text-neutral-400 dark:text-white/75">加载中</div>
      </div>
    );
  }

  if (projectError || !project) {
    return (
      <div className="py-20 text-center">
        <div className="mx-auto max-w-md rounded-lg border border-red-200 bg-red-50 px-3.5 py-2.5 text-[12.5px] text-red-700 dark:border-red-500/25 dark:bg-red-500/10 dark:text-red-300">
          <ErrorRetry message={projectError?.message ?? '无法加载项目数据'} onRetry={() => void refreshProject()} />
        </div>
      </div>
    );
  }

  // ---- render ----

  return (
    <div>
      <Reveal>
        <SectionHead
          kicker="Paper · Manuscript"
          title="论文 Claim"
          right={<button className="btn-secondary" onClick={() => navigate(`/cases/${projectId}/graph`)}>查看演化图谱</button>}
        />
      </Reveal>

      {/* action-level error banner */}
      {actionError && (
        <div className="mb-6 flex items-start justify-between gap-3 rounded-lg border border-red-200 bg-red-50 px-3.5 py-2.5 text-[12.5px] text-red-700 dark:border-red-500/25 dark:bg-red-500/10 dark:text-red-300">
          <span>{actionError}</span>
          <button
            className="shrink-0 underline underline-offset-2 text-neutral-500 hover:text-neutral-700 dark:text-white/80 dark:hover:text-white"
            onClick={() => setActionError(null)}
          >
            关闭
          </button>
        </div>
      )}

      <Reveal delay={60}>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-6 pb-8 hairline-b">
          <Stat
            value={<span className="font-mono text-2xl">{project.file}</span>}
            label="当前文稿"
          />
          <Stat value={project.version} label="版本" tone="teal" />
          <Stat value={project.claimsTotal} label="当前 Claim" />
          <Stat value={confirmedCount} label="已确认" tone="teal" />
        </div>
      </Reveal>

      {confirmedCount > 0 && (
        <Reveal delay={90}>
          <div className="card mt-4 flex flex-col gap-3 p-4 sm:flex-row sm:items-center sm:justify-between">
            <div className="text-[13px] leading-relaxed text-neutral-600 dark:text-white/90">
              已有 <b>{confirmedCount}</b> 条已确认 Claim，可以启动文献雷达扫描和影响复核。
            </div>
            <button
              className="btn-primary shrink-0"
              onClick={() => navigate(`/cases/${projectId}/radar`)}
            >
              去文献雷达
            </button>
          </div>
        </Reveal>
      )}

      <div className="mt-8">
        <Tabs
          tabs={['文稿与版本', '项目主张', 'AI 全文画像', '前后一致性', '竞争监控']}
          active={tab}
          onChange={setTab}
        />

        {/* ================================================================ */}
        {/* Tab 0: 文稿与版本                                                   */}
        {/* ================================================================ */}
        {tab === 0 && (
          <div>
            <Reveal>
              <div className="card flex flex-col justify-between gap-4 p-6 md:flex-row md:items-center">
                <div>
                  <div className="text-[15px] font-medium">同步新版本</div>
                  <p className="mt-1 text-[12.5px] text-neutral-500 dark:text-white/80">
                    上传后自动重新提取 Claim 并与当前账本比对
                  </p>
                </div>
                <div className="flex items-center gap-3">
                  <label className="btn-primary cursor-pointer">
                    选择文件
                    <input
                      type="file"
                      accept=".tex,.md,.pdf"
                      className="hidden"
                      onChange={handleUpload}
                    />
                  </label>
                  {project.claims.length > 0 &&
                    !project.claims.some((c) => c.confirmed === 'yes' || c.confirmed === 'history') && (
                      <button className="btn-ghost" disabled={reExtracting} onClick={handleReExtract}>
                        {reExtracting ? '重新提取中…' : '重新提取 Claim'}
                      </button>
                    )}
                  {synced && (
                    <span className="badge-green badge-dot">
                      已同步 · 无新增 Claim
                    </span>
                  )}
                </div>
              </div>
            </Reveal>
            <Reveal delay={80}>
              <div className="card mt-5 overflow-x-auto">
                <table className="table-base min-w-[560px]">
                  <thead>
                    <tr>
                      <th>版本</th>
                      <th>日期</th>
                      <th>文件</th>
                      <th>Claim 数</th>
                      <th>备注</th>
                    </tr>
                  </thead>
                  <tbody>
                    {project.versions.map((v: VersionRec) => (
                      <tr key={v.v}>
                        <td className="font-mono text-[12px]">{v.v}</td>
                        <td className="locator">{v.date}</td>
                        <td className="font-mono text-[12px] text-neutral-500 dark:text-white/85">
                          {v.file}
                        </td>
                        <td className="tabular">{v.claims}</td>
                        <td className="text-neutral-500 dark:text-white/85">{v.note}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Reveal>
            <Reveal delay={120}>
              <section className="card mt-5 p-6">
                <div className="flex flex-col justify-between gap-3 md:flex-row md:items-start">
                  <div>
                    <div className="text-[15px] font-medium">版本对比</div>
                    <p className="mt-1 text-[12.5px] text-neutral-500 dark:text-white/80">
                      对比两个版本中的 Claim 生命周期变化（新增、修改、删除与未变化）。
                    </p>
                  </div>
                  <span className="locator">Claim lifecycle diff</span>
                </div>
                <div className="mt-5 grid gap-3 md:grid-cols-[1fr_1fr_auto] md:items-end">
                  <label className="text-xs text-neutral-500 dark:text-white/80">
                    之前版本
                    <select
                      className="input mt-1"
                      value={previousVersionId || project.versions[1]?.id || ''}
                      onChange={(e) => setPreviousVersionId(e.target.value)}
                    >
                      <option value="">选择版本</option>
                      {project.versions.map((v) => (
                        <option key={v.id ?? v.v} value={v.id ?? ''}>{v.v} · {v.file}</option>
                      ))}
                    </select>
                  </label>
                  <label className="text-xs text-neutral-500 dark:text-white/80">
                    当前版本
                    <select
                      className="input mt-1"
                      value={currentVersionId || project.versions[0]?.id || ''}
                      onChange={(e) => setCurrentVersionId(e.target.value)}
                    >
                      <option value="">选择版本</option>
                      {project.versions.map((v) => (
                        <option key={v.id ?? v.v} value={v.id ?? ''}>{v.v} · {v.file}</option>
                      ))}
                    </select>
                  </label>
                  <button className="btn-primary" disabled={trackingChanges || project.versions.length < 2} onClick={() => void handleTrackChanges()}>
                    {trackingChanges ? '对比中…' : '对比 Claim 变化'}
                  </button>
                </div>
                {trackError && <div className="mt-3 text-xs text-red-700 dark:text-red-300">{trackError}</div>}
                {claimChanges && !claimChanges.error && (
                  <div className="mt-5 rounded-lg border border-hairline p-4 dark:border-white/10">
                    <div className="grid grid-cols-2 gap-3 text-center sm:grid-cols-5">
                      {[
                        ['新增', claimChanges.new],
                        ['修改', claimChanges.modified],
                        ['删除', claimChanges.deleted],
                        ['未变化', claimChanges.unchanged],
                        ['当前总数', claimChanges.total_current],
                      ].map(([label, value]) => (
                        <div key={label as string}>
                          <div className="font-mono text-lg">{value}</div>
                          <div className="locator">{label}</div>
                        </div>
                      ))}
                    </div>
                    {claimChanges.modified_details.length > 0 && (
                      <div className="mt-5 overflow-x-auto">
                        <table className="table-base min-w-[520px]">
                          <thead><tr><th>Claim</th><th>变化</th><th>变化比例</th></tr></thead>
                          <tbody>{claimChanges.modified_details.map((item) => (
                            <tr key={item.claim_id}>
                              <td className="font-mono text-xs">{item.stable_key || item.claim_id}</td>
                              <td>{item.change_type}</td>
                              <td>{Math.round(item.change_ratio * 100)}%</td>
                            </tr>
                          ))}</tbody>
                        </table>
                      </div>
                    )}
                  </div>
                )}
              </section>
            </Reveal>
          </div>
        )}

        {/* ================================================================ */}
        {/* Tab 1: 项目主张                                                     */}
        {/* ================================================================ */}
        {tab === 1 && (
          <div className="grid gap-4">
            {project.claims.length === 0 && (
              <Reveal>
                <div className="card p-8 text-center">
                  <p className="text-sm text-neutral-600 dark:text-white/90">还没有提取出任何 Claim</p>
                  <p className="mt-2 text-xs leading-relaxed text-neutral-400 dark:text-white/75">
                    系统需要 LLM 才能从文稿中提取高质量 Claim；未配置模型时会退化为规则提取，可能结果偏少。
                    请到「设置」页配置模型（或检查本机 Ollama），然后在「文稿与版本」里重新上传文稿触发提取。
                  </p>
                </div>
              </Reveal>
            )}
            {project.claims.map((c: Claim, i: number) => {
              const st =
                confirm[c.id] ??
                (c.confirmed === 'yes'
                  ? 'yes'
                  : c.status === 'revalidate'
                    ? 'no' // rejected claims stay rejected across refreshes
                    : c.confirmed === 'pending'
                      ? undefined
                      : 'no');
              return (
                <Reveal key={c.id} delay={i * 50}>
                  <article className="card p-6">
                    <div className="mb-3 flex flex-wrap items-center gap-3">
                      <span
                        className={
                          st === 'yes' ? 'badge-green' : st === 'no' ? 'badge-gray' : 'badge-orange'
                        }
                      >
                        {st === 'yes'
                          ? '✓ 已确认'
                          : st === 'no'
                            ? '↺ 历史'
                            : '○ 待确认'}
                      </span>
                      <span className={claimStatusMeta[c.status].cls}>
                        {claimStatusMeta[c.status].label}
                      </span>
                      <span className="locator ml-auto">{c.id}</span>
                    </div>
                    <p className="text-[15px] font-medium">{c.text}</p>
                    <div className="mt-4">
                      <Quote loc={c.loc}>{c.quote}</Quote>
                    </div>
                    <div className="mt-5 overflow-x-auto">
                      <table className="table-base min-w-[520px]">
                        <tbody>
                          {CONTRACT_LABELS.map(([label, key]) => (
                            <tr key={key}>
                              <td className="w-24 whitespace-nowrap !py-2 text-xs text-neutral-400 dark:text-white/75">
                                {label}
                              </td>
                              <td className="!py-2 text-[13px] text-neutral-600 dark:text-white/90">
                                {c.contract[key]}
                              </td>
                            </tr>
                          ))}
                          <tr>
                            <td className="!py-2 text-xs text-neutral-400 dark:text-white/75">
                              可证伪条件
                            </td>
                            <td className="!py-2 text-[13px] text-[#B42318]/80 dark:text-red-300">
                              {c.falsifiable}
                            </td>
                          </tr>
                        </tbody>
                      </table>
                    </div>
                    <div className="mt-4 flex flex-wrap gap-2 pt-4 hairline-t">
                      {c.status === 'history' ? (
                        <span className="text-xs text-neutral-400 dark:text-white/75">
                          该主张已被后续修订替代
                        </span>
                      ) : (
                        <>
                          <button className="btn-ghost" onClick={() => beginEdit(c)}>
                            编辑
                          </button>
                          {st !== 'yes' && (
                            <button
                              className="btn-primary"
                              onClick={() => handleConfirm(c.id)}
                            >
                              确认
                            </button>
                          )}
                          {st !== 'no' && (
                            <button
                              className="btn-quiet"
                              onClick={() => handleReject(c.id)}
                            >
                              拒绝
                            </button>
                          )}
                          {!splitting[c.id] && splitText[c.id] == null && (
                            <button
                              className="btn-ghost"
                              onClick={() => setSplitText((m) => ({ ...m, [c.id]: '' }))}
                            >
                              拆分
                            </button>
                          )}
                          <button
                            className="btn-ghost"
                            disabled={decomposing[c.id]}
                            onClick={() => handleDecompose(c.id)}
                          >
                            {decomposing[c.id] ? '分解中…' : '原子化'}
                          </button>
                          <button
                            className="btn-ghost"
                            disabled={verifying[c.id]}
                            onClick={() => handleVerify(c.id)}
                          >
                            {verifying[c.id] ? '验证中…' : '语义验证'}
                          </button>
                        </>
                      )}
                    </div>

                    {editingClaim === c.id && editDraft && (
                      <div className="mt-4 rounded-lg border border-hairline bg-neutral-50 p-4 dark:border-white/10 dark:bg-white/5">
                        <div className="mb-3 text-xs font-medium text-neutral-500 dark:text-white/85">编辑 Claim</div>
                        <label className="block text-xs text-neutral-500 dark:text-white/80">
                          主张
                          <textarea className="input mt-1 min-h-20 resize-y" value={editDraft.statement} onChange={(e) => setEditDraft({ ...editDraft, statement: e.target.value })} />
                        </label>
                        <label className="mt-3 block text-xs text-neutral-500 dark:text-white/80">
                          可证伪条件
                          <textarea className="input mt-1 min-h-16 resize-y" value={editDraft.falsifiable_condition} onChange={(e) => setEditDraft({ ...editDraft, falsifiable_condition: e.target.value })} />
                        </label>
                        <div className="mt-3 grid gap-3 sm:grid-cols-2">
                          {CONTRACT_LABELS.map(([label, key]) => (
                            <label key={key} className="text-xs text-neutral-500 dark:text-white/80">
                              {label}
                              <input className="input mt-1" value={editDraft.contract[key]} onChange={(e) => setEditDraft({ ...editDraft, contract: { ...editDraft.contract, [key]: e.target.value } })} />
                            </label>
                          ))}
                        </div>
                        {editError && <div className="mt-3 rounded border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700 dark:border-red-500/25 dark:bg-red-500/10 dark:text-red-300">{editError}</div>}
                        <div className="mt-4 flex gap-2">
                          <button className="btn-primary text-xs" disabled={savingEdit} onClick={() => void handleSaveEdit(c.id)}>
                            {savingEdit ? '保存中…' : '保存'}
                          </button>
                          <button className="btn-ghost text-xs" disabled={savingEdit} onClick={cancelEdit}>取消</button>
                        </div>
                      </div>
                    )}

                    {/* split inline form */}
                    {splitText[c.id] != null && (
                      <div className="mt-4 rounded-lg border border-hairline bg-neutral-50 p-4 dark:border-white/10 dark:bg-white/5">
                        <div className="mb-2 text-xs text-neutral-500 dark:text-white/85">
                          每行一句，拆分为多个独立主张
                        </div>
                        <textarea
                          className="input h-24 resize-none"
                          value={splitText[c.id] ?? ''}
                          onChange={(e) => setSplitText((m) => ({ ...m, [c.id]: e.target.value }))}
                          placeholder={'第一句主张\n第二句主张'}
                        />
                        <div className="mt-2 flex gap-2">
                          <button
                            className="btn-primary text-xs"
                            disabled={splitting[c.id]}
                            onClick={() => void handleSplit(c.id)}
                          >
                            {splitting[c.id] ? '拆分中…' : '确认拆分'}
                          </button>
                          <button
                            className="btn-ghost text-xs"
                            onClick={() => setSplitText((m) => {
                              const next = { ...m };
                              delete next[c.id];
                              return next;
                            })}
                          >
                            取消
                          </button>
                        </div>
                      </div>
                    )}

                    {/* decompose results */}
                    {decompose[c.id] && (
                      <div className="mt-4 rounded-lg border border-hairline p-4 dark:border-white/10">
                        <div className="kicker mb-3">原子主张</div>
                        {decompose[c.id]!.map((atom, idx) => (
                          <div key={idx} className="py-2 hairline-b last:border-none">
                            <p className="text-sm">{atom.statement}</p>
                            <Quote loc={atom.source_locator}>{atom.source_quote}</Quote>
                            {atom.dependencies.length > 0 && (
                              <div className="locator mt-1">依赖: {atom.dependencies.join(', ')}</div>
                            )}
                          </div>
                        ))}
                      </div>
                    )}

                    {/* semantic verification result */}
                    {verify[c.id] && (
                      <div className="mt-4 rounded-lg border border-hairline p-4 text-sm dark:border-white/10">
                        <div className="mb-1 flex items-center gap-2">
                          <span className={`badge-dot ${verify[c.id]!.faithful ? 'badge-green' : 'badge-red'}`}>
                            {verify[c.id]!.faithful === null
                              ? '未验证'
                              : verify[c.id]!.faithful
                                ? '语句与引用一致'
                                : '存在偏差'}
                          </span>
                          <span className="locator">{verify[c.id]!.verified}</span>
                        </div>
                        {verify[c.id]!.issues.length > 0 && (
                          <ul className="mt-2 list-disc pl-4 text-xs text-neutral-500 dark:text-white/85">
                            {verify[c.id]!.issues.map((issue, idx) => (
                              <li key={idx}>{issue}</li>
                            ))}
                          </ul>
                        )}
                      </div>
                    )}
                  </article>
                </Reveal>
              );
            })}
          </div>
        )}

        {/* ================================================================ */}
        {/* Tab 2: AI 全文画像                                                  */}
        {/* ================================================================ */}
        {tab === 2 && (
          <div className="grid gap-4">
            {/* Full-text profile */}
            <Reveal>
              <section className="card p-7">
                <SectionHead
                  kicker="Full-text Profile"
                  title="全文画像"
                  right={
                    <button className="btn-ghost" onClick={handleReanalyze}>
                      重新分析
                    </button>
                  }
                />
                {profileLoading && (
                  <div className="py-8 text-center text-sm text-neutral-400 dark:text-white/75">
                    加载中…
                  </div>
                )}
                {profileError && (
                  <div className="rounded-lg border border-red-200 bg-red-50 px-3.5 py-2.5 text-[12.5px] text-red-700 dark:border-red-500/25 dark:bg-red-500/10 dark:text-red-300">
                    <ErrorRetry message={profileError.message} onRetry={() => void refetchProfile()} />
                  </div>
                )}
                {!profileLoading && !profileError && !profile && (
                  <Empty text="AI 全文画像尚未分析" />
                )}
                {profile && (
                  <dl className="grid gap-x-10 gap-y-6 md:grid-cols-2">
                    <div>
                      <dt className="mb-1.5 text-xs text-neutral-400 dark:text-white/75">研究问题</dt>
                      <dd className="text-[15px] font-medium leading-snug">
                        {profile.question}
                      </dd>
                    </div>
                    <div>
                      <dt className="mb-1.5 text-xs text-neutral-400 dark:text-white/75">核心论点</dt>
                      <dd className="text-sm leading-relaxed text-neutral-600 dark:text-white/90">
                        {profile.thesis}
                      </dd>
                    </div>
                    <div>
                      <dt className="mb-1.5 text-xs text-neutral-400 dark:text-white/75">主要贡献</dt>
                      <dd>
                        <ul className="space-y-1 text-sm text-neutral-600 dark:text-white/90">
                          {profile.contributions.map((x) => (
                            <li key={x}>· {x}</li>
                          ))}
                        </ul>
                      </dd>
                    </div>
                    <div>
                      <dt className="mb-1.5 text-xs text-neutral-400 dark:text-white/75">关键发现</dt>
                      <dd>
                        <ul className="space-y-1 text-sm text-neutral-600 dark:text-white/90">
                          {profile.findings.map((x) => (
                            <li key={x}>· {x}</li>
                          ))}
                        </ul>
                      </dd>
                    </div>
                    <div>
                      <dt className="mb-1.5 text-xs text-neutral-400 dark:text-white/75">局限</dt>
                      <dd>
                        <ul className="space-y-1 text-sm text-neutral-500 dark:text-white/85">
                          {profile.limits.map((x) => (
                            <li key={x}>· {x}</li>
                          ))}
                        </ul>
                      </dd>
                    </div>
                    <div>
                      <dt className="mb-1.5 text-xs text-neutral-400 dark:text-white/75">
                        每周监控主题
                      </dt>
                      <dd className="flex flex-wrap gap-2">
                        {project.topics.map((t: string) => (
                          <span key={t} className="chip font-mono !text-[11px]">
                            {t}
                          </span>
                        ))}
                      </dd>
                    </div>
                  </dl>
                )}
              </section>
            </Reveal>

            {/* Per-claim profile */}
            <Reveal delay={80}>
              <section className="card p-7">
                <SectionHead kicker="Per-Claim Profile" title="逐 Claim 画像" />
                <div className="grid gap-4 md:grid-cols-2">
                  {project.claims.map((c: Claim) => (
                    <div key={c.id} className="card card-hover p-4">
                      <div className="mb-2 flex items-center gap-2">
                        <span className="font-mono text-[11px] text-neutral-400 dark:text-white/75">
                          {c.id}
                        </span>
                        <span className={claimStatusMeta[c.status].cls}>
                          {claimStatusMeta[c.status].label}
                        </span>
                      </div>
                      <p className="text-[13px] leading-relaxed text-neutral-600 dark:text-white/90">
                        {c.text}
                      </p>
                      <p className="locator mt-2">
                        证据 {c.evidence.length} ·{' '}
                        {c.radarWatch ? 'Radar 关注中' : '常规监控'}
                      </p>
                    </div>
                  ))}
                </div>
              </section>
            </Reveal>
          </div>
        )}

        {/* ================================================================ */}
        {/* Tab 3: 竞争监控                                                     */}
        {/* ================================================================ */}
        {/* ================================================================ */}
        {/* Tab 3: 前后一致性                                                   */}
        {/* ================================================================ */}
        {tab === 3 && (
          <div className="grid gap-4">
            <Reveal>
              <SectionHead
                kicker={
                  semanticOn
                    ? '第一层数值检查 + 第二层语义检查（调用模型）'
                    : '第一层数值检查 · 确定性 · 不调用模型'
                }
                title="文稿内部前后一致性"
                right={
                  <div className="flex items-center gap-2">
                    <button
                      className="btn-quiet"
                      onClick={() => setSemanticOn((on) => !on)}
                      title="语义检查会调用大模型，耗时更长并消耗额度"
                    >
                      {semanticOn ? '关闭语义检查' : '开启语义检查'}
                    </button>
                    <button className="btn-quiet" onClick={() => void refetchConsistency()}>
                      重新检查
                    </button>
                  </div>
                }
              />
            </Reveal>

            {consistencyLoading && (
              <div className="py-8 text-center text-sm text-neutral-400 dark:text-white/75">
                检查中…
              </div>
            )}

            {consistencyError && (
              <div className="rounded-lg border border-red-200 bg-red-50 px-3.5 py-2.5 text-[12.5px] text-red-700 dark:border-red-500/25 dark:bg-red-500/10 dark:text-red-300">
                <ErrorRetry
                  message={consistencyError.message}
                  onRetry={() => void refetchConsistency()}
                />
              </div>
            )}

            {!consistencyLoading && !consistencyError && consistency && (
              <>
                <Reveal>
                  <div className="card grid grid-cols-3 gap-4 p-6">
                    <Stat
                      value={consistency.finding_count}
                      label="疑似不一致"
                      tone={consistency.finding_count > 0 ? 'red' : 'teal'}
                    />
                    <Stat
                      value={consistency.manuscript.version_no}
                      label="手稿版本"
                    />
                    <Stat
                      value={consistency.manuscript.chars.toLocaleString()}
                      label="字符数"
                    />
                  </div>
                </Reveal>

                {/* What the check looked at and what it discarded. A reviewer
                    deciding whether to trust "0 findings" needs to know
                    whether the model ran at all. */}
                {consistency.semantic && (
                  <Reveal>
                    <div className="rounded-lg border border-neutral-200 px-3.5 py-2.5 text-[12px] leading-relaxed text-neutral-500 dark:border-white/10 dark:text-white/70">
                      {consistency.semantic.status === 'nli_unavailable' ? (
                        <>语义检查未运行：本地 NLI 模型不可用，已跳过，未向大模型发送任何内容。上方结果仅来自第一层数值检查。</>
                      ) : consistency.semantic.status === 'error' ? (
                        <>语义检查出错，已跳过：{consistency.semantic.detail}。上方结果仅来自第一层数值检查。</>
                      ) : (
                        <>
                          语义检查：{consistency.semantic.sentences} 个待查句 ·{' '}
                          {consistency.semantic.pairs_generated} 组句对 · NLI 粗筛后送模型{' '}
                          {consistency.semantic.pairs_judged} 组
                          {consistency.semantic.rejected_unquoted > 0 && (
                            <>
                              {' '}· 因无法逐字引用原文被丢弃{' '}
                              {consistency.semantic.rejected_unquoted} 条
                            </>
                          )}
                          {consistency.semantic.rejected_low_confidence > 0 && (
                            <> · 因置信度过低被丢弃 {consistency.semantic.rejected_low_confidence} 条</>
                          )}
                          {consistency.semantic.truncated && <> · 已截断至上限</>}
                        </>
                      )}
                    </div>
                  </Reveal>
                )}

                {consistency.findings.length === 0 && (
                  <Empty
                    text={
                      semanticOn
                        ? '没有发现前后矛盾。数值层覆盖同一实验条件下同一指标的冲突，语义层覆盖主张与证据、前后论述的冲突。'
                        : '没有发现前后矛盾的数值。这是确定性检查的结果，只覆盖同一实验条件下同一指标的数值冲突。'
                    }
                  />
                )}

                {consistency.findings.map((f: ConsistencyFinding, i: number) => (
                  <Reveal key={`${f.metric}-${i}`} delay={i * 60}>
                    <div className="card p-6">
                      <div className="flex flex-wrap items-center gap-2">
                        <span
                          className={
                            f.severity === 'critical'
                              ? 'chip !border-red-300 !bg-red-50 !text-red-700 dark:!border-red-500/30 dark:!bg-red-500/10 dark:!text-red-300'
                              : 'chip !border-orange-300 !bg-orange-50 !text-orange-700 dark:!border-orange-500/30 dark:!bg-orange-500/10 dark:!text-orange-300'
                          }
                        >
                          {f.severity === 'critical' ? '严重' : '待复核'}
                        </span>
                        {f.metric && (
                          <span className="chip font-mono !text-[11px]">{f.metric}</span>
                        )}
                        <span className="chip !text-[11px]">
                          {f.occurrences[0]?.source === 'semantic' ? '语义' : '数值'}
                        </span>
                        {typeof f.confidence === 'number' && (
                          <span className="chip font-mono !text-[11px]">
                            置信度 {f.confidence.toFixed(2)}
                          </span>
                        )}
                        <span className="chip !text-[11px]">待人工确认</span>
                      </div>

                      <p className="mt-3 text-[13.5px] leading-relaxed">{f.summary}</p>

                      <div className="mt-4 grid gap-2.5">
                        {f.occurrences.map((o, j) => (
                          <Quote
                            key={j}
                            loc={`${o.section} · 第 ${o.start_offset} 字符${
                              o.source === 'table' ? ' · 表格' : ''
                            }`}
                            label={o.value === null ? o.scope : `${o.value}${o.unit}`}
                          >
                            {o.quote}
                          </Quote>
                        ))}
                      </div>
                    </div>
                  </Reveal>
                ))}
              </>
            )}
          </div>
        )}

        {tab === 4 && (
          <div className="grid gap-4">
            {competitorsLoading && (
              <div className="py-8 text-center text-sm text-neutral-400 dark:text-white/75">
                加载中…
              </div>
            )}
            {competitorsError && (
              <div className="rounded-lg border border-red-200 bg-red-50 px-3.5 py-2.5 text-[12.5px] text-red-700 dark:border-red-500/25 dark:bg-red-500/10 dark:text-red-300">
                <ErrorRetry message={competitorsError.message} onRetry={() => void refetchCompetitors()} />
              </div>
            )}
            {!competitorsLoading &&
              !competitorsError &&
              competitors &&
              competitors.length === 0 && (
                <Empty text="尚未添加监控对象" />
              )}
            {competitors &&
              competitors.map((w: CompetitorItem, i: number) => (
                <Reveal key={w.team} delay={i * 60}>
                  <div className="card flex flex-col gap-4 p-6 md:flex-row md:items-center">
                    <div className="flex-1">
                      <div className="text-[15px] font-medium">{w.team}</div>
                      <div className="mt-2.5 flex flex-wrap gap-2">
                        {w.aliases.map((a: string) => (
                          <span key={a} className="chip font-mono !text-[11px]">
                            {a}
                          </span>
                        ))}
                      </div>
                    </div>
                    <button
                      className="btn-quiet shrink-0"
                      onClick={() => handleRemoveCompetitor(w)}
                    >
                      移除
                    </button>
                  </div>
                </Reveal>
              ))}
            <Reveal delay={100}>
              <form
                className="card grid items-end gap-4 p-6 md:grid-cols-[1fr_1fr_auto]"
                onSubmit={handleAddCompetitor}
              >
                <label>
                  <span className="text-xs text-neutral-500 dark:text-white/85">团队 / 实验室</span>
                  <input
                    className="input mt-1.5"
                    value={team}
                    onChange={(e) => setTeam(e.target.value)}
                    placeholder="例如：DeepMind Retrieval Team"
                  />
                </label>
                <label>
                  <span className="text-xs text-neutral-500 dark:text-white/85">
                    作者别名（逗号分隔）
                  </span>
                  <input
                    className="input mt-1.5"
                    value={alias}
                    onChange={(e) => setAlias(e.target.value)}
                    placeholder="J. Smith, Jane Smith"
                  />
                </label>
                <button type="submit" className="btn-primary">
                  添加监控
                </button>
              </form>
            </Reveal>
          </div>
        )}
      </div>
    </div>
  );
}
