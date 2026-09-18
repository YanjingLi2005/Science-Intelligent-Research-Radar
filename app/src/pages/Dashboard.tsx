import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router';
import { ArrowUpRight, Radar, ShieldAlert } from 'lucide-react';
import { Bar, BarChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { Reveal, SectionHead, Stat } from '../components/chrome';
import FidelityPanel from '../components/FidelityPanel';
import { useProject } from '../contexts/ProjectContext';
import type { PageKey } from '../App';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '../components/ui/dialog';
import { downloadWritingBrief, exportBibtex, getDigest, getMonitoringStats, getCostAlert, getCostBySource, setCostAlert, testCostAlert, getNotificationHistory, getDigestWebhook, setDigestWebhook, testDigestWebhook, getScanWebhook, setScanWebhook, getEmailNotify, setEmailNotify, testEmailNotify, listScans, getSources, getSourceDetail, setSourceTags, setSourceBulkTags, removeSources, importSource, useApi, type ScanStatus, type CostAlertConfig, type DigestWebhookConfig, type ScanWebhookConfig, type EmailNotifyConfig, type SourceDetail } from '../api';
import { claimStatusMeta } from '../data/mock';
import ErrorRetry from '../components/ErrorRetry';

function scanMeta(status: ScanStatus) {
  switch (status.status) {
    case 'completed':
      return { dot: 'green', label: '完成' };
    case 'running':
      return { dot: 'blue pulse', label: '扫描中' };
    case 'failed':
    case 'interrupted':
      return { dot: 'red', label: '失败' };
    case 'cancelled':
      return { dot: 'amber', label: '已取消' };
    default:
      return { dot: 'gray', label: status.status };
  }
}

export default function Dashboard() {
  const navigate = useNavigate();
  const { project, loading, error, projectId, refreshProject } = useProject();
  const [notificationFilter, setNotificationFilter] = useState('');
  const [sourceKindFilter, setSourceKindFilter] = useState('');
  const [ccfRankFilter, setCcfRankFilter] = useState('');
  const [tagFilter, setTagFilter] = useState('');
  const [sourceOffset, setSourceOffset] = useState(0);
  const [selectedSourceIds, setSelectedSourceIds] = useState<string[]>([]);
  const [removingSources, setRemovingSources] = useState(false);
  const [sourceRemovalError, setSourceRemovalError] = useState<string | null>(null);
  const [bulkSourceTagsInput, setBulkSourceTagsInput] = useState('');
  const [bulkSourceTagsSaving, setBulkSourceTagsSaving] = useState(false);
  const [bulkSourceTagsError, setBulkSourceTagsError] = useState<string | null>(null);
  const [selectedSourceId, setSelectedSourceId] = useState<string | null>(null);
  const [sourceDetail, setSourceDetail] = useState<SourceDetail | null>(null);
  const [sourceDetailLoading, setSourceDetailLoading] = useState(false);
  const [sourceDetailError, setSourceDetailError] = useState<Error | null>(null);
  const [sourceTagsInput, setSourceTagsInput] = useState('');
  const [sourceTagsSaving, setSourceTagsSaving] = useState(false);
  const [sourceTagsError, setSourceTagsError] = useState<string | null>(null);
  const [showSourceImport, setShowSourceImport] = useState(false);
  const [sourceImportUrl, setSourceImportUrl] = useState('');
  const [sourceImportDoi, setSourceImportDoi] = useState('');
  const [sourceImporting, setSourceImporting] = useState(false);
  const [sourceImportError, setSourceImportError] = useState<string | null>(null);
  const { data: scans } = useApi(() => listScans(projectId), [projectId]);
  const {
    data: sources,
    loading: sourcesLoading,
    error: sourcesError,
    refetch: refetchSources,
  } = useApi(
    () =>
      getSources(projectId, {
        limit: 10,
        offset: sourceOffset,
        source_kind: sourceKindFilter || undefined,
        ccf_rank: ccfRankFilter || undefined,
        tag: tagFilter || undefined,
      }),
    [projectId, sourceKindFilter, ccfRankFilter, tagFilter, sourceOffset],
  );
  const { data: monitoringStats } = useApi(() => getMonitoringStats(projectId), [projectId]);
  const {
    data: costBySource,
    loading: costBySourceLoading,
    error: costBySourceError,
  } = useApi(() => getCostBySource(projectId), [projectId]);
  const { data: notifications } = useApi(
    () => getNotificationHistory(projectId, notificationFilter || undefined),
    [projectId, notificationFilter],
  );
  const go = (page: PageKey) => navigate(`/cases/${projectId}/${page === 'dashboard' ? '' : page}`);
  const [briefState, setBriefState] = useState<'idle' | 'busy' | 'done' | 'error'>('idle');
  const [digestState, setDigestState] = useState<'idle' | 'busy' | 'done' | 'error'>('idle');
  const [digestSections, setDigestSections] = useState<string[]>(['scan', 'deep', 'impacts', 'actions']);
  const [costAlert, setCostAlertCfg] = useState<CostAlertConfig | null>(null);
  const [costAlertSaving, setCostAlertSaving] = useState(false);
  const [costAlertTesting, setCostAlertTesting] = useState(false);
  const [digestWebhook, setDigestWebhookCfg] = useState<DigestWebhookConfig | null>(null);
  const [digestWebhookSaving, setDigestWebhookSaving] = useState(false);
  const [digestTesting, setDigestTesting] = useState(false);
  const { data: scanWebhookData } = useApi(() => getScanWebhook(projectId), [projectId]);
  const [scanWebhook, setScanWebhookCfg] = useState<ScanWebhookConfig | null>(null);
  const [scanWebhookSaving, setScanWebhookSaving] = useState(false);
  const [emailNotify, setEmailNotifyCfg] = useState<EmailNotifyConfig | null>(null);
  const [emailNotifySaving, setEmailNotifySaving] = useState(false);
  const [emailNotifyTesting, setEmailNotifyTesting] = useState(false);

  useEffect(() => {
    setScanWebhookCfg(scanWebhookData);
  }, [scanWebhookData]);

  const openSourceDetail = async (sourceId: string) => {
    setSelectedSourceId(sourceId);
    setSourceDetail(null);
    setSourceDetailError(null);
    setSourceTagsInput('');
    setSourceTagsError(null);
    setSourceDetailLoading(true);
    try {
      const detail = await getSourceDetail(projectId, sourceId);
      setSourceDetail(detail);
    } catch (caught) {
      setSourceDetailError(caught instanceof Error ? caught : new Error(String(caught)));
    } finally {
      setSourceDetailLoading(false);
    }
  };

  const handleImportSource = async () => {
    if (sourceImporting) return;
    const url = sourceImportUrl.trim();
    const doi = sourceImportDoi.trim();
    if (!url && !doi) {
      setSourceImportError('请输入 DOI 或论文 URL');
      return;
    }
    setSourceImporting(true);
    setSourceImportError(null);
    try {
      await importSource(projectId, { url: url || undefined, doi: doi || undefined });
      setSourceImportUrl('');
      setSourceImportDoi('');
      setShowSourceImport(false);
      refetchSources();
    } catch (caught) {
      setSourceImportError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setSourceImporting(false);
    }
  };

  const closeSourceDetail = () => {
    setSelectedSourceId(null);
    setSourceDetail(null);
    setSourceDetailError(null);
    setSourceTagsInput('');
    setSourceTagsError(null);
  };

  const handleSaveSourceTags = async () => {
    if (!sourceDetail || sourceTagsSaving) return;
    const addedTags = sourceTagsInput
      .split(',')
      .map((tag) => tag.trim())
      .filter(Boolean);
    const tags = Array.from(new Set([...(sourceDetail.tags ?? []), ...addedTags]));
    setSourceTagsSaving(true);
    setSourceTagsError(null);
    try {
      const result = await setSourceTags(projectId, sourceDetail.id, tags);
      setSourceDetail((current) => (current ? { ...current, tags: result.tags } : current));
      setSourceTagsInput('');
      refetchSources();
    } catch (caught) {
      setSourceTagsError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setSourceTagsSaving(false);
    }
  };

  const handleRemoveSelectedSources = async () => {
    if (selectedSourceIds.length === 0 || removingSources) return;
    setRemovingSources(true);
    setSourceRemovalError(null);
    try {
      await removeSources(projectId, selectedSourceIds);
      setSelectedSourceIds([]);
      refetchSources();
    } catch (caught) {
      setSourceRemovalError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setRemovingSources(false);
    }
  };

  const handleBulkTagSelectedSources = async () => {
    if (selectedSourceIds.length === 0 || bulkSourceTagsSaving) return;
    const tags = Array.from(
      new Set(
        bulkSourceTagsInput
          .split(',')
          .map((tag) => tag.trim())
          .filter(Boolean),
      ),
    );
    if (tags.length === 0) {
      setBulkSourceTagsError('请输入至少一个标签');
      return;
    }
    setBulkSourceTagsSaving(true);
    setBulkSourceTagsError(null);
    try {
      await setSourceBulkTags(projectId, selectedSourceIds, tags);
      setSelectedSourceIds([]);
      setBulkSourceTagsInput('');
      refetchSources();
    } catch (caught) {
      setBulkSourceTagsError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBulkSourceTagsSaving(false);
    }
  };

  useEffect(() => {
    if (!projectId) return;
    getCostAlert(projectId)
      .then(setCostAlertCfg)
      .catch(() => setCostAlertCfg(null));
    getDigestWebhook(projectId)
      .then(setDigestWebhookCfg)
      .catch(() => setDigestWebhookCfg(null));
    getEmailNotify(projectId)
      .then(setEmailNotifyCfg)
      .catch(() => setEmailNotifyCfg(null));
  }, [projectId]);

  // ---- derived stats ----
  const claimStats = useMemo(() => {
    if (!project) return { confirmed: 0, pending: 0, disputed: 0, valid: 0, revalidate: 0 };
    return {
      confirmed: project.claims.filter((c) => c.confirmed === 'yes').length,
      pending: project.claims.filter((c) => c.confirmed === 'pending').length,
      disputed: project.claims.filter((c) => c.status === 'disputed').length,
      valid: project.claims.filter((c) => c.status === 'valid' || c.status === 'supported').length,
      revalidate: project.claims.filter((c) => c.status === 'revalidate').length,
    };
  }, [project]);

  const urgentActions = useMemo(() => {
    if (!project) return [];
    return project.actions.filter((a) => a.priority === 'P0' || a.priority === 'P1').slice(0, 5);
  }, [project]);

  const paperStats = useMemo(() => {
    if (!project) return { total: 0, compared: 0, impacted: 0, urgent: 0 };
    return {
      total: project.papers.length,
      compared: project.papers.length,
      impacted: project.papers.filter((p) => p.verdict !== 'none').length,
      urgent: project.papers.filter((p) => p.urgency === 'urgent').length,
    };
  }, [project]);

  const scanStatusCounts = useMemo(() => {
    const counts: Record<string, number> = {};
    (scans ?? []).forEach((s) => {
      counts[s.status] = (counts[s.status] ?? 0) + 1;
    });
    return counts;
  }, [scans]);

  const scanStatusChartData = useMemo(
    () => Object.entries(scanStatusCounts).map(([name, value]) => ({ name, value })),
    [scanStatusCounts],
  );

  const scanTrendData = useMemo(
    () =>
      (scans ?? [])
        .slice()
        .reverse()
        .map((s) => ({
          name: s.started_at ? new Date(s.started_at).toLocaleDateString() : '?',
          papers: typeof s.stats?.scanned_papers === 'number' ? s.stats.scanned_papers : 0,
          impacts: typeof s.stats?.impact_candidates === 'number' ? s.stats.impact_candidates : 0,
        })),
    [scans],
  );

  const scanDurationData = useMemo(
    () =>
      (scans ?? [])
        .slice()
        .reverse()
        .map((s) => {
          const start = s.started_at ? new Date(s.started_at).getTime() : 0;
          const end = s.finished_at ? new Date(s.finished_at).getTime() : start;
          const minutes = start && end ? Math.max(0, Math.round((end - start) / 60000)) : 0;
          return {
            name: s.started_at ? new Date(s.started_at).toLocaleDateString() : '?',
            minutes,
          };
        }),
    [scans],
  );

  const scanCostData = useMemo(
    () =>
      (scans ?? [])
        .slice()
        .reverse()
        .map((s) => ({
          name: s.started_at ? new Date(s.started_at).toLocaleDateString() : '?',
          cost: s.estimated_cost ?? 0,
        })),
    [scans],
  );

  const costBySourceChartData = useMemo(
    () => (costBySource?.items ?? []).map((item) => ({ name: item.source_kind, cost: item.cost_usd })),
    [costBySource],
  );

  const affectedClaimCount = useMemo(() => {
    if (!project) return 0;
    return new Set(
      project.papers
        .filter((paper) => paper.verdict !== 'none')
        .flatMap((paper) => paper.claimIds),
    ).size;
  }, [project]);

  const riskClaims = useMemo(() => {
    if (!project) return [];
    return project.claims
      .filter(
        (claim) =>
          claim.status === 'disputed' ||
          claim.status === 'revalidate' ||
          claim.evidence.some((evidence) => evidence.kind === 'challenge'),
      )
      .slice(0, 3);
  }, [project]);

  const citation = project?.citationHealth;
  const receipts = project?.retrievalReceipts ?? [];

  const exportBrief = async () => {
    if (!project) return;
    setBriefState('busy');
    try {
      const markdown = await downloadWritingBrief(project.id);
      const blob = new Blob([markdown], { type: 'text/markdown;charset=utf-8' });
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = `${project.short || 'research-radar'}-writing-brief.md`;
      anchor.click();
      URL.revokeObjectURL(url);
      setBriefState('done');
    } catch {
      setBriefState('error');
    }
  };

  const saveCostAlert = async (enabled: boolean, budget?: number, webhook?: string) => {
    if (!project || !costAlert) return;
    setCostAlertSaving(true);
    try {
      const cfg = await setCostAlert(project.id, {
        enabled,
        monthly_budget_usd: budget ?? costAlert.monthly_budget_usd,
        webhook_url: webhook ?? costAlert.webhook_url ?? '',
      });
      setCostAlertCfg(cfg);
    } catch {
      // best-effort
    } finally {
      setCostAlertSaving(false);
    }
  };

  const handleTestCostAlert = async () => {
    if (!project) return;
    setCostAlertTesting(true);
    try {
      const result = await testCostAlert(project.id);
      alert(`成本告警测试已发送到 Webhook：${result.webhook_url}`);
    } catch (e) {
      alert(`测试发送失败：${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setCostAlertTesting(false);
    }
  };

  const saveDigestWebhook = async (enabled: boolean, webhook?: string, schedule?: string) => {
    if (!project || !digestWebhook) return;
    setDigestWebhookSaving(true);
    try {
      const cfg = await setDigestWebhook(project.id, {
        enabled,
        webhook_url: webhook ?? digestWebhook.webhook_url,
        schedule: schedule ?? digestWebhook.schedule,
      });
      setDigestWebhookCfg(cfg);
    } catch {
      // best-effort
    } finally {
      setDigestWebhookSaving(false);
    }
  };

  const saveScanWebhook = async (enabled: boolean, webhook?: string, notifyOn?: string) => {
    if (!project || !scanWebhook) return;
    setScanWebhookSaving(true);
    try {
      const cfg = await setScanWebhook(project.id, {
        enabled,
        webhook_url: webhook ?? scanWebhook.webhook_url,
        notify_on: notifyOn ?? scanWebhook.notify_on,
      });
      setScanWebhookCfg(cfg);
    } catch {
      // best-effort
    } finally {
      setScanWebhookSaving(false);
    }
  };

  const handleTestDigestWebhook = async () => {
    if (!project) return;
    setDigestTesting(true);
    try {
      await testDigestWebhook(project.id);
      alert('测试周报已发送到 Webhook');
    } catch (e) {
      alert(`测试发送失败：${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setDigestTesting(false);
    }
  };

  const saveEmailNotify = async (
    enabled: boolean,
    fields?: Partial<EmailNotifyConfig>,
  ) => {
    if (!project || !emailNotify) return;
    setEmailNotifySaving(true);
    try {
      const cfg = await setEmailNotify(project.id, {
        enabled,
        smtp_host: fields?.smtp_host ?? emailNotify.smtp_host,
        smtp_port: fields?.smtp_port ?? emailNotify.smtp_port,
        username: fields?.username ?? emailNotify.username,
        password: fields?.password ?? emailNotify.password,
        recipient: fields?.recipient ?? emailNotify.recipient,
        subject_prefix: fields?.subject_prefix ?? emailNotify.subject_prefix,
      });
      setEmailNotifyCfg(cfg);
    } catch (e) {
      alert(`保存邮件配置失败：${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setEmailNotifySaving(false);
    }
  };

  const handleTestEmailNotify = async () => {
    if (!project) return;
    setEmailNotifyTesting(true);
    try {
      const result = await testEmailNotify(project.id);
      alert(`测试邮件已发送到：${result.recipient}`);
    } catch (e) {
      alert(`测试发送失败：${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setEmailNotifyTesting(false);
    }
  };

  const exportDigest = async () => {
    if (!project) return;
    setDigestState('busy');
    try {
      const digest = await getDigest(project.id, digestSections);
      const blob = new Blob([digest.markdown], { type: 'text/markdown;charset=utf-8' });
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = `${project.short || 'research-radar'}-radar-digest.md`;
      anchor.click();
      URL.revokeObjectURL(url);
      setDigestState('done');
    } catch {
      setDigestState('error');
    }
  };

  const downloadJSON = (data: unknown, filename: string) => {
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = filename;
    anchor.click();
    URL.revokeObjectURL(url);
  };

  const downloadBibtex = async () => {
    if (!project) return;
    const bibtex = await exportBibtex(project.id);
    const blob = new Blob([bibtex.content], { type: 'application/x-bibtex;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = bibtex.filename.endsWith('.bib') ? bibtex.filename : `${bibtex.filename}.bib`;
    anchor.click();
    URL.revokeObjectURL(url);
  };

  const downloadMarkdown = (data: unknown, filename: string) => {
    const markdown = `\`\`\`json\n${JSON.stringify(data, null, 2)}\n\`\`\`\n`;
    const blob = new Blob([markdown], { type: 'text/markdown;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = filename;
    anchor.click();
    URL.revokeObjectURL(url);
  };

  // ----

  if (loading) {
    return (
      <div aria-busy="true" aria-live="polite" className="space-y-6">
        <div className="space-y-2">
          <div className="h-3 w-24 rounded bg-neutral-200/70 dark:bg-white/10 animate-pulse" />
          <div className="h-7 w-64 rounded bg-neutral-200/70 dark:bg-white/10 animate-pulse" />
        </div>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-5">
          {Array.from({ length: 4 }, (_, i) => (
            <div key={i} className="card p-5 h-24 animate-pulse bg-neutral-100/70 dark:bg-white/5" />
          ))}
        </div>
        <div className="card p-7 h-40 animate-pulse bg-neutral-100/70 dark:bg-white/5" />
      </div>
    );
  }

  if (error || !project) {
    return (
      <div className="card p-8 text-center">
        <ErrorRetry message={error?.message ?? '未找到项目'} onRetry={() => void refreshProject()} />
      </div>
    );
  }

  return (
    <div>
      <Reveal>
        <SectionHead
          kicker="情报板 · Intelligence Board"
          title={project.name}
          right={
            <div className="flex items-center gap-2">
              <button className="btn-secondary shrink-0" onClick={() => go('ops')}>运维</button>
              <button className="btn-secondary shrink-0" onClick={() => go('integrity')}><ShieldAlert size={14} /> 诚信雷达</button>
              <button className="btn-primary shrink-0" onClick={() => go('radar')}>
                <Radar size={14} /> 立即扫描
              </button>
            </div>
          }
        />
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[12.5px] text-neutral-500 dark:text-white/80">
          <span>{project.version}</span>
          <span>·</span>
          <span>{project.file}</span>
          <span>·</span>
          <span>上次扫描 {project.lastScan}</span>
        </div>
        <p className="mt-2 max-w-2xl text-sm text-neutral-500 dark:text-white/85 leading-relaxed">
          雷达持续关注公开文献，这里是最近一次情报更新：哪些新证据改变了你的 Claim，下一步最该做什么。
        </p>
      </Reveal>

      {/* Key metrics */}
      <Reveal delay={60}>
        <div className="mt-8 grid grid-cols-2 md:grid-cols-4 gap-4 pb-8 hairline-b">
          <div className="card p-5">
            <Stat value={`${claimStats.confirmed}/${project.claimsTotal}`} label="已确认 Claim" tone="teal" />
          </div>
          <div className="card p-5">
            <Stat value={claimStats.disputed} label="需要复核 Claim" tone={claimStats.disputed > 0 ? 'red' : 'ink'} />
          </div>
          <div className="card p-5">
            <Stat value={project.urgent} label="紧急影响" tone={project.urgent > 0 ? 'red' : 'ink'} />
          </div>
          <div className="card p-5">
            <Stat value={paperStats.impacted} label="发现影响材料" tone="ink" />
          </div>
        </div>
      </Reveal>

      {/* Scan history — the continuous-watch log */}
      <Reveal delay={80}>
        <div className="mt-8">
          <SectionHead
            kicker="Radar Log"
            title="扫描历史"
            right={<span className="locator">{scans?.length ?? 0} 次扫描</span>}
          />
          <div className="card p-6">
            {!scans || scans.length === 0 ? (
              <p className="text-sm text-neutral-400 dark:text-white/75 py-4 text-center">
                还没有扫描记录 —— 雷达从第一次扫描开始记录每一次情报更新
              </p>
            ) : (
              <ol className="timeline">
                {scans.slice(0, 8).map((scan) => {
                  const meta = scanMeta(scan);
                  const started = scan.started_at ? new Date(scan.started_at).toLocaleString('zh-CN', { hour12: false }) : '—';
                  const scanned = (scan.stats as Record<string, unknown>)?.scanned_papers;
                  const impacts = (scan.stats as Record<string, unknown>)?.impact_count;
                  return (
                    <li key={scan.id} className={`timeline-item ${meta.dot.includes('green') ? 'pass' : meta.dot.includes('amber') ? 'warn' : meta.dot.includes('red') ? 'fail' : ''}`}>
                      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 min-w-0">
                        <span className="status-dot shrink-0 mt-[3px] -ml-1 hidden" />
                        <span className={`text-[13px] font-medium ${scan.status === 'running' ? 'text-teal' : 'text-ink dark:text-white'}`}>
                          {scan.status === 'running' && <span className="blink-soft">● </span>}
                          {scan.status === 'running' ? '雷达扫描中…' : `雷达扫描 · ${meta.label}`}
                        </span>
                        <span className="locator">{started}</span>
                        <span className="locator">{scan.mode === 'auto_public_paper_radar' ? 'auto' : scan.mode}</span>
                        <span className="ml-auto flex items-center gap-2 text-[11px] text-neutral-400 dark:text-white/80">
                          {typeof scanned === 'number' && <span>{scanned} 篇论文</span>}
                          {typeof impacts === 'number' && <span>· {impacts} 条影响</span>}
                          {scan.status === 'running' && scan.progress.message && (
                            <span className="truncate max-w-[220px]">{scan.progress.message}</span>
                          )}
                          {scan.status === 'failed' && scan.error_message && (
                            <span className="truncate max-w-[260px] text-red-600 dark:text-red-300">{scan.error_message}</span>
                          )}
                        </span>
                      </div>
                    </li>
                  );
                })}
              </ol>
            )}
          </div>
        </div>
      </Reveal>

      {/* Claim-centered risk readout */}
      <Reveal delay={90}>
        <div className="mt-8 card p-7 md:p-9">
          <div className="flex flex-col md:flex-row md:items-start md:justify-between gap-6">
            <div className="min-w-0">
              <div className="kicker mb-3">Risk readout</div>
              <h2 className="text-[17px] font-semibold tracking-tight leading-snug">
                {paperStats.impacted > 0
                  ? `最近情报显示 ${paperStats.impacted} 条可能影响论文的证据`
                  : '还没有发现会改变论文结论的证据'}
              </h2>
              <p className="mt-3 text-sm text-neutral-500 dark:text-white/85 leading-relaxed max-w-2xl">
                {paperStats.impacted > 0
                  ? `这些材料关联 ${affectedClaimCount} 个 Claim。先核验原文证据，再决定是否补实验、补引用或收窄表述。`
                  : '完成一次雷达扫描后，这里会显示支持、挑战、边界条件和在先工作的变化。'}
              </p>
            </div>
            <button className="btn-primary shrink-0" onClick={() => go('actions')}>
              查看证据与行动 <ArrowUpRight size={14} />
            </button>
          </div>
          {riskClaims.length > 0 && (
            <div className="mt-6 pt-5 hairline-t grid gap-3">
              {riskClaims.map((claim) => (
                <button
                  key={claim.id}
                  className="text-left flex items-start gap-3 group"
                  onClick={() => go('actions')}
                >
                  <span className="locator mt-0.5">{claim.id}</span>
                  <span className="flex-1 text-sm text-neutral-600 dark:text-white/90 group-hover:text-ink dark:group-hover:text-white transition-colors">{claim.text}</span>
                  <span className={claimStatusMeta[claim.status].cls}>{claimStatusMeta[claim.status].label}</span>
                </button>
              ))}
            </div>
          )}
        </div>
      </Reveal>

      {/* Urgent actions */}
      {urgentActions.length > 0 && (
        <Reveal delay={100}>
          <div className="mt-8">
            <SectionHead kicker="Actions" title="本周紧急行动" />
            <div className="card overflow-hidden">
              {urgentActions.map((a) => (
                <div key={a.id} className="px-6 py-4 hairline-b last:border-none flex items-start gap-4">
                  <span className={`mt-0.5 shrink-0 badge-dot ${a.priority === 'P0' ? 'badge-red' : 'badge-orange'}`}>
                    {a.priority}
                  </span>
                  <div className="flex-1 min-w-0">
                    <p className="text-sm text-neutral-700 dark:text-white/95">{a.title}</p>
                    {a.reason && (
                      <p className="text-xs text-neutral-400 dark:text-white/80 mt-1 line-clamp-2">{a.reason}</p>
                    )}
                  </div>
                  <span className="text-xs text-neutral-400 dark:text-white/80 shrink-0">{a.due}</span>
                </div>
              ))}
            </div>
            <div className="mt-3 text-right">
              <button className="btn-ghost text-xs" onClick={() => go('actions')}>
                查看全部行动 →
              </button>
            </div>
          </div>
        </Reveal>
      )}

      {/* P0: evidence-fidelity measurement from the latest scan */}
      <Reveal delay={110}>
        <div className="mt-8">
          <FidelityPanel fidelity={project.fidelity} />
        </div>
      </Reveal>

      {/* Provenance and citation health */}
      <Reveal delay={120}>
        <div className="mt-8 grid md:grid-cols-2 gap-6">
          <div className="card p-6">
            <div className="kicker mb-3">Citation Health</div>
            <div className="flex items-end justify-between gap-4">
              <div>
                <div className="num-display text-teal">
                  {Math.round((citation?.metadataCoverage ?? 1) * 100)}%
                </div>
                <div className="text-xs text-neutral-400 dark:text-white/80 mt-1">本地来源元数据覆盖</div>
              </div>
              <div className="text-right text-xs text-neutral-500 dark:text-white/85">
                <div>{citation?.referenceEntries ?? 0} 条参考文献</div>
                <div className="mt-1">{citation?.issues.length ?? 0} 个待处理信号</div>
              </div>
            </div>
            {(citation?.integrity?.retracted ?? 0) > 0 && (
              <p className="mt-4 text-xs text-red-600 dark:text-red-300">发现撤稿来源，请优先复核相关 Claim。</p>
            )}
          </div>
          <div className="card p-6">
            <div className="flex items-center justify-between gap-3">
              <div className="kicker">Retrieval Receipts</div>
              <span className="text-xs text-neutral-400 dark:text-white/80">{receipts.length} 条记录</span>
            </div>
            <div className="mt-3 space-y-2">
              {receipts.slice(0, 3).map((receipt) => (
                <div key={receipt.id} className="flex items-center justify-between gap-3 text-xs">
                  <span className="font-mono text-neutral-500 dark:text-white/85">{receipt.sourceKind}</span>
                  <span className={receipt.status === 'success' ? 'text-teal' : 'text-red-500 dark:text-red-400'}>
                    {receipt.retrievedCount}/{receipt.requestedCount}
                  </span>
                </div>
              ))}
              {receipts.length === 0 && <span className="text-xs text-neutral-400 dark:text-white/80">完成一次扫描后显示可复现检索记录</span>}
            </div>
          </div>
        </div>
      </Reveal>

      {/* Scan summary + Quick links */}
      <Reveal delay={130}>
        <div className="mt-8 grid md:grid-cols-2 gap-6">
          {/* Recent scan */}
          <div className="card p-6">
            <div className="kicker mb-3">最近扫描</div>
            <p className="text-sm text-neutral-500 dark:text-white/85">{project.lastScan}</p>
            <div className="mt-4 grid grid-cols-2 gap-3">
              <div>
                <div className="text-base font-semibold tracking-tight tabular-nums">{paperStats.total}</div>
                <div className="text-xs text-neutral-400 dark:text-white/80">发现论文</div>
              </div>
              <div>
                <div className="text-base font-semibold tracking-tight tabular-nums">{paperStats.impacted}</div>
                <div className="text-xs text-neutral-400 dark:text-white/80">材料影响</div>
              </div>
              <div>
                <div className="text-base font-semibold tracking-tight tabular-nums">{paperStats.urgent}</div>
                <div className="text-xs text-neutral-400 dark:text-white/80">紧急</div>
              </div>
              <div>
                <div className="text-base font-semibold tracking-tight tabular-nums">{project.actions.length}</div>
                <div className="text-xs text-neutral-400 dark:text-white/80">行动建议</div>
              </div>
            </div>
          </div>

          {/* Quick actions */}
          <div className="card p-6 flex flex-col gap-3">
            <div className="kicker mb-1">快捷操作</div>
            <button className="btn-primary w-full justify-start" onClick={() => go('radar')}>
              <Radar size={14} /> 扫描会影响论文的新文献
            </button>
            <button className="btn-ghost w-full justify-start" onClick={() => go('actions')}>
              → 查看行动队列 ({project.actions.length})
            </button>
            <button className="btn-ghost w-full justify-start" onClick={() => go('paper')}>
              📄 核验论文 Claim ({project.claimsTotal})
            </button>
            <button className="btn-ghost w-full justify-start" onClick={() => go('improve')}>
              ✎ 改造工作台
            </button>
            <button className="btn-ghost w-full justify-start" onClick={() => void exportBrief()} disabled={briefState === 'busy'}>
              {briefState === 'busy' ? '正在导出证据简报…' : '↓ 导出写作证据简报'}
            </button>
            {briefState === 'done' && <span className="text-xs text-teal">简报已下载</span>}
            {briefState === 'error' && <span className="text-xs text-red-600 dark:text-red-300">简报导出失败</span>}
            <div className="flex flex-wrap gap-2">
              <button className="btn-primary justify-start" onClick={() => navigate(`/cases/${projectId}/digest`)}>
                阅读周报
              </button>
              <button className="btn-ghost justify-start" onClick={() => void exportDigest()} disabled={digestState === 'busy'}>
                {digestState === 'busy' ? '正在生成雷达周报…' : '↓ 导出雷达周报'}
              </button>
            </div>
            {digestState === 'done' && <span className="text-xs text-teal">周报已下载</span>}
            {digestState === 'error' && <span className="text-xs text-red-600 dark:text-red-300">周报生成失败</span>}
            <div className="flex flex-wrap gap-2 text-[11px]">
              {([
                ['scan', '扫描'],
                ['deep', '深度调研'],
                ['impacts', '影响'],
                ['actions', '行动'],
              ] as [string, string][]).map(([key, label]) => {
                const active = digestSections.includes(key);
                return (
                  <button
                    key={key}
                    className={`rounded-full border px-2 py-0.5 transition-colors ${
                      active
                        ? 'border-ink bg-ink text-white dark:border-white dark:bg-white dark:text-[#111113]'
                        : 'border-hairline text-neutral-500 hover:border-neutral-400 dark:border-white/10 dark:text-white/85'
                    }`}
                    onClick={() =>
                      setDigestSections((current) =>
                        active
                          ? current.filter((item) => item !== key)
                          : [...current, key]
                      )
                    }
                  >
                    {label}
                  </button>
                );
              })}
            </div>
            {monitoringStats && (
              <div className="mt-2 rounded-lg border border-hairline px-3.5 py-2.5 text-[11px] text-neutral-500 dark:border-white/[0.08] dark:text-white/85">
                监控健康：总扫描 {monitoringStats.total_scans} · 失败 {monitoringStats.status_counts.failed ?? 0} · 自动启动失败 {monitoringStats.auto_scan_start_failures}
              </div>
            )}
            {Object.keys(scanStatusCounts).length > 0 && (
              <div className="mt-2 text-[11px] text-neutral-500 dark:text-white/85">
                扫描状态：{Object.entries(scanStatusCounts).map(([status, count]) => `${status} ${count}`).join(' · ')}
              </div>
            )}
            {costAlert && (
              <div className={`mt-2 rounded-lg border px-3.5 py-2.5 text-[11px] ${monitoringStats?.cost_alert_exceeded ? 'border-red-200 bg-red-50 text-red-700 dark:border-red-500/25 dark:bg-red-500/10 dark:text-red-300' : 'border-hairline text-neutral-500 dark:border-white/[0.08] dark:text-white/85'}`}>
                <div className="flex items-center justify-between gap-2">
                  <span>月度成本 ${monitoringStats?.monthly_cost_usd?.toFixed(2) ?? '0.00'}</span>
                  <div className="flex items-center gap-2">
                    <button
                      className="btn-quiet !px-2 !py-0.5 text-[10px]"
                      disabled={costAlertTesting || !costAlert.enabled || !costAlert.webhook_url?.trim()}
                      onClick={() => void handleTestCostAlert()}
                    >
                      {costAlertTesting ? '测试中…' : '测试发送'}
                    </button>
                    <button
                      className="btn-quiet !px-2 !py-0.5 text-[10px]"
                      disabled={costAlertSaving}
                      onClick={() => void saveCostAlert(!costAlert.enabled)}
                    >
                      {costAlertSaving ? '…' : costAlert.enabled ? '已开启' : '已关闭'}
                    </button>
                  </div>
                </div>
                {costAlert.enabled && (
                  <div className="mt-2 flex items-center gap-2">
                    <span>预算 $</span>
                    <input
                      type="number"
                      className="input !w-20 !py-1 text-[11px]"
                      defaultValue={costAlert.monthly_budget_usd}
                      onBlur={(e) => void saveCostAlert(true, Number(e.target.value))}
                    />
                    <span>Webhook</span>
                    <input
                      className="input flex-1 !py-1 text-[11px]"
                      placeholder="https://example.com/hook"
                      defaultValue={costAlert.webhook_url ?? ''}
                      onBlur={(e) => void saveCostAlert(true, undefined, e.target.value)}
                    />
                  </div>
                )}
                {monitoringStats?.cost_alert_exceeded && (
                  <div className="mt-1 text-red-600 dark:text-red-300">已超出月度预算！</div>
                )}
              </div>
            )}
          </div>
        </div>
      </Reveal>

      {/* Scan status chart */}
      <Reveal delay={135}>
        <div className="card mt-8 p-6">
          <div className="kicker mb-3">扫描状态分布</div>
          {scanStatusChartData.length === 0 ? (
            <p className="text-sm text-neutral-400 dark:text-white/75 text-center py-8">暂无扫描记录</p>
          ) : (
            <ResponsiveContainer width="100%" height={200}>
              <BarChart data={scanStatusChartData}>
                <XAxis dataKey="name" />
                <YAxis allowDecimals={false} />
                <Tooltip />
                <Bar dataKey="value" fill="#4E8AFB" radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          )}
        </div>
      </Reveal>

      {/* Scan trend */}
      <Reveal delay={136}>
        <div className="card mt-8 p-6">
          <div className="kicker mb-3">扫描历史趋势</div>
          {scanTrendData.length === 0 ? (
            <p className="text-sm text-neutral-400 dark:text-white/75 text-center py-8">暂无扫描记录</p>
          ) : (
            <ResponsiveContainer width="100%" height={220}>
              <BarChart data={scanTrendData}>
                <XAxis dataKey="name" />
                <YAxis allowDecimals={false} />
                <Tooltip />
                <Bar dataKey="papers" name="发现论文" fill="#4E8AFB" radius={[4, 4, 0, 0]} />
                <Bar dataKey="impacts" name="影响候选" fill="#B54708" radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          )}
        </div>
      </Reveal>

      {/* Scan duration */}
      <Reveal delay={137}>
        <div className="card mt-8 p-6">
          <div className="kicker mb-3">扫描耗时趋势（分钟）</div>
          {scanDurationData.length === 0 ? (
            <p className="text-sm text-neutral-400 dark:text-white/75 text-center py-8">暂无扫描记录</p>
          ) : (
            <ResponsiveContainer width="100%" height={200}>
              <BarChart data={scanDurationData}>
                <XAxis dataKey="name" />
                <YAxis allowDecimals={false} />
                <Tooltip />
                <Bar dataKey="minutes" name="耗时（分钟）" fill="#067647" radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          )}
        </div>
      </Reveal>

      {/* Scan cost */}
      <Reveal delay={138}>
        <div className="card mt-8 p-6">
          <div className="kicker mb-3">扫描成本趋势（USD）</div>
          {scanCostData.length === 0 ? (
            <p className="text-sm text-neutral-400 dark:text-white/75 text-center py-8">暂无扫描记录</p>
          ) : (
            <ResponsiveContainer width="100%" height={200}>
              <BarChart data={scanCostData}>
                <XAxis dataKey="name" />
                <YAxis />
                <Tooltip />
                <Bar dataKey="cost" name="成本（USD）" fill="#B54708" radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          )}
        </div>
      </Reveal>

      {/* Digest webhook */}
      <Reveal delay={139}>
        <div className="card mt-8 p-6">
          <div className="flex items-center justify-between gap-3">
            <div className="kicker">周报自动发送</div>
            <div className="flex items-center gap-2">
              <button
                className="btn-quiet !px-2 !py-1 text-[10px]"
                disabled={digestTesting || !digestWebhook?.enabled}
                onClick={() => void handleTestDigestWebhook()}
              >
                {digestTesting ? '测试中…' : '测试发送'}
              </button>
              <button
                className="btn-quiet !px-2 !py-1 text-[10px]"
                disabled={digestWebhookSaving || !digestWebhook}
                onClick={() => void saveDigestWebhook(!digestWebhook?.enabled)}
              >
                {digestWebhookSaving ? '…' : digestWebhook?.enabled ? '已开启' : '已关闭'}
              </button>
            </div>
          </div>
          {digestWebhook?.enabled && (
            <div className="mt-3 space-y-2">
              <div className="flex items-center gap-2 text-[11px]">
                <span>Webhook</span>
                <input
                  className="input flex-1 !py-1 text-[11px]"
                  placeholder="https://example.com/digest"
                  defaultValue={digestWebhook.webhook_url}
                  onBlur={(e) => void saveDigestWebhook(true, e.target.value)}
                />
              </div>
              <div className="flex items-center gap-2 text-[11px]">
                <span>周期</span>
                <select
                  className="input !w-28 !py-1 text-[11px]"
                  value={digestWebhook.schedule}
                  onChange={(e) => void saveDigestWebhook(true, undefined, e.target.value)}
                >
                  <option value="daily">每天</option>
                  <option value="weekly">每周</option>
                  <option value="monthly">每月</option>
                </select>
              </div>
            </div>
          )}
        </div>
      </Reveal>

      {/* Scan completion webhook */}
      <Reveal delay={140}>
        <div className="card mt-8 p-6">
          <div className="flex items-center justify-between gap-3">
            <div className="kicker">扫描完成通知</div>
            <button
              className="btn-quiet !px-2 !py-1 text-[10px]"
              disabled={scanWebhookSaving || !scanWebhook}
              onClick={() => void saveScanWebhook(!scanWebhook?.enabled)}
            >
              {scanWebhookSaving ? '…' : scanWebhook?.enabled ? '已开启' : '已关闭'}
            </button>
          </div>
          {scanWebhook?.enabled && (
            <div className="mt-3 space-y-2">
              <div className="flex items-center gap-2 text-[11px]">
                <span>Webhook</span>
                <input
                  className="input flex-1 !py-1 text-[11px]"
                  placeholder="https://example.com/scan"
                  defaultValue={scanWebhook.webhook_url}
                  onBlur={(e) => void saveScanWebhook(true, e.target.value)}
                />
              </div>
              <div className="flex items-center gap-2 text-[11px]">
                <span>通知条件</span>
                <select
                  className="input !w-28 !py-1 text-[11px]"
                  value={scanWebhook.notify_on}
                  onChange={(e) => void saveScanWebhook(true, undefined, e.target.value)}
                >
                  <option value="completed">完成时</option>
                  <option value="failed">失败时</option>
                  <option value="all">全部</option>
                </select>
              </div>
            </div>
          )}
        </div>
      </Reveal>

      {/* Email/SMTP notifications */}
      <Reveal delay={141}>
        <div className="card mt-8 p-6">
          <div className="flex items-center justify-between gap-3">
            <div className="kicker">邮件通知（SMTP）</div>
            <div className="flex items-center gap-2">
              <button
                className="btn-quiet !px-2 !py-1 text-[10px]"
                disabled={emailNotifyTesting || !emailNotify?.enabled}
                onClick={() => void handleTestEmailNotify()}
              >
                {emailNotifyTesting ? '测试中…' : '测试发送'}
              </button>
              <button
                className="btn-quiet !px-2 !py-1 text-[10px]"
                disabled={emailNotifySaving || !emailNotify}
                onClick={() => void saveEmailNotify(!emailNotify?.enabled)}
              >
                {emailNotifySaving ? '…' : emailNotify?.enabled ? '已开启' : '已关闭'}
              </button>
            </div>
          </div>
          {emailNotify && (
            <div className="mt-3 space-y-2">
              <div className="flex items-center gap-2 text-[11px]">
                <span>SMTP 服务器</span>
                <input
                  className="input flex-1 !py-1 text-[11px]"
                  placeholder="smtp.example.com"
                  defaultValue={emailNotify.smtp_host}
                  onBlur={(e) => void saveEmailNotify(emailNotify.enabled, { smtp_host: e.target.value })}
                />
              </div>
              <div className="flex items-center gap-2 text-[11px]">
                <span>端口</span>
                <input
                  type="number"
                  className="input !w-24 !py-1 text-[11px]"
                  defaultValue={emailNotify.smtp_port}
                  onBlur={(e) => void saveEmailNotify(emailNotify.enabled, { smtp_port: Number(e.target.value) || 587 })}
                />
                <span className="text-neutral-400 dark:text-white/70">465=SSL / 其他=STARTTLS</span>
              </div>
              <div className="flex items-center gap-2 text-[11px]">
                <span>用户名</span>
                <input
                  className="input flex-1 !py-1 text-[11px]"
                  placeholder="sender@example.com"
                  defaultValue={emailNotify.username}
                  onBlur={(e) => void saveEmailNotify(emailNotify.enabled, { username: e.target.value })}
                />
              </div>
              <div className="flex items-center gap-2 text-[11px]">
                <span>密码</span>
                <input
                  type="password"
                  className="input flex-1 !py-1 text-[11px]"
                  placeholder={emailNotify.password === '***' ? '已保存，留空则保持不变' : 'SMTP 密码'}
                  defaultValue={emailNotify.password === '***' ? '***' : emailNotify.password}
                  onBlur={(e) => void saveEmailNotify(emailNotify.enabled, { password: e.target.value })}
                />
              </div>
              <div className="flex items-center gap-2 text-[11px]">
                <span>收件人</span>
                <input
                  className="input flex-1 !py-1 text-[11px]"
                  placeholder="you@example.com"
                  defaultValue={emailNotify.recipient}
                  onBlur={(e) => void saveEmailNotify(emailNotify.enabled, { recipient: e.target.value })}
                />
              </div>
              <div className="flex items-center gap-2 text-[11px]">
                <span>主题前缀</span>
                <input
                  className="input flex-1 !py-1 text-[11px]"
                  defaultValue={emailNotify.subject_prefix}
                  onBlur={(e) => void saveEmailNotify(emailNotify.enabled, { subject_prefix: e.target.value })}
                />
              </div>
            </div>
          )}
        </div>
      </Reveal>

      {/* Notification history */}
      <Reveal delay={139}>
        <div className="card mt-8 p-6">
          <div className="flex items-center justify-between gap-3">
            <div className="kicker mb-0">通知历史</div>
            <div className="flex items-center gap-2">
              <select
                className="input !w-52 !py-1 text-[11px]"
                value={notificationFilter}
                onChange={(e) => setNotificationFilter(e.target.value)}
              >
                <option value="">全部</option>
                <option value="cost_alert_webhook_sent">Webhook 已发送</option>
                <option value="cost_alert_webhook_failed">Webhook 失败</option>
                <option value="auto_scan_start_failed">自动扫描启动失败</option>
              </select>
              <button
                className="btn-quiet !px-2 !py-1 text-[10px]"
                onClick={() => downloadJSON(notifications ?? [], `notifications-${project?.short || 'case'}.json`)}
              >
                导出 JSON
              </button>
              <button
                className="btn-quiet !px-2 !py-1 text-[10px]"
                onClick={() =>
                  downloadMarkdown(notifications ?? [], `notifications-${project?.short || 'case'}.md`)
                }
              >
                导出 MD
              </button>
            </div>
          </div>
          {!notifications || notifications.length === 0 ? (
            <p className="text-sm text-neutral-400 dark:text-white/75 text-center py-6">暂无通知</p>
          ) : (
            <div className="space-y-2">
              {notifications.slice(0, 5).map((n) => (
                <div key={n.id} className="flex flex-wrap items-center gap-2 text-[12px]">
                  <span className={`badge ${n.event_type.includes('failed') ? 'badge-red' : 'badge-green'}`}>
                    {n.event_type}
                  </span>
                  <span className="locator">{n.created_at ? new Date(n.created_at).toLocaleString() : ''}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      </Reveal>

      {/* Source library */}
      <Reveal delay={140}>
        <div className="mt-8">
          <SectionHead
            kicker="Source Library"
            title="文献来源库"
            right={
              <div className="flex items-center gap-2">
                <span className="locator">{sources?.length ?? 0} 个来源</span>
                <button
                  className="btn-quiet !px-2 !py-1 text-[10px]"
                  onClick={() => setShowSourceImport((value) => !value)}
                >
                  {showSourceImport ? '收起导入' : '导入来源'}
                </button>
                <button
                  className="btn-quiet !px-2 !py-1 text-[10px]"
                  onClick={() => downloadJSON(sources ?? [], `sources-${project.short || 'case'}.json`)}
                >
                  导出 JSON
                </button>
                <button
                  className="btn-quiet !px-2 !py-1 text-[10px]"
                  onClick={() => void downloadBibtex()}
                >
                  导出 BibTeX
                </button>
              </div>
            }
          />
          <div className="card overflow-hidden">
            <div className="flex flex-wrap items-center gap-2 border-b border-hairline p-3">
              <label className="flex items-center gap-2 text-[11px] text-neutral-500 dark:text-white/85">
                <span>CCF</span>
                <select
                  className="input !w-24 !py-1 text-[11px]"
                  value={ccfRankFilter}
                  onChange={(e) => {
                    setCcfRankFilter(e.target.value);
                    setSourceOffset(0);
                  }}
                >
                  <option value="">全部</option>
                  <option value="A">A</option>
                  <option value="B">B</option>
                  <option value="C">C</option>
                </select>
              </label>
              <label className="flex items-center gap-2 text-[11px] text-neutral-500 dark:text-white/85">
                <span>来源类型</span>
                <select
                  className="input !w-44 !py-1 text-[11px]"
                  value={sourceKindFilter}
                  onChange={(e) => {
                    setSourceKindFilter(e.target.value);
                    setSourceOffset(0);
                  }}
                >
                  <option value="">全部</option>
                  <option value="arxiv">arxiv</option>
                  <option value="openalex">openalex</option>
                  <option value="semantic_scholar">semantic_scholar</option>
                  <option value="pubmed">pubmed</option>
                </select>
              </label>
              <label className="flex items-center gap-2 text-[11px] text-neutral-500 dark:text-white/85">
                <span>标签</span>
                <input
                  className="input !w-36 !py-1 text-[11px]"
                  value={tagFilter}
                  placeholder="按标签筛选"
                  aria-label="按标签筛选"
                  onChange={(event) => {
                    setTagFilter(event.target.value);
                    setSourceOffset(0);
                  }}
                />
              </label>
              <label className="flex items-center gap-2 text-[11px] text-neutral-500 dark:text-white/85">
                <span>批量标签</span>
                <input
                  className="input !w-36 !py-1 text-[11px]"
                  value={bulkSourceTagsInput}
                  placeholder="逗号分隔"
                  aria-label="批量标签"
                  onChange={(event) => {
                    setBulkSourceTagsInput(event.target.value);
                    setBulkSourceTagsError(null);
                  }}
                />
              </label>
              <div className="ml-auto flex items-center gap-2">
                <button
                  className="btn-quiet !px-2 !py-1 text-[10px]"
                  disabled={
                    selectedSourceIds.length === 0 ||
                    bulkSourceTagsSaving ||
                    !bulkSourceTagsInput.trim()
                  }
                  onClick={() => void handleBulkTagSelectedSources()}
                >
                  {bulkSourceTagsSaving ? '打标签中…' : '批量打标签'}
                </button>
                <button
                  className="btn-quiet !px-2 !py-1 text-[10px]"
                  disabled={selectedSourceIds.length === 0 || removingSources}
                  onClick={() => void handleRemoveSelectedSources()}
                >
                  {removingSources ? '删除中…' : '删除选中'}
                </button>
                <button
                  className="btn-quiet !px-2 !py-1 text-[10px]"
                  disabled={sourceOffset === 0 || sourcesLoading}
                  onClick={() => setSourceOffset((offset) => Math.max(0, offset - 10))}
                >
                  上一页
                </button>
                <span className="text-[11px] tabular-nums text-neutral-400 dark:text-white/75">
                  第 {Math.floor(sourceOffset / 10) + 1} 页
                </span>
                <button
                  className="btn-quiet !px-2 !py-1 text-[10px]"
                  disabled={sourcesLoading || !sources || sources.length < 10}
                  onClick={() => setSourceOffset((offset) => offset + 10)}
                >
                  下一页
                </button>
              </div>
              {showSourceImport && (
                <div className="basis-full flex flex-wrap items-center gap-2 rounded-lg border border-dashed border-hairline dark:border-white/10 bg-neutral-50 p-2.5 dark:bg-white/[0.03]">
                  <input
                    className="input flex-1 !py-1 text-[11px] min-w-[180px]"
                    value={sourceImportUrl}
                    onChange={(event) => setSourceImportUrl(event.target.value)}
                    placeholder="论文 URL（arXiv / OpenAlex / DOI 落地页等）"
                    aria-label="论文 URL"
                  />
                  <input
                    className="input !w-40 !py-1 text-[11px]"
                    value={sourceImportDoi}
                    onChange={(event) => setSourceImportDoi(event.target.value)}
                    placeholder="DOI，例如 10.1000/xyz123"
                    aria-label="DOI"
                  />
                  <button
                    className="btn-primary !px-3 !py-1 text-[11px]"
                    disabled={sourceImporting}
                    onClick={() => void handleImportSource()}
                  >
                    {sourceImporting ? '导入中…' : '开始导入'}
                  </button>
                  {sourceImportError && (
                    <span className="basis-full text-[11px] text-red-600 dark:text-red-300">{sourceImportError}</span>
                  )}
                </div>
              )}
              {sourceRemovalError && (
                <p className="basis-full text-[11px] text-red-600 dark:text-red-300">
                  删除来源失败：{sourceRemovalError}
                </p>
              )}
              {bulkSourceTagsError && (
                <p className="basis-full text-[11px] text-red-600 dark:text-red-300">
                  批量打标签失败：{bulkSourceTagsError}
                </p>
              )}
            </div>
            {sourcesLoading ? (
              <div aria-busy="true" className="space-y-3 p-6">
                {Array.from({ length: 4 }, (_, i) => (
                  <div key={i} className="h-8 rounded bg-neutral-100/70 animate-pulse dark:bg-white/5" />
                ))}
              </div>
            ) : sourcesError ? (
              <p className="p-6 text-sm text-red-600 dark:text-red-300">
                来源库加载失败：{sourcesError.message}
              </p>
            ) : !sources || sources.length === 0 ? (
              <p className="py-8 text-center text-sm text-neutral-400 dark:text-white/75">
                暂无文献来源
              </p>
            ) : (
              <div className="overflow-x-auto">
                <table className="table-base min-w-[800px]">
                  <thead>
                    <tr>
                      <th className="w-10">
                        <span className="sr-only">选择</span>
                      </th>
                      <th>标题</th>
                      <th>作者</th>
                      <th>来源</th>
                      <th>CCF</th>
                      <th>快照</th>
                    </tr>
                  </thead>
                  <tbody>
                    {sources.slice(0, 10).map((source) => (
                      <tr
                        key={source.id}
                        role="button"
                        tabIndex={0}
                        aria-label={`查看来源详情：${source.title}`}
                        className="cursor-pointer focus-visible:bg-neutral-100 dark:focus-visible:bg-white/[0.06]"
                        onClick={() => void openSourceDetail(source.id)}
                        onKeyDown={(event) => {
                          if (event.key === 'Enter' || event.key === ' ') {
                            event.preventDefault();
                            void openSourceDetail(source.id);
                          }
                        }}
                      >
                        <td
                          onClick={(event) => event.stopPropagation()}
                          onKeyDown={(event) => event.stopPropagation()}
                        >
                          <input
                            type="checkbox"
                            className="h-4 w-4 accent-ink"
                            checked={selectedSourceIds.includes(source.id)}
                            aria-label={`选择来源：${source.title}`}
                            onChange={() => {
                              setSelectedSourceIds((current) =>
                                current.includes(source.id)
                                  ? current.filter((id) => id !== source.id)
                                  : [...current, source.id],
                              );
                              setSourceRemovalError(null);
                              setBulkSourceTagsError(null);
                            }}
                          />
                        </td>
                        <td className="max-w-[280px]">
                          <div className="line-clamp-2 font-medium text-ink dark:text-white" title={source.title}>
                            {source.title}
                          </div>
                        </td>
                        <td className="max-w-[220px] text-neutral-500 dark:text-white/85">
                          <div className="line-clamp-2">{source.authors.join(', ') || '—'}</div>
                        </td>
                        <td className="max-w-[180px] text-neutral-500 dark:text-white/85">
                          <div className="line-clamp-2">{source.venue || source.source_kind || '—'}</div>
                        </td>
                        <td>
                          {source.ccf_rank ? (
                            <span className="badge-blue">CCF-{source.ccf_rank}</span>
                          ) : (
                            <span className="text-neutral-300 dark:text-white/65">—</span>
                          )}
                        </td>
                        <td className="font-mono tabular-nums text-neutral-500 dark:text-white/85">
                          {source.snapshot_count}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {sources.length > 10 && (
                  <div className="px-3 py-2 text-right text-[11px] text-neutral-400 dark:text-white/75">
                    显示前 10 条，共 {sources.length} 个来源
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      </Reveal>

      <Dialog
        open={selectedSourceId !== null}
        onOpenChange={(open) => {
          if (!open) closeSourceDetail();
        }}
      >
        <DialogContent className="max-h-[85vh] max-w-3xl overflow-y-auto">
          <DialogHeader>
            <DialogTitle>{sourceDetail?.title ?? '来源详情'}</DialogTitle>
            <DialogDescription>
              {sourceDetail ? `${sourceDetail.source_kind}${sourceDetail.venue ? ` · ${sourceDetail.venue}` : ''}` : '正在加载来源信息'}
            </DialogDescription>
          </DialogHeader>

          {sourceDetailLoading ? (
            <div aria-busy="true" className="space-y-3 py-4">
              <div className="h-4 w-3/4 rounded bg-neutral-100 animate-pulse dark:bg-white/10" />
              <div className="h-4 w-full rounded bg-neutral-100 animate-pulse dark:bg-white/10" />
              <div className="h-4 w-2/3 rounded bg-neutral-100 animate-pulse dark:bg-white/10" />
            </div>
          ) : sourceDetailError ? (
            <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-500/25 dark:bg-red-500/10 dark:text-red-300">
              来源详情加载失败：{sourceDetailError.message}
            </div>
          ) : sourceDetail ? (
            <div className="space-y-5 text-sm">
              <div className="grid gap-x-6 gap-y-4 sm:grid-cols-2">
                <div className="sm:col-span-2">
                  <div className="kicker mb-1">作者</div>
                  <div className="text-neutral-700 dark:text-white/95">{sourceDetail.authors.join(', ') || '—'}</div>
                </div>
                <div>
                  <div className="kicker mb-1">Venue</div>
                  <div className="text-neutral-700 dark:text-white/95">{sourceDetail.venue || '—'}</div>
                </div>
                <div>
                  <div className="kicker mb-1">来源类型</div>
                  <div className="text-neutral-700 dark:text-white/95">{sourceDetail.source_kind || '—'}</div>
                </div>
                <div>
                  <div className="kicker mb-1">CCF Rank</div>
                  <div className="text-neutral-700 dark:text-white/95">{sourceDetail.ccf_rank ? `CCF-${sourceDetail.ccf_rank}` : '—'}</div>
                </div>
                <div>
                  <div className="kicker mb-1">Integrity State</div>
                  <div className="text-neutral-700 dark:text-white/95">{sourceDetail.integrity_state || '—'}</div>
                </div>
                <div>
                  <div className="kicker mb-1">DOI</div>
                  <div className="break-all font-mono text-[12px] text-neutral-700 dark:text-white/95">{sourceDetail.doi || '—'}</div>
                </div>
                <div>
                  <div className="kicker mb-1">arXiv ID</div>
                  <div className="break-all font-mono text-[12px] text-neutral-700 dark:text-white/95">{sourceDetail.arxiv_id || '—'}</div>
                </div>
                <div>
                  <div className="kicker mb-1">arXiv Primary Category</div>
                  <div className="text-neutral-700 dark:text-white/95">{sourceDetail.arxiv_primary_category || '—'}</div>
                </div>
                <div>
                  <div className="kicker mb-1">研究领域</div>
                  <div className="text-neutral-700 dark:text-white/95">{sourceDetail.fields_of_study.join(', ') || '—'}</div>
                </div>
                <div>
                  <div className="kicker mb-1">快照数量</div>
                  <div className="tabular-nums text-neutral-700 dark:text-white/95">{sourceDetail.snapshots.length}</div>
                </div>
              </div>

              <div className="grid gap-4 pt-4 hairline-t sm:grid-cols-2">
                <div>
                  <div className="kicker mb-2">Related Impact IDs</div>
                  {sourceDetail.related_impact_ids.length > 0 ? (
                    <div className="flex flex-wrap gap-1.5">
                      {sourceDetail.related_impact_ids.map((id) => (
                        <span key={id} className="rounded-md border border-hairline px-2 py-1 font-mono text-[11px] text-neutral-600 dark:border-white/10 dark:text-white/90">{id}</span>
                      ))}
                    </div>
                  ) : (
                    <div className="text-neutral-400 dark:text-white/75">—</div>
                  )}
                </div>
                <div>
                  <div className="kicker mb-2">Related Claim IDs</div>
                  {sourceDetail.related_claim_ids.length > 0 ? (
                    <div className="flex flex-wrap gap-1.5">
                      {sourceDetail.related_claim_ids.map((id) => (
                        <span key={id} className="rounded-md border border-hairline px-2 py-1 font-mono text-[11px] text-neutral-600 dark:border-white/10 dark:text-white/90">{id}</span>
                      ))}
                    </div>
                  ) : (
                    <div className="text-neutral-400 dark:text-white/75">—</div>
                  )}
                </div>
              </div>

              <div className="pt-4 hairline-t">
                <div className="kicker mb-2">Tags</div>
                <div className="flex flex-wrap gap-1.5">
                  {(sourceDetail.tags ?? []).length > 0 ? (
                    (sourceDetail.tags ?? []).map((tag) => (
                      <span key={tag} className="chip font-mono !text-[11px]">{tag}</span>
                    ))
                  ) : (
                    <span className="text-neutral-400 dark:text-white/75">暂无标签</span>
                  )}
                </div>
                <div className="mt-3 flex flex-col gap-2 sm:flex-row">
                  <input
                    className="input flex-1 text-[12px]"
                    placeholder="添加标签，逗号分隔"
                    value={sourceTagsInput}
                    onChange={(event) => setSourceTagsInput(event.target.value)}
                    onKeyDown={(event) => {
                      if (event.key === 'Enter') {
                        event.preventDefault();
                        void handleSaveSourceTags();
                      }
                    }}
                  />
                  <button
                    className="btn-quiet shrink-0"
                    disabled={sourceTagsSaving}
                    onClick={() => void handleSaveSourceTags()}
                  >
                    {sourceTagsSaving ? '保存中…' : '保存标签'}
                  </button>
                </div>
                {sourceTagsError && (
                  <div className="mt-2 text-[12px] text-red-600 dark:text-red-300">标签保存失败：{sourceTagsError}</div>
                )}
              </div>
            </div>
          ) : null}
        </DialogContent>
      </Dialog>

      {/* Cost by source */}
      <Reveal delay={145}>
        <div className="mt-8 card p-6">
          <div className="flex items-center justify-between gap-3">
            <div className="kicker mb-0">成本按来源拆分</div>
            <span className="text-xs tabular-nums text-neutral-500 dark:text-white/85">
              总成本 {costBySource ? `$${costBySource.total_cost_usd.toFixed(2)}` : '—'}
            </span>
          </div>
          {costBySourceLoading ? (
            <div aria-busy="true" className="mt-4 h-[220px] rounded bg-neutral-100/70 animate-pulse dark:bg-white/5" />
          ) : costBySourceError ? (
            <p className="py-8 text-center text-sm text-red-600 dark:text-red-300">
              成本按来源加载失败：{costBySourceError.message}
            </p>
          ) : costBySourceChartData.length === 0 ? (
            <p className="py-8 text-center text-sm text-neutral-400 dark:text-white/75">暂无成本记录</p>
          ) : (
            <div className="mt-4">
              <ResponsiveContainer width="100%" height={220}>
                <BarChart data={costBySourceChartData}>
                  <XAxis dataKey="name" />
                  <YAxis />
                  <Tooltip />
                  <Bar dataKey="cost" name="成本（USD）" fill="#B54708" radius={[4, 4, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          )}
        </div>
      </Reveal>

      {/* Topics */}
      {project.topics.length > 0 && (
        <Reveal delay={150}>
          <div className="mt-8">
            <SectionHead kicker="Watch Topics" title="雷达监控主题" />
            <div className="flex flex-wrap gap-2">
              {project.topics.map((t) => (
                <span key={t} className="chip font-mono !text-[11px]">{t}</span>
              ))}
            </div>
          </div>
        </Reveal>
      )}
    </div>
  );
}
