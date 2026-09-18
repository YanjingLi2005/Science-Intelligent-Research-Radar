import { useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router';
import {
  AlertTriangle,
  ArrowRight,
  BookOpen,
  CheckCircle2,
  Network,
  Radar as RadarIcon,
  Sparkles,
  Users,
} from 'lucide-react';
import { toast } from 'sonner';
import { verdictMeta, matrixStatusMeta, sourceLabel } from '../data/mock';
import { BenchmarkComparison } from '../components/BenchmarkComparison';
import { AuthorLabDrawer } from '../components/AuthorLabDrawer';
import {
  startScan,
  startDeepResearch,
  getScanStatus,
  listScans,
  cancelScan,
  confirmImpact,
  dismissImpact,
  getSettings,
  getAutoScan,
  setAutoScan,
  getScanFilters,
  setScanFilters,
  useApi,
  askLiterature,
  type AutoScanConfig,
  type DeepResearchBrief,
  type LiteratureQAResult,
  type ScanFilters,
} from '../api';
import { useProject } from '../contexts/ProjectContext';
import { Reveal, SectionHead, Stat, Tabs, Quote } from '../components/chrome';
import { Spinner } from '../components/ui/spinner';
import { Skeleton } from '../components/ui/skeleton';
import ErrorRetry from '../components/ErrorRetry';

const STAGES = ['搜索多源文献', '混合检索', '提取公开全文', '逐项比较', '生成行动'];

type ScanState = 'idle' | 'running' | 'done' | 'failed';
type QueueFilter = 'all' | 'challenge' | 'support' | 'boundary' | 'prior';

export default function Radar() {
  const navigate = useNavigate();
  const { project, loading, error, projectId, refreshProject } = useProject();
  const [scan, setScan] = useState<ScanState>('idle');
  const [stage, setStage] = useState(0);
  const [progress, setProgress] = useState(0);
  const [sel, setSel] = useState(() => project?.papers[0]?.id ?? '');
  const [queueFilter, setQueueFilter] = useState<QueueFilter>('all');
  const [tab, setTab] = useState(0);
  const [decisions, setDecisions] = useState<Record<string, 'adopted' | 'rejected'>>(
    () => Object.fromEntries(
      (project?.papers ?? [])
        .filter((item) => item.reviewState === 'confirmed' || item.reviewState === 'dismissed')
        .map((item) => [item.id, item.reviewState === 'confirmed' ? 'adopted' : 'rejected']),
    ),
  );
  const [scanMessage, setScanMessage] = useState('');
  const [decisionError, setDecisionError] = useState('');
  const [savingDecision, setSavingDecision] = useState('');
  const [labProfileOpen, setLabProfileOpen] = useState(false);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);
  const scanIdRef = useRef<string | null>(null);
  const scanToastIdRef = useRef<string | null>(null);
  const scanErrorCountRef = useRef(0);

  // Deep research (gpt-researcher style bounded literature synthesis)
  const [deep, setDeep] = useState<'idle' | 'running' | 'done' | 'failed'>('idle');
  const [deepProgress, setDeepProgress] = useState(0);
  const [deepMessage, setDeepMessage] = useState('');
  const [deepBrief, setDeepBrief] = useState<DeepResearchBrief | null>(null);
  const deepTimer = useRef<ReturnType<typeof setInterval> | null>(null);
  const deepIdRef = useRef<string | null>(null);
  const deepErrorCountRef = useRef(0);

  // Literature Q&A over collected papers
  const [qaQuestion, setQaQuestion] = useState('');
  const [qaResult, setQaResult] = useState<LiteratureQAResult | null>(null);
  const [qaLoading, setQaLoading] = useState(false);
  const [qaError, setQaError] = useState('');

  // Scan configuration
  const [showConfig, setShowConfig] = useState(false);
  const [maxResults, setMaxResults] = useState(32);
  const [analysisLimit, setAnalysisLimit] = useState(3);
  const { data: settings } = useApi(() => getSettings(), []);
  const { data: scans } = useApi(() => listScans(projectId), [projectId]);
  const [autoScan, setAutoScanCfg] = useState<AutoScanConfig | null>(null);
  const [autoScanSaving, setAutoScanSaving] = useState(false);
  const [autoScanError, setAutoScanError] = useState('');
  const [scanFilters, setScanFiltersCfg] = useState<ScanFilters | null>(null);
  const [scanFiltersSaving, setScanFiltersSaving] = useState(false);
  const [scanFiltersError, setScanFiltersError] = useState('');
  const currentProjectId = project?.id;

  const papers = useMemo(() => project?.papers ?? [], [project?.papers]);
  const paper = papers.find((p) => p.id === sel) ?? papers[0];

  const latestScanStats = useMemo(() => {
    const completed = scans?.find((s) => s.status === 'completed');
    return completed?.stats ?? {};
  }, [scans]);
  const dedupedCount = typeof latestScanStats.deduped_duplicates === 'number' ? latestScanStats.deduped_duplicates : 0;
  const visiblePapers = useMemo(
    () => queueFilter === 'all' ? papers : papers.filter((item) => item.verdict === queueFilter),
    [papers, queueFilter],
  );
  const linkedClaim = project?.claims.find((claim) => paper?.claimIds.includes(claim.id));
  const linkedAction = project?.actions.find((action) => action.sourcePaperId === paper?.id);
  // G0 gate: the radar only scans confirmed claims. Fresh extractions are
  // candidates awaiting human confirmation, so the scan button must guide the
  // user instead of failing with a cryptic backend error.
  const confirmedClaimCount = (project?.claims ?? []).filter((c) => c.confirmed === 'yes').length;
  const claimCount = (project?.claims ?? []).length;
  const scanBlocked = confirmedClaimCount === 0;

  useEffect(() => () => {
    if (timer.current) clearInterval(timer.current);
    if (deepTimer.current) clearInterval(deepTimer.current);
  }, []);

  useEffect(() => {
    let cancelled = false;
    if (!currentProjectId) {
      setAutoScanCfg(null);
      setScanFiltersCfg(null);
      return () => {
        cancelled = true;
      };
    }
    getAutoScan(currentProjectId)
      .then((config) => {
        if (!cancelled) setAutoScanCfg(config);
      })
      .catch(() => {
        if (!cancelled) setAutoScanCfg(null);
      });
    getScanFilters(currentProjectId)
      .then((config) => {
        if (!cancelled) setScanFiltersCfg(config);
      })
      .catch(() => {
        if (!cancelled) setScanFiltersCfg(null);
      });
    return () => {
      cancelled = true;
    };
  }, [currentProjectId]);

  const stopPolling = () => {
    if (timer.current) {
      clearInterval(timer.current);
      timer.current = null;
    }
  };

  const poll = async (caseId: string, scanId: string) => {
    try {
      const status = await getScanStatus(caseId, scanId);
      scanErrorCountRef.current = 0;
      const nextProgress = Math.max(0, Math.min(100, (status.progress.value ?? 0) * 100));
      setProgress(nextProgress);
      setStage(Math.min(STAGES.length - 1, Math.floor((nextProgress / 100) * STAGES.length)));
      setScanMessage(status.progress.message ?? '');

      if (status.status === 'completed') {
        stopPolling();
        setScan('done');
        setProgress(100);
        setStage(STAGES.length);
        await refreshProject();
        if (scanToastIdRef.current !== scanId) {
          scanToastIdRef.current = scanId;
          const impactCount = typeof (status.stats as Record<string, unknown> | null)?.impact_count === 'number' ? Number((status.stats as Record<string, unknown>).impact_count) : 0;
          if (impactCount > 0) {
            toast.success(`扫描完成，发现 ${impactCount} 条材料影响`);
          } else {
            toast.success('扫描完成，暂无新的材料影响');
          }
        }
      } else if (status.status === 'failed' || status.status === 'interrupted') {
        stopPolling();
        setScan('failed');
        setScanMessage(status.error_message || status.progress.message || '扫描失败');
        if (scanToastIdRef.current !== scanId) {
          scanToastIdRef.current = scanId;
          toast.error(`扫描失败：${status.error_message || status.progress?.message || '未知错误'}`);
        }
      } else if (status.status === 'cancelled') {
        stopPolling();
        setScan('idle');
        setScanMessage('扫描已取消。');
        await refreshProject();
        if (scanToastIdRef.current !== scanId) {
          scanToastIdRef.current = scanId;
          toast.info('扫描已取消');
        }
      }
    } catch (e) {
      scanErrorCountRef.current += 1;
      // Allow up to 3 consecutive network glitches before marking as completely failed
      if (scanErrorCountRef.current >= 3) {
        stopPolling();
        setScan('failed');
        setScanMessage(e instanceof Error ? e.message : String(e));
      }
    }
  };

  const start = async () => {
    if (!project) return;
    stopPolling();
    scanErrorCountRef.current = 0;
    setScan('running');
    setStage(0);
    setProgress(0);
    setScanMessage('正在启动雷达…');

    try {
      const res = await startScan(project.id, maxResults, analysisLimit);
      scanIdRef.current = res.scan_id;
      timer.current = setInterval(() => {
        void poll(project.id, res.scan_id);
      }, 1000);
      void poll(project.id, res.scan_id);
    } catch (e) {
      scanIdRef.current = null;
      setScan('failed');
      setScanMessage(e instanceof Error ? e.message : String(e));
    }
  };

  const cancel = async () => {
    if (!project) return;
    stopPolling();
    scanErrorCountRef.current = 0;
    if (scanIdRef.current) {
      try {
        await cancelScan(project.id, scanIdRef.current);
      } catch { /* best-effort */ }
      scanIdRef.current = null;
    }
    setScan('idle');
  };

  const stopDeepPolling = () => {
    if (deepTimer.current) {
      clearInterval(deepTimer.current);
      deepTimer.current = null;
    }
  };

  const deepPoll = async (caseId: string, scanId: string) => {
    try {
      const status = await getScanStatus(caseId, scanId);
      deepErrorCountRef.current = 0;
      setDeepProgress(Math.max(0, Math.min(100, (status.progress.value ?? 0) * 100)));
      setDeepMessage(status.progress.message ?? '');

      if (status.status === 'completed') {
        stopDeepPolling();
        setDeep('done');
        setDeepProgress(100);
        const brief = (status.stats?.research_brief ?? null) as DeepResearchBrief | null;
        setDeepBrief(brief);
        if (brief?.executive_summary) setDeepMessage('深度调研完成。');
        await refreshProject();
      } else if (status.status === 'failed' || status.status === 'interrupted') {
        stopDeepPolling();
        setDeep('failed');
        setDeepMessage(status.error_message || status.progress.message || '深度调研失败');
      } else if (status.status === 'cancelled') {
        stopDeepPolling();
        setDeep('idle');
        setDeepMessage('深度调研已取消。');
      }
    } catch (e) {
      deepErrorCountRef.current += 1;
      if (deepErrorCountRef.current >= 3) {
        stopDeepPolling();
        setDeep('failed');
        setDeepMessage(e instanceof Error ? e.message : String(e));
      }
    }
  };

  const startDeep = async () => {
    if (!project) return;
    stopDeepPolling();
    deepErrorCountRef.current = 0;
    setDeep('running');
    setDeepProgress(0);
    setDeepMessage('正在启动深度调研…');
    setDeepBrief(null);

    try {
      const res = await startDeepResearch(project.id, {
        question: project.question,
        depth: 1,
        max_sources: 8,
        max_subqueries: 3,
      });
      deepIdRef.current = res.scan_id;
      deepTimer.current = setInterval(() => {
        void deepPoll(project.id, res.scan_id);
      }, 1500);
      void deepPoll(project.id, res.scan_id);
    } catch (e) {
      deepIdRef.current = null;
      setDeep('failed');
      setDeepMessage(e instanceof Error ? e.message : String(e));
    }
  };

  const deepCancel = async () => {
    if (!project) return;
    stopDeepPolling();
    if (deepIdRef.current) {
      try {
        await cancelScan(project.id, deepIdRef.current);
      } catch { /* best-effort */ }
      deepIdRef.current = null;
    }
    setDeep('idle');
  };

  const ask = async () => {
    if (!project || !qaQuestion.trim()) return;
    setQaLoading(true);
    setQaError('');
    setQaResult(null);
    try {
      const result = await askLiterature(project.id, qaQuestion.trim(), 5);
      setQaResult(result);
    } catch (e) {
      setQaError(e instanceof Error ? e.message : String(e));
    } finally {
      setQaLoading(false);
    }
  };

  const saveAutoScan = async (enabled: boolean, intervalHours?: number) => {
    if (!project || !autoScan) return;
    setAutoScanSaving(true);
    setAutoScanError('');
    try {
      const cfg = await setAutoScan(project.id, {
        enabled,
        interval_hours: intervalHours ?? autoScan.interval_hours,
      });
      setAutoScanCfg(cfg);
    } catch (e) {
      setAutoScanError(e instanceof Error ? e.message : String(e));
    } finally {
      setAutoScanSaving(false);
    }
  };

  const saveScanFilters = async (filters: Partial<ScanFilters>) => {
    if (!project || !scanFilters) return;
    setScanFiltersSaving(true);
    setScanFiltersError('');
    try {
      const cfg = await setScanFilters(project.id, filters);
      setScanFiltersCfg(cfg);
    } catch (e) {
      setScanFiltersError(e instanceof Error ? e.message : String(e));
    } finally {
      setScanFiltersSaving(false);
    }
  };

  const decide = async (paperId: string, decision: 'adopted' | 'rejected') => {
    if (!project) return;
    setSavingDecision(paperId);
    setDecisionError('');
    try {
      if (decision === 'adopted') {
        await confirmImpact(project.id, paperId);
      } else {
        await dismissImpact(project.id, paperId);
      }
      setDecisions((current) => ({ ...current, [paperId]: decision }));
      await refreshProject();
    } catch (e) {
      setDecisionError(e instanceof Error ? e.message : String(e));
    } finally {
      setSavingDecision('');
    }
  };

  const summary = useMemo(
    () => [
      { v: papers.length, l: '公开论文' },
      { v: papers.length, l: '深度比较' },
      { v: papers.filter((p) => p.verdict !== 'none').length, l: '材料影响' },
      { v: papers.filter((p) => p.urgency === 'urgent').length, l: '紧急', tone: 'red' as const },
      { v: papers.filter((p) => p.verdict === 'none' && p.urgency === 'urgent').length, l: '竞争预警', tone: 'orange' as const },
      { v: papers.filter((p) => p.verdict === 'prior' || p.verdict === 'boundary').length, l: '完整性' },
    ],
    [papers],
  );

  const dotCls: Record<string, string> = {
    challenge: 'bg-[#B42318]',
    support: 'bg-[#067647]',
    boundary: 'bg-[#B54708]',
    prior: 'bg-[#175CD3]',
    none: 'bg-[#98A2B3]',
  };

function RadarSkeleton() {
  return (
    <div className="space-y-8 animate-pulse" aria-busy="true" aria-live="polite">
      <div>
        <div className="flex items-center justify-between">
          <div>
            <Skeleton className="h-4 w-16 mb-2" />
            <Skeleton className="h-7 w-32" />
          </div>
          <Skeleton className="h-4 w-28" />
        </div>
        <Skeleton className="h-4 w-96 mt-2" />
        <div className="flex gap-2 mt-3">
          <Skeleton className="h-6 w-20 rounded-full" />
          <Skeleton className="h-6 w-24 rounded-full" />
          <Skeleton className="h-6 w-28 rounded-full" />
        </div>
      </div>

      <div className="card p-7">
        <div className="flex justify-between items-center">
          <div className="space-y-2">
            <Skeleton className="h-4 w-24" />
            <Skeleton className="h-4 w-72" />
          </div>
          <Skeleton className="h-10 w-36 rounded-lg" />
        </div>
      </div>

      <div className="grid grid-cols-3 md:grid-cols-6 gap-6 py-6 border-y border-hairline dark:border-white/10">
        {[...Array(6)].map((_, i) => (
          <div key={i} className="space-y-2">
            <Skeleton className="h-7 w-12" />
            <Skeleton className="h-3 w-16" />
          </div>
        ))}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-12 gap-8 items-start">
        <div className="lg:col-span-4 space-y-4">
          <div className="card p-4 space-y-3">
            <Skeleton className="h-8 w-full rounded-md" />
            <Skeleton className="h-20 w-full rounded-md" />
            <Skeleton className="h-20 w-full rounded-md" />
            <Skeleton className="h-20 w-full rounded-md" />
          </div>
        </div>
        <div className="lg:col-span-8 space-y-4">
          <div className="card p-7 space-y-4">
            <div className="flex gap-2">
              <Skeleton className="h-5 w-20 rounded-full" />
              <Skeleton className="h-5 w-20 rounded-full" />
            </div>
            <Skeleton className="h-8 w-3/4" />
            <Skeleton className="h-4 w-1/2" />
            <Skeleton className="h-32 w-full rounded-lg" />
            <Skeleton className="h-48 w-full rounded-lg" />
          </div>
        </div>
      </div>
    </div>
  );
}

  if (loading) {
    return <RadarSkeleton />;
  }

  if (error) {
    return (
      <div className="card p-8 text-center">
        <ErrorRetry message={`加载失败: ${error.message}`} onRetry={() => void refreshProject()} />
      </div>
    );
  }

  if (!project) {
    return (
      <div className="card p-8 text-center">
        <p className="text-neutral-400 text-sm dark:text-white/80">未找到项目</p>
      </div>
    );
  }

  return (
    <div>
      <Reveal>
        <SectionHead
          kicker="Radar"
          title="文献雷达"
          right={<span className="locator">上次扫描 {project.lastScan}</span>}
        />
        <p className="text-sm text-neutral-500 dark:text-white/80 leading-relaxed">
          雷达自动从你的文稿与 Claim 提炼监控主题，持续盯住公开文献 —— 不需要自己写检索词：
        </p>
        <div className="flex flex-wrap gap-2 mt-3">
          {project.topics.map((t) => (
            <span key={t} className="chip font-mono !text-[11px]">{t}</span>
          ))}
        </div>
      </Reveal>

      {/* scan control with config */}
      <Reveal delay={80}>
        <div className="card mt-8 p-7">
          {scan === 'idle' && (
            <div>
              <div className="flex flex-col md:flex-row md:items-center gap-5 justify-between">
                <div>
                  <div className="kicker mb-2">Scan protocol</div>
                  <p className="text-sm text-neutral-500 dark:text-white/80">先找语义相关性，再沿引用与全文证据核验，最后落到行动。</p>
                  <div className="flex flex-wrap gap-2 mt-3">
                    <span className="badge-gray badge-dot">语义检索</span>
                    <span className="badge-blue badge-dot">多源文献</span>
                    <span className="badge-teal badge-dot">全文比较</span>
                    <span className="badge-green badge-dot">行动建议</span>
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  <button
                    className="btn-ghost text-xs"
                    onClick={startDeep}
                    disabled={deep === 'running'}
                    title="对当前研究问题做一次有边界的深度文献调研，输出结构化简报"
                  >
                    <BookOpen size={14} /> 深度调研
                  </button>
                  <button
                    className="btn-quiet text-xs"
                    onClick={() => navigate(`/cases/${projectId}/deep-research`)}
                  >
                    历史调研
                  </button>
                  <button
                    className="btn-ghost text-xs"
                    onClick={() => setShowConfig((s) => !s)}
                  >
                    {showConfig ? '收起配置' : '⚙ 搜索配置'}
                  </button>
                  <button
                    className="btn-primary !px-6 !py-3"
                    onClick={start}
                    disabled={scanBlocked}
                    title={scanBlocked ? '请先在「论文 Claim」页确认至少一条 Claim 后再扫描' : undefined}
                  >
                    <RadarIcon size={15} /> 启动雷达扫描
                  </button>
                </div>
              </div>

              {/* G0 gate guidance: fresh claims are candidates awaiting human confirmation */}
              {scanBlocked && (
                <div className="mt-5 rounded-lg border border-amber-200 bg-amber-50 px-3.5 py-2.5 text-[12.5px] leading-relaxed text-amber-800 dark:border-amber-500/25 dark:bg-amber-500/10 dark:text-amber-200">
                  {claimCount === 0 ? (
                    <>
                      还没有可扫描的 Claim。请先到「论文 Claim」页检查文稿的 Claim 提取结果，
                      或重新上传文稿后再次提取；确认至少一条 Claim 后即可开始扫描。
                    </>
                  ) : (
                    <>
                      雷达只扫描<b>已确认</b>的 Claim（G0 关口）。
                      当前 {confirmedClaimCount}/{claimCount} 条已确认。请先到「论文 Claim」页，
                      确认至少一条 Claim 后再开始扫描。
                    </>
                  )}
                  <button
                    onClick={() => navigate(`/cases/${projectId}/paper`)}
                    className="mt-2 flex items-center gap-1 rounded-md px-2 py-1 text-[12px] font-medium text-amber-900 underline decoration-amber-500/50 underline-offset-2 hover:bg-amber-100 dark:text-amber-100 dark:hover:bg-amber-500/10"
                  >
                    <ArrowRight size={13} /> 去确认 Claim
                  </button>
                </div>
              )}

              {/* Config panel */}
              {showConfig && (
                <div className="mt-5 pt-5 hairline-t grid md:grid-cols-2 gap-4">
                  <label className="block">
                    <span className="text-xs text-neutral-500 dark:text-white/85">搜索篇数上限</span>
                    <select
                      className="input mt-1.5"
                      value={maxResults}
                      onChange={(e) => setMaxResults(Number(e.target.value))}
                    >
                      <option value={16}>16 篇</option>
                      <option value={32}>32 篇</option>
                      <option value={50}>50 篇</option>
                      <option value={100}>100 篇</option>
                    </select>
                    <span className="text-[10px] text-neutral-400 dark:text-white/75 mt-0.5 block">多源搜索结果上限</span>
                  </label>
                  <label className="block">
                    <span className="text-xs text-neutral-500 dark:text-white/85">深度分析篇数</span>
                    <select
                      className="input mt-1.5"
                      value={analysisLimit}
                      onChange={(e) => setAnalysisLimit(Number(e.target.value))}
                    >
                      <option value={1}>1 篇</option>
                      <option value={3}>3 篇</option>
                      <option value={5}>5 篇</option>
                      <option value={7}>7 篇</option>
                      <option value={10}>10 篇</option>
                    </select>
                    <span className="text-[10px] text-neutral-400 dark:text-white/75 mt-0.5 block">LLM 逐项比较与行动建议篇数</span>
                  </label>
                  <div className="md:col-span-2 text-xs text-neutral-400 dark:text-white/75">
                    本次使用：{settings?.llm.model ?? '未配置模型'}。
                    模型切换统一在设置页完成，避免把其他提供商的模型名发送到当前接口。
                  </div>
                  <div className="md:col-span-2 rounded-lg border border-hairline p-4 dark:border-white/[0.08]">
                    <div className="flex items-center justify-between gap-3">
                      <div>
                        <div className="text-[13px] font-medium">自动雷达扫描</div>
                        <div className="text-[11px] text-neutral-400 dark:text-white/80 mt-0.5">
                          定时对已确认 Claim 自动启动文献雷达，持续盯住公开文献。
                        </div>
                      </div>
                      <button
                        className={`btn-quiet shrink-0 ${autoScan?.enabled ? '!text-teal' : ''}`}
                        disabled={autoScanSaving || !autoScan}
                        onClick={() => void saveAutoScan(!autoScan?.enabled)}
                      >
                        {autoScanSaving ? '保存中…' : autoScan?.enabled ? '已开启' : '已关闭'}
                      </button>
                    </div>
                    {autoScan?.enabled && (
                      <div className="mt-3 flex items-center gap-2">
                        <span className="text-xs text-neutral-500 dark:text-white/85">间隔</span>
                        <select
                          className="input !w-28 !py-1.5 text-xs"
                          value={autoScan.interval_hours}
                          disabled={autoScanSaving}
                          onChange={(e) => void saveAutoScan(true, Number(e.target.value))}
                        >
                          <option value={24}>每天</option>
                          <option value={168}>每周</option>
                          <option value={720}>每月</option>
                        </select>
                        {autoScan.next_run_at && (
                          <span className="locator ml-auto">下次 {new Date(autoScan.next_run_at).toLocaleString()}</span>
                        )}
                      </div>
                    )}
                    {autoScanError && (
                      <div className="mt-2 text-[11px] text-red-600 dark:text-red-300">{autoScanError}</div>
                    )}
                    {autoScan?.last_error && (
                      <div className="mt-2 text-[11px] text-amber-600 dark:text-amber-300">
                        上次自动启动失败：{autoScan.last_error}
                      </div>
                    )}
                  </div>
                  <div className="md:col-span-2 rounded-lg border border-hairline p-4 dark:border-white/[0.08]">
                    <div className="text-[13px] font-medium">扫描定向过滤</div>
                    <div className="text-[11px] text-neutral-400 dark:text-white/80 mt-0.5">
                      只保留目标 CCF 等级 / arXiv 分类的论文（用于 CS/AI 定向监控）。
                    </div>
                    <div className="mt-3 grid md:grid-cols-2 gap-3">
                      <label className="block">
                        <span className="text-xs text-neutral-500 dark:text-white/85">CCF 等级</span>
                        <select
                          className="input mt-1.5"
                          value={scanFilters?.ccf_rank ?? ''}
                          disabled={scanFiltersSaving}
                          onChange={(e) => void saveScanFilters({ ccf_rank: e.target.value || null })}
                        >
                          <option value="">全部</option>
                          <option value="A">仅 CCF-A</option>
                          <option value="B">仅 CCF-B</option>
                          <option value="C">仅 CCF-C</option>
                        </select>
                      </label>
                      <label className="block">
                        <span className="text-xs text-neutral-500 dark:text-white/85">arXiv 分类（逗号分隔）</span>
                        <input
                          className="input mt-1.5"
                          placeholder="cs.AI, cs.CL, cs.LG"
                          defaultValue={(scanFilters?.arxiv_categories ?? []).join(', ')}
                          disabled={scanFiltersSaving}
                          onBlur={(e) => {
                            const cats = e.target.value.split(',').map((s) => s.trim()).filter(Boolean);
                            void saveScanFilters({ arxiv_categories: cats });
                          }}
                        />
                      </label>
                    </div>
                    {scanFiltersError && (
                      <div className="mt-2 text-[11px] text-red-600 dark:text-red-300">{scanFiltersError}</div>
                    )}
                  </div>
                </div>
              )}
            </div>
          )}
          {scan === 'running' && (
            <div className="flex flex-col md:flex-row items-start md:items-center gap-6">
              <div className="relative w-16 h-16 shrink-0">
                <div className="absolute inset-0 rounded-full border-2 border-teal/40 animate-ping opacity-25" />
                <div className="absolute inset-0 rounded-full border border-teal/30" />
                <div className="absolute inset-2 rounded-full border border-teal/20" />
                <div className="absolute inset-0 radar-ring rounded-full border border-teal/40" />
                <div className="absolute inset-0 radar-beam">
                  <div className="absolute left-1/2 top-1/2 w-1/2 h-[2px] bg-gradient-to-r from-teal to-transparent origin-left" />
                </div>
                <div className="absolute left-1/2 top-1/2 w-2 h-2 -translate-x-1/2 -translate-y-1/2 rounded-full bg-teal shadow-[0_0_8px_rgba(20,184,166,0.8)]" />
              </div>
              <div className="flex-1 min-w-0 w-full space-y-3">
                <div className="flex items-center justify-between gap-2">
                  <div className="flex items-center gap-2">
                    <span className="badge-teal font-mono text-[10px] uppercase tracking-wider">Scanning · Stage {stage + 1}/{STAGES.length}</span>
                    <span className="text-xs font-semibold text-neutral-800 dark:text-white/90">{STAGES[stage] ?? '检索分析中'}</span>
                  </div>
                  <span className="font-mono text-xs font-bold text-teal">{Math.round(progress)}%</span>
                </div>
                <div className="flex items-center gap-1.5 flex-wrap">
                  {STAGES.map((s, i) => (
                    <div
                      key={s}
                      className={`flex items-center gap-1 px-2 py-0.5 rounded text-[11px] transition-all duration-300 ${
                        i < stage
                          ? 'bg-teal/10 text-teal dark:bg-teal/20 font-medium'
                          : i === stage
                          ? 'bg-teal text-white shadow-sm font-semibold'
                          : 'bg-neutral-100 text-neutral-400 dark:bg-white/[0.05] dark:text-white/50'
                      }`}
                    >
                      {i < stage ? (
                        <CheckCircle2 size={11} className="text-teal" />
                      ) : i === stage ? (
                        <Spinner className="size-2.5 text-white" />
                      ) : (
                        <span className="w-1 h-1 rounded-full bg-neutral-300 dark:bg-white/30" />
                      )}
                      <span>{s}</span>
                    </div>
                  ))}
                </div>
                <div className="h-1.5 bg-hairline dark:bg-white/10 rounded-full overflow-hidden relative">
                  <div
                    className="h-full bg-gradient-to-r from-teal-500 to-emerald-400 transition-all duration-300 rounded-full shadow-[0_0_12px_rgba(20,184,166,0.5)]"
                    style={{ width: `${progress}%` }}
                  />
                </div>
                {scanMessage && (
                  <p className="text-xs text-neutral-500 dark:text-white/75 flex items-center gap-1.5">
                    <span className="w-1.5 h-1.5 rounded-full bg-teal animate-pulse" />
                    {scanMessage}
                  </p>
                )}
              </div>
              <button className="btn-quiet text-xs shrink-0 self-center" onClick={cancel}>
                取消扫描
              </button>
            </div>
          )}
          {scan === 'done' && (
            <div className="flex flex-col md:flex-row md:items-center gap-5 justify-between">
              <div className="flex items-center gap-3">
                <span className="badge-green badge-dot">扫描完成</span>
                <span className="text-sm text-neutral-500 dark:text-white/80">
                  {summary[2].v > 0
                    ? `发现 ${summary[2].v} 项材料影响，${summary[3].v} 项紧急`
                    : '扫描完成，但没有新的材料影响或证据'}
                </span>
              </div>
              <button className="btn-ghost" onClick={start}>
                重新扫描
              </button>
            </div>
          )}
          {scan === 'failed' && (
            <div className="flex flex-col md:flex-row md:items-center gap-4 justify-between">
              <div>
                <span className="badge-red badge-dot">扫描失败</span>
                <p className="mt-2 text-xs text-red-700 break-words dark:text-red-300">{scanMessage}</p>
              </div>
              <button className="btn-ghost" onClick={() => setScan('idle')}>修改配置后重试</button>
            </div>
          )}
        </div>
      </Reveal>

      {/* deep research panel */}
      {deep !== 'idle' && (
        <Reveal delay={100}>
          <div className="card mt-6 p-7">
            {deep === 'running' && (
              <div className="flex flex-col md:flex-row items-start md:items-center gap-6">
                <div className="relative w-16 h-16 shrink-0">
                  <div className="absolute inset-0 rounded-full border-2 border-[#4E8AFB]/40 animate-ping opacity-25" />
                  <div className="absolute inset-0 rounded-full border border-[#4E8AFB]/30" />
                  <div className="absolute inset-2 rounded-full border border-[#4E8AFB]/20" />
                  <div className="absolute inset-0 radar-ring rounded-full border border-[#4E8AFB]/40" />
                  <div className="absolute left-1/2 top-1/2 w-2 h-2 -translate-x-1/2 -translate-y-1/2 rounded-full bg-[#4E8AFB] shadow-[0_0_8px_rgba(78,138,251,0.8)]" />
                </div>
                <div className="flex-1 min-w-0 w-full space-y-3">
                  <div className="flex items-center justify-between gap-2">
                    <div className="flex items-center gap-2">
                      <BookOpen size={14} className="text-[#4E8AFB]" />
                      <span className="text-xs font-semibold text-neutral-800 dark:text-white/90">
                        深度文献调研与结构化综述生成中
                      </span>
                    </div>
                    <span className="font-mono text-xs font-bold text-[#4E8AFB]">{Math.round(deepProgress)}%</span>
                  </div>
                  <div className="h-1.5 bg-hairline dark:bg-white/10 rounded-full overflow-hidden relative">
                    <div
                      className="h-full bg-gradient-to-r from-blue-500 to-indigo-500 transition-all duration-300 rounded-full shadow-[0_0_12px_rgba(78,138,251,0.5)]"
                      style={{ width: `${deepProgress}%` }}
                    />
                  </div>
                  {deepMessage && (
                    <p className="text-xs text-neutral-500 dark:text-white/75 flex items-center gap-1.5">
                      <span className="w-1.5 h-1.5 rounded-full bg-[#4E8AFB] animate-pulse" />
                      {deepMessage}
                    </p>
                  )}
                </div>
                <button className="btn-quiet text-xs shrink-0 self-center" onClick={deepCancel}>
                  取消调研
                </button>
              </div>
            )}
            {deep === 'failed' && (
              <div className="flex flex-col md:flex-row md:items-center gap-3 justify-between">
                <div>
                  <span className="badge-red badge-dot">深度调研失败</span>
                  <p className="mt-2 text-xs text-red-700 break-words dark:text-red-300">{deepMessage}</p>
                </div>
                <button className="btn-ghost" onClick={() => setDeep('idle')}>关闭</button>
              </div>
            )}
            {deep === 'done' && deepBrief && (
              <div className="space-y-6">
                <div className="flex items-start justify-between gap-4">
                  <div>
                    <div className="kicker mb-2">Deep research brief</div>
                    <h2 className="display-md">{deepBrief.title}</h2>
                    <p className="mt-2 text-sm text-neutral-600 leading-relaxed dark:text-white/90">{deepBrief.executive_summary}</p>
                  </div>
                  <button className="btn-quiet shrink-0" onClick={() => setDeep('idle')}>关闭</button>
                </div>

                {deepBrief.sections.length > 0 && (
                  <div className="grid gap-5">
                    {deepBrief.sections.map((section) => (
                      <div key={section.heading} className="rounded-lg border border-hairline p-4 dark:border-white/[0.08]">
                        <div className="kicker mb-2">{section.heading}</div>
                        <ul className="space-y-1.5">
                          {section.findings.map((finding) => (
                            <li key={finding} className="text-[13px] leading-relaxed text-neutral-700 dark:text-white/95">· {finding}</li>
                          ))}
                        </ul>
                      </div>
                    ))}
                  </div>
                )}

                <div className="grid md:grid-cols-2 gap-5">
                  {deepBrief.key_insights.length > 0 && (
                    <div>
                      <div className="kicker mb-2">关键洞察</div>
                      <ul className="space-y-1.5">
                        {deepBrief.key_insights.map((item) => (
                          <li key={item} className="text-[13px] text-neutral-600 dark:text-white/90">· {item}</li>
                        ))}
                      </ul>
                    </div>
                  )}
                  {deepBrief.contradictions.length > 0 && (
                    <div>
                      <div className="kicker mb-2">矛盾 / 张力</div>
                      <ul className="space-y-1.5">
                        {deepBrief.contradictions.map((item) => (
                          <li key={item} className="text-[13px] text-amber-700 dark:text-amber-300/80">· {item}</li>
                        ))}
                      </ul>
                    </div>
                  )}
                  {deepBrief.research_gaps.length > 0 && (
                    <div>
                      <div className="kicker mb-2">研究空白</div>
                      <ul className="space-y-1.5">
                        {deepBrief.research_gaps.map((item) => (
                          <li key={item} className="text-[13px] text-neutral-600 dark:text-white/90">· {item}</li>
                        ))}
                      </ul>
                    </div>
                  )}
                  {deepBrief.recommended_next_steps.length > 0 && (
                    <div>
                      <div className="kicker mb-2">建议下一步</div>
                      <ul className="space-y-1.5">
                        {deepBrief.recommended_next_steps.map((item) => (
                          <li key={item} className="text-[13px] text-neutral-600 dark:text-white/90">· {item}</li>
                        ))}
                      </ul>
                    </div>
                  )}
                </div>

                {deepBrief.sources.length > 0 && (
                  <div>
                    <div className="kicker mb-2">来源论文（{deepBrief.sources.length}）</div>
                    <ul className="space-y-2">
                      {deepBrief.sources.map((source) => (
                        <li key={source.source_id} className="rounded-lg border border-hairline px-4 py-3 text-[12.5px] dark:border-white/[0.08]">
                          <div className="flex items-center gap-2">
                            <span className="font-medium text-neutral-800 dark:text-white/95">{source.title}</span>
                            <span className="locator ml-auto">{source.year ?? ''}</span>
                          </div>
                          <div className="mt-1 text-neutral-500 dark:text-white/85">{source.authors.join(', ')}</div>
                          {source.relevance && <div className="mt-1 text-neutral-600 dark:text-white/90">{source.relevance}</div>}
                          {source.url && (
                            <a href={source.url} target="_blank" rel="noreferrer" className="mt-1 inline-block text-[#4E8AFB] hover:underline">
                              {source.url}
                            </a>
                          )}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}

                {deepBrief.warnings.length > 0 && (
                  <div className="rounded-lg border border-amber-200 bg-amber-50 px-3.5 py-2.5 text-[12px] text-amber-800 dark:border-amber-500/25 dark:bg-amber-500/10 dark:text-amber-200">
                    {deepBrief.warnings.join('；')}
                  </div>
                )}
              </div>
            )}
          </div>
        </Reveal>
      )}

      {/* literature Q&A */}
      <Reveal delay={100}>
        <div className="card mt-6 p-7">
          <div className="flex items-center gap-2">
            <BookOpen size={14} className="text-teal" />
            <div className="kicker">Ask collected papers</div>
            <button
              className="ml-auto inline-flex items-center gap-1 text-[12px] text-teal hover:underline"
              onClick={() => navigate(`/cases/${projectId}/qa`)}
            >
              打开独立问答 <ArrowRight size={13} />
            </button>
          </div>
          <p className="mt-1 text-sm text-neutral-500 dark:text-white/80">
            在已收集的论文库里提问，答案会带来源论文与证据片段。
          </p>
          <div className="mt-4 flex gap-2">
            <input
              className="input flex-1"
              placeholder="例如：这些论文里有哪些方法提升了检索增强生成的鲁棒性？"
              value={qaQuestion}
              onChange={(e) => setQaQuestion(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') void ask(); }}
            />
            <button className="btn-primary shrink-0" onClick={() => void ask()} disabled={qaLoading || !qaQuestion.trim()}>
              {qaLoading ? '询问中…' : '提问'}
            </button>
          </div>
          {qaLoading && (
            <div className="mt-4 rounded-lg border border-hairline p-4 space-y-3 animate-pulse dark:border-white/[0.08]" aria-busy="true" aria-live="polite">
              <div className="flex items-center gap-2">
                <Spinner className="size-3.5 text-teal" />
                <span className="text-xs font-medium text-teal">正在检索文献并生成精准回答…</span>
              </div>
              <Skeleton className="h-4 w-full" />
              <Skeleton className="h-4 w-5/6" />
              <Skeleton className="h-4 w-3/4" />
              <div className="flex gap-2 pt-1">
                <Skeleton className="h-5 w-24 rounded-full" />
                <Skeleton className="h-5 w-28 rounded-full" />
              </div>
            </div>
          )}
          {qaError && (
            <div className="mt-3">
              <ErrorRetry message={`问答请求失败：${qaError}`} onRetry={() => void ask()} />
            </div>
          )}
          {qaResult && (
            <div className="mt-5 space-y-4">
              <div className="rounded-lg border border-hairline p-4 dark:border-white/[0.08]">
                <div className="kicker mb-2">Answer</div>
                <p className="text-sm leading-relaxed text-neutral-700 dark:text-white/95">{qaResult.answer}</p>
                {qaResult.uncertainty && (
                  <p className="mt-2 text-xs text-neutral-500 dark:text-white/80">不确定性：{qaResult.uncertainty}</p>
                )}
              </div>
              {qaResult.sources.length > 0 && (
                <div>
                  <div className="kicker mb-2">来源（{qaResult.sources.length}）</div>
                  <ul className="space-y-2">
                    {qaResult.sources.map((source) => (
                      <li key={source.source_id} className="rounded-lg border border-hairline px-4 py-3 text-[12.5px] dark:border-white/[0.08]">
                        <div className="flex items-center gap-2">
                          <span className="font-medium text-neutral-800 dark:text-white/95">{source.title}</span>
                          {source.year && <span className="locator ml-auto">{source.year}</span>}
                        </div>
                        {source.venue && <div className="mt-0.5 text-neutral-500 dark:text-white/85">{source.venue}</div>}
                        {source.evidence_snippets.length > 0 && (
                          <div className="mt-2 space-y-1">
                            {source.evidence_snippets.map((snippet) => (
                              <p key={snippet} className="text-xs text-neutral-500 dark:text-white/85">“{snippet}”</p>
                            ))}
                          </div>
                        )}
                        {source.url && (
                          <a href={source.url} target="_blank" rel="noreferrer" className="mt-1 inline-block text-teal hover:underline">
                            {source.url}
                          </a>
                        )}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
              {qaResult.warnings.length > 0 && (
                <div className="rounded-lg border border-amber-200 bg-amber-50 px-3.5 py-2.5 text-[12px] text-amber-800 dark:border-amber-500/25 dark:bg-amber-500/10 dark:text-amber-200">
                  {qaResult.warnings.join('；')}
                </div>
              )}
            </div>
          )}
        </div>
      </Reveal>

      {/* summary metrics */}
      <Reveal delay={120}>
        <div className="grid grid-cols-3 md:grid-cols-6 gap-6 mt-8 pb-8 hairline-b">
          {summary.map((m) => (
            <Stat key={m.l} value={m.v} label={m.l} tone={m.tone} />
          ))}
        </div>
        {dedupedCount > 0 && (
          <p className="locator mt-2">最近一次扫描跨源去重合并了 {dedupedCount} 篇重复论文</p>
        )}
      </Reveal>

      {/* scan timeline */}
      {scans && scans.length > 0 && (
        <Reveal delay={130}>
          <div className="card mt-8 p-6">
            <div className="kicker mb-3">扫描时间线</div>
            <div className="space-y-2">
              {scans.slice(0, 10).map((scan) => (
                <div key={scan.id} className="flex flex-wrap items-center gap-3 text-[12px]">
                  <span className="locator">{scan.started_at ? new Date(scan.started_at).toLocaleString() : ''}</span>
                  <span className={`badge ${scan.status === 'completed' ? 'badge-green' : scan.status === 'failed' || scan.status === 'interrupted' ? 'badge-red' : scan.status === 'running' ? 'badge-blue' : 'badge-gray'}`}>
                    {scan.status}
                  </span>
                  <span className="text-neutral-500 dark:text-white/85">论文 {Number(scan.stats?.scanned_papers ?? 0)}</span>
                  <span className="text-neutral-500 dark:text-white/85">影响 {Number(scan.stats?.impact_candidates ?? 0)}</span>
                  {typeof scan.stats?.deduped_duplicates === 'number' && (
                    <span className="text-neutral-500 dark:text-white/85">去重 {Number(scan.stats.deduped_duplicates)}</span>
                  )}
                  {typeof scan.estimated_cost === 'number' && scan.estimated_cost > 0 && (
                    <span className="text-neutral-500 dark:text-white/85">${scan.estimated_cost.toFixed(2)}</span>
                  )}
                </div>
              ))}
            </div>
          </div>
        </Reveal>
      )}

      {/* two-column workspace */}
      <div className="mt-8 grid lg:grid-cols-[340px_1fr] gap-6 items-start">
        {/* impact queue */}
        <Reveal delay={140}>
          <div className="card overflow-hidden">
            <div className="px-5 py-4 hairline-b">
              <div className="flex items-center justify-between">
                <span className="text-sm font-medium">影响队列</span>
                <span className="locator">{visiblePapers.length} / {papers.length} 篇</span>
              </div>
              <div className="mt-3 flex items-center gap-1.5 overflow-x-auto pb-0.5">
                {([
                  ['all', '全部'],
                  ['challenge', '挑战'],
                  ['support', '支持'],
                  ['boundary', '边界'],
                  ['prior', '在先'],
                ] as [QueueFilter, string][]).map(([value, label]) => (
                  <button
                    key={value}
                    className={`rounded-full border px-2.5 py-1 text-[10px] whitespace-nowrap transition-colors ${
                      queueFilter === value
                        ? 'border-ink bg-ink text-white dark:border-white dark:bg-white dark:text-[#111113]'
                        : 'border-hairline text-neutral-500 hover:border-neutral-400 dark:border-white/10 dark:text-white/85 dark:hover:border-white/30 dark:hover:text-white'
                    }`}
                    onClick={() => setQueueFilter(value)}
                  >
                    {label}
                  </button>
                ))}
              </div>
            </div>
            <ul>
              {visiblePapers.map((p) => (
                <li key={p.id}>
                  <button
                    onClick={() => { setSel(p.id); setTab(0); }}
                    className={`w-full text-left px-5 py-4 border-b border-hairline/60 transition-colors duration-200 dark:border-white/[0.06] ${
                      sel === p.id ? 'bg-teal-soft/60 dark:bg-white/[0.07]' : 'hover:bg-neutral-50 dark:hover:bg-white/[0.04]'
                    }`}
                  >
                    <div className="flex items-center gap-2 mb-1.5">
                      <span className={`w-[7px] h-[7px] rounded-full shrink-0 ${dotCls[p.verdict]}`} />
                      <span className="font-mono text-[11px] text-neutral-400 dark:text-white/75">{p.claimIds.join(' · ')}</span>
                      {p.urgency === 'urgent' && <span className="badge-red ml-auto">紧急</span>}
                      {decisions[p.id] === 'adopted' && <span className="badge-green ml-auto">已采用</span>}
                    </div>
                    <div className="text-[13px] leading-snug text-neutral-700 line-clamp-2 dark:text-white/95">{p.title}</div>
                    <div className="locator mt-1.5">{p.date}</div>
                  </button>
                </li>
              ))}
              {visiblePapers.length === 0 && <li className="px-5 py-10 text-center text-xs text-neutral-400 dark:text-white/75">这个分类暂时没有材料</li>}
            </ul>
          </div>
        </Reveal>

        {/* detail */}
        {paper && (
          <Reveal delay={180}>
            <div className="card p-7">
              <div className="flex flex-wrap items-center gap-3 mb-2">
                <span className={verdictMeta[paper.verdict].cls}>{verdictMeta[paper.verdict].label}</span>
                {(paper.empiricalDominance || paper.strategicFlags?.includes('EMPIRICAL_DOMINANCE')) && (
                  <span className="badge-red text-[10px] font-bold font-mono">EMPIRICAL_DOMINANCE</span>
                )}
                <span className="locator">{sourceLabel(paper)}</span>
                {paper.ccfRank && <span className="badge-blue badge-dot">CCF-{paper.ccfRank}</span>}
                {paper.arxivPrimaryCategory && <span className="locator">{paper.arxivPrimaryCategory}</span>}
                {paper.fieldsOfStudy && paper.fieldsOfStudy.length > 0 && (
                  <span className="locator">{paper.fieldsOfStudy.slice(0, 3).join(' · ')}</span>
                )}
                <span className="locator ml-auto">{paper.date}</span>
              </div>
              <h2 className="display-md">{paper.title}</h2>
              <p className="mt-1.5 text-xs text-neutral-400 dark:text-white/75">{paper.authors.join(', ')}</p>

              {/* Author & Lab Profile badges / drawer trigger */}
              <div className="mt-3 flex flex-wrap items-center gap-2">
                {(paper.influenceMetrics?.is_top_tier_lab || paper.authorProfile?.is_top_tier || paper.strategicFlags?.includes('TOP_TIER_LAB')) && (
                  <span className="badge-blue">Top-Tier Lab</span>
                )}
                {((typeof paper.influenceMetrics?.top_h_index === 'number' && paper.influenceMetrics.top_h_index > 0) || (typeof paper.authorProfile?.top_h_index === 'number' && paper.authorProfile.top_h_index > 0)) && (
                  <span className="badge-teal">H-Index {paper.influenceMetrics?.top_h_index ?? paper.authorProfile?.top_h_index}</span>
                )}
                {typeof paper.influenceMetrics?.citation_velocity === 'number' && paper.influenceMetrics.citation_velocity > 0 && (
                  <span className="badge-orange">Citation Velocity {paper.influenceMetrics.citation_velocity}/mo</span>
                )}
                <button
                  onClick={() => setLabProfileOpen(true)}
                  className="btn-ghost text-xs py-1 px-2.5 flex items-center gap-1.5"
                >
                  <Users className="size-3.5" /> 团队动态 (Lab Profile)
                </button>
              </div>

              {/* Direct Conflict / Superior Metric Callout Banner */}
              {(paper.verdict === 'challenge' || paper.urgency === 'urgent' || paper.matrix.some((r) => r.status === 'diff')) && (
                <div className="mt-5 rounded-xl border border-red-200 bg-red-50/70 p-4 transition-all duration-200 dark:border-red-500/25 dark:bg-red-500/10">
                  <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
                    <div className="flex items-start gap-2.5">
                      <AlertTriangle size={18} className="text-red-600 shrink-0 mt-0.5 dark:text-red-400" />
                      <div>
                        <div className="text-[13px] font-semibold text-red-900 dark:text-red-200">
                          {paper.verdict === 'challenge' ? '存在直接冲突 / 削弱证据 (Direct Conflict)' : '检测到关键指标或适用边界差异 (Superior Metric / Diff)'}
                        </div>
                        <p className="text-xs text-red-700 dark:text-red-300 mt-0.5 leading-relaxed">
                          {paper.why || '公开论文的实验结果或主张可能对你的文稿形成削弱，建议在改造工作台中调整论述或生成针对性改写补丁。'}
                        </p>
                      </div>
                    </div>
                    <button
                      onClick={() => navigate(`/cases/${projectId}/improve`)}
                      className="btn-primary !py-2 !px-4 text-xs shrink-0 flex items-center gap-1.5 shadow-sm self-start sm:self-center"
                    >
                      <Sparkles size={14} /> 前往改写补丁 (Improve)
                    </button>
                  </div>
                </div>
              )}

              {/* Evidence trace: make the product's core promise visible before the raw details. */}
              <div className="mt-6 rounded-lg border border-hairline bg-[#f7f6f2] p-4 md:p-5 dark:border-white/[0.08] dark:bg-white/[0.03]">
                <div className="flex items-start justify-between gap-4">
                  <div>
                    <div className="kicker mb-1.5 flex items-center gap-1.5"><Network size={12} /> Evidence trace</div>
                    <p className="text-xs text-neutral-500 leading-relaxed dark:text-white/80">这项材料如何从公开论文，传导到你的 Claim，再变成下一步判断。</p>
                  </div>
                  <span className="locator shrink-0">{paper.claimIds.length} 个 Claim</span>
                </div>
                <div className="mt-4 grid gap-2 md:grid-cols-[1fr_auto_1fr_auto_1fr] items-stretch">
                  <div className="rounded-md border border-hairline bg-white p-3 min-w-0 dark:border-white/[0.08] dark:bg-white/[0.04]">
                    <div className="locator mb-2">01 · 公开论文</div>
                    <p className="text-xs text-neutral-700 leading-relaxed line-clamp-3 dark:text-white/95">{paper.title}</p>
                  </div>
                  <div className="hidden md:flex items-center justify-center text-neutral-300 dark:text-white/60"><ArrowRight size={16} /></div>
                  <div className="rounded-md border border-teal/20 bg-teal-soft/45 p-3 min-w-0 dark:border-[#4E8AFB]/25 dark:bg-[#4E8AFB]/10">
                    <div className="locator mb-2 !text-teal">02 · 论文 Claim</div>
                    <div className="flex flex-wrap gap-1.5">
                      {paper.claimIds.map((claimId) => <span key={claimId} className="badge-teal font-mono">{claimId}</span>)}
                    </div>
                    <p className="mt-2 text-xs text-neutral-600 leading-relaxed line-clamp-2 dark:text-white/90">{linkedClaim?.text ?? '关联的 Claim 将在确认后显示。'}</p>
                  </div>
                  <div className="hidden md:flex items-center justify-center text-neutral-300 dark:text-white/60"><ArrowRight size={16} /></div>
                  <div className="rounded-md border border-hairline bg-white p-3 min-w-0 dark:border-white/[0.08] dark:bg-white/[0.04] flex flex-col justify-between">
                    <div>
                      <div className="locator mb-2">03 · 下一步</div>
                      <p className="text-xs text-neutral-700 leading-relaxed line-clamp-3 dark:text-white/95">{linkedAction?.title ?? paper.suggestion}</p>
                      {linkedAction && <div className="mt-2 flex items-center gap-1 text-[10px] text-teal"><CheckCircle2 size={12} /> 已进入行动队列</div>}
                    </div>
                    <button
                      onClick={() => navigate(`/cases/${projectId}/improve`)}
                      className="mt-3 inline-flex items-center gap-1 text-[11px] font-medium text-teal hover:underline self-start"
                    >
                      <Sparkles size={12} /> 在改造工作台中起草改写 <ArrowRight size={11} />
                    </button>
                  </div>
                </div>
              </div>

              <div className="mt-6">
                <Tabs tabs={['比较矩阵', '量化基准对比', '证据', '决策']} active={tab} onChange={setTab} />

                {tab === 0 && (
                  <div className="overflow-x-auto">
                    <table className="table-base min-w-[560px]">
                      <thead>
                        <tr>
                          <th>字段</th>
                          <th>本方</th>
                          <th>公开论文</th>
                          <th>状态</th>
                        </tr>
                      </thead>
                      <tbody>
                        {paper.matrix.map((r) => (
                          <tr
                            key={r.field}
                            className={
                              r.status === 'diff'
                                ? 'bg-red-50/40 dark:bg-red-500/[0.06]'
                                : r.status === 'partial'
                                ? 'bg-amber-50/30 dark:bg-amber-500/[0.04]'
                                : ''
                            }
                          >
                            <td className="font-medium whitespace-nowrap dark:text-white flex items-center gap-1.5">
                              {r.status === 'diff' && <span className="w-1.5 h-1.5 rounded-full bg-red-500 shrink-0" />}
                              {r.status === 'partial' && <span className="w-1.5 h-1.5 rounded-full bg-amber-500 shrink-0" />}
                              {r.field}
                            </td>
                            <td className="text-neutral-600 dark:text-white/90">{r.ours}</td>
                            <td className="text-neutral-600 dark:text-white/90">{r.theirs}</td>
                            <td>
                              <span className={matrixStatusMeta[r.status].cls}>{matrixStatusMeta[r.status].label}</span>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}

                {tab === 1 && (
                  <div className="pt-2">
                    <BenchmarkComparison data={paper.benchmarkComparison} paperTitle={paper.title} />
                  </div>
                )}

                {tab === 2 && (
                  <div className="grid gap-6">
                    <Quote label="你的文稿" loc={paper.yourLoc}>
                      {paper.yourQuote}
                    </Quote>
                    <Quote label="公开论文" loc={paper.quoteLoc} against={paper.verdict === 'challenge'}>
                      {paper.quote}
                    </Quote>
                  </div>
                )}

                {tab === 3 && (
                  <div className="grid gap-5">
                    {/* Reviewer 2 Adversarial Critique & Defense Strategy */}
                    {(paper.lethalReviewerQuestion || (paper.defenseStrategy && Object.keys(paper.defenseStrategy).length > 0)) && (
                      <div className="rounded-lg border border-red-200 bg-red-50/60 p-4 dark:border-red-500/20 dark:bg-red-500/[0.06]">
                        <div className="flex items-center gap-2 mb-2">
                          <span className="badge-red text-[11px]">Reviewer 2 对抗质疑</span>
                          {paper.impactType && <span className="badge-gray font-mono text-[10px]">{paper.impactType}</span>}
                        </div>
                        {paper.lethalReviewerQuestion && (
                          <p className="text-xs text-red-900 font-medium dark:text-red-200 leading-relaxed mb-3">
                            “{paper.lethalReviewerQuestion}”
                          </p>
                        )}
                        {paper.defenseStrategy && (
                          <div className="space-y-2 text-xs text-neutral-700 dark:text-white/85">
                            {typeof paper.defenseStrategy.framing_shift === 'string' && (
                              <div>
                                <span className="font-semibold text-teal">话术迁移策略：</span>
                                <span>{paper.defenseStrategy.framing_shift}</span>
                              </div>
                            )}
                            {Array.isArray(paper.defenseStrategy.required_experiments) && paper.defenseStrategy.required_experiments.length > 0 && (
                              <div>
                                <span className="font-semibold text-neutral-800 dark:text-white">建议防御性消融实验：</span>
                                <ul className="list-disc list-inside mt-1 text-neutral-600 dark:text-white/80 space-y-0.5">
                                  {paper.defenseStrategy.required_experiments.map((exp: unknown, idx: number) => (
                                    <li key={idx}>{String(exp)}</li>
                                  ))}
                                </ul>
                              </div>
                            )}
                          </div>
                        )}
                      </div>
                    )}

                    <div>
                      <div className="kicker mb-2">为什么重要</div>
                      <p className="text-sm text-neutral-600 leading-relaxed dark:text-white/90">{paper.why}</p>
                    </div>
                    <div className="grid md:grid-cols-2 gap-5">
                      <div>
                        <div className="kicker mb-2">建议动作</div>
                        <p className="text-sm text-neutral-600 leading-relaxed dark:text-white/90">{paper.suggestion}</p>
                      </div>
                      <div>
                        <div className="kicker mb-2">不确定因素</div>
                        <p className="text-sm text-neutral-500 leading-relaxed dark:text-white/85">{paper.uncertainty}</p>
                      </div>
                    </div>
                    <div className="pt-5 hairline-t">
                      {decisionError && (
                        <div className="mb-4 rounded-lg border border-red-200 bg-red-50 px-3.5 py-2.5 text-[12.5px] text-red-700 dark:border-red-500/25 dark:bg-red-500/10 dark:text-red-300">
                          {decisionError}
                        </div>
                      )}
                      <div className="flex flex-wrap items-center gap-2">
                        {!decisions[paper.id] && (
                          <>
                            <button
                              className="btn-primary"
                              disabled={savingDecision === paper.id}
                              onClick={() => void decide(paper.id, 'adopted')}
                            >
                              {savingDecision === paper.id ? '保存中…' : '采用这项影响'}
                            </button>
                            <button
                              className="btn-quiet"
                              disabled={savingDecision === paper.id}
                              onClick={() => void decide(paper.id, 'rejected')}
                            >
                              不采用
                            </button>
                          </>
                        )}
                        {decisions[paper.id] === 'adopted' && (
                          <>
                            <span className="badge-green badge-dot">已采用 · 当前判断：{verdictMeta[paper.verdict].label}</span>
                            <button className="btn-quiet ml-auto" onClick={() => setDecisions((m) => { const n = { ...m }; delete n[paper.id]; return n; })}>
                              修改判断
                            </button>
                          </>
                        )}
                        {decisions[paper.id] === 'rejected' && (
                          <>
                            <span className="badge-gray badge-dot">已标记不采用</span>
                            <button className="btn-quiet ml-auto" onClick={() => setDecisions((m) => { const n = { ...m }; delete n[paper.id]; return n; })}>
                              修改判断
                            </button>
                          </>
                        )}
                      </div>
                    </div>
                  </div>
                )}
              </div>
            </div>
            <AuthorLabDrawer paper={paper} open={labProfileOpen} onOpenChange={setLabProfileOpen} />
          </Reveal>
        )}
      </div>
    </div>
  );
}
