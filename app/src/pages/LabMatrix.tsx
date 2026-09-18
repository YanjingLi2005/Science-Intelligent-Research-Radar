import { useMemo, useState } from 'react';
import { AlertTriangle, CheckCircle2, Download, FileText, Flame, FlaskConical } from 'lucide-react';
import { downloadComplianceReport, getClaimMatrix, useApi, type ClaimMatrixRow } from '../api';
import ErrorRetry from '../components/ErrorRetry';
import { Empty, Stat } from '../components/chrome';
import { Spinner } from '../components/ui/spinner';
import { toast } from 'sonner';

const columns: { key: keyof ClaimMatrixRow | string; label: string }[] = [
  { key: 'case_title', label: '课题' },
  { key: 'urgency', label: '竞争风险' },
  { key: 'stable_key', label: 'Stable key' },
  { key: 'statement', label: 'Claim' },
  { key: 'task', label: '任务' },
  { key: 'dataset', label: '数据集' },
  { key: 'metric', label: '指标' },
  { key: 'comparator', label: '比较对象' },
  { key: 'scope', label: '范围' },
  { key: 'review_state', label: '审核' },
];

function cell(row: ClaimMatrixRow, key: string): string {
  if (key in row) return String(row[key as keyof ClaimMatrixRow] ?? '');
  return String(row.contract[key as keyof typeof row.contract] ?? '');
}

function stateClass(state: string): string {
  return state === 'confirmed' ? 'badge-green' : 'badge-orange';
}

function urgencyMeta(urgency?: string) {
  switch (urgency) {
    case 'critical':
      return { label: '高危冲突', className: 'badge-red', icon: Flame };
    case 'review':
      return { label: '边界审查', className: 'badge-orange', icon: AlertTriangle };
    case 'supported':
      return { label: '有新支持', className: 'badge-teal', icon: CheckCircle2 };
    default:
      return { label: '正常领先', className: 'badge-gray', icon: CheckCircle2 };
  }
}

type UrgencyFilter = 'all' | 'critical' | 'review' | 'clean';

export default function LabMatrix() {
  const { data, loading, error, refetch } = useApi(() => getClaimMatrix(), []);
  const [filter, setFilter] = useState('all');
  const [urgencyFilter, setUrgencyFilter] = useState<UrgencyFilter>('all');
  const [exportingReport, setExportingReport] = useState<string | null>(null);

  const selectedCaseId = useMemo(() => {
    if (filter.startsWith('case:')) return filter.slice(5);
    if (data && data.cases.length > 0) return data.cases[0].id;
    return null;
  }, [filter, data]);

  const handleDownloadReport = async (format: 'markdown' | 'html') => {
    if (!selectedCaseId) return;
    setExportingReport(format);
    try {
      await downloadComplianceReport(selectedCaseId, format);
      toast.success(`科研合规审计报告 (${format.toUpperCase()}) 已导出`);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '导出合规报告失败');
    } finally {
      setExportingReport(null);
    }
  };

  const filterOptions = useMemo(() => {
    if (!data) return [];
    const groups = data.cases
      .filter((item) => item.group_key)
      .map((item) => ({ value: `group:${item.group_key}`, label: `组 · ${item.group_key}` }));
    const cases = data.cases.map((item) => ({ value: `case:${item.id}`, label: item.short || item.title }));
    return [...groups.filter((item, index, all) => all.findIndex((other) => other.value === item.value) === index), ...cases];
  }, [data]);

  const summary = useMemo(() => {
    if (!data) return { total_claims: 0, critical_claims: 0, review_claims: 0, clean_claims: 0 };
    if (data.summary) return data.summary;
    const total = data.rows.length;
    const critical = data.rows.filter((r) => r.urgency === 'critical').length;
    const review = data.rows.filter((r) => r.urgency === 'review').length;
    const clean = total - critical - review;
    return { total_claims: total, critical_claims: critical, review_claims: review, clean_claims: clean };
  }, [data]);

  const rows = useMemo(() => {
    if (!data) return [];
    return data.rows.filter((row) => {
      // Group/Case filter
      if (filter !== 'all') {
        if (filter.startsWith('case:') && row.case_id !== filter.slice(5)) return false;
        if (filter.startsWith('group:') && row.group_key !== filter.slice(6)) return false;
      }
      // Urgency filter
      if (urgencyFilter === 'critical') return row.urgency === 'critical';
      if (urgencyFilter === 'review') return row.urgency === 'review';
      if (urgencyFilter === 'clean') return row.urgency === 'normal' || row.urgency === 'supported' || !row.urgency;
      return true;
    });
  }, [data, filter, urgencyFilter]);

  if (loading && !data) {
    return <div className="flex items-center justify-center py-20"><Spinner className="size-8 text-neutral-400 dark:text-white/60" /></div>;
  }
  if (error || !data) {
    return <ErrorRetry message={`课题组矩阵加载失败：${error?.message ?? '未知错误'}`} onRetry={() => void refetch()} />;
  }

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-4 border-b border-black/10 pb-6 dark:border-white/10">
        <div>
          <div className="kicker flex items-center gap-2">
            <FlaskConical size={14} /> RESEARCH LAB PANORAMA
          </div>
          <h1 className="mt-2 text-[clamp(2rem,3vw,2.75rem)] font-semibold tracking-[-0.04em]">课题组全景矩阵 (Lab Matrix)</h1>
          <p className="mt-1 text-sm text-neutral-500 dark:text-white/70">
            聚合多课题与多研究方向的竞争风险，展示团队维度的受冲击全景。
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <label className="flex items-center gap-2 text-xs text-neutral-500 dark:text-white/70">
            课题/分组筛选
            <select value={filter} onChange={(event) => setFilter(event.target.value)} className="rounded-lg border border-hairline bg-white px-3 py-2 text-sm text-ink outline-none dark:border-white/10 dark:bg-white/[0.06] dark:text-white">
              <option value="all">全部课题 ({data.cases.length} 个)</option>
              {filterOptions.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
            </select>
          </label>
          <div className="flex items-center gap-2">
            <button
              className="btn-ghost text-xs flex items-center gap-1.5"
              disabled={!selectedCaseId || Boolean(exportingReport)}
              onClick={() => void handleDownloadReport('markdown')}
              title={selectedCaseId ? '导出当前选中课题合规报告 (Markdown)' : '请先选择课题'}
            >
              <FileText size={13} />
              {exportingReport === 'markdown' ? '导出中…' : '合规报告 (MD)'}
            </button>
            <button
              className="btn-primary text-xs flex items-center gap-1.5"
              disabled={!selectedCaseId || Boolean(exportingReport)}
              onClick={() => void handleDownloadReport('html')}
              title={selectedCaseId ? '导出当前选中课题合规报告 (HTML)' : '请先选择课题'}
            >
              <Download size={13} />
              {exportingReport === 'html' ? '导出中…' : '合规报告 (HTML)'}
            </button>
          </div>
        </div>
      </header>

      {/* Lab Risk Panorama Stat Cards */}
      <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
        <div className="card p-5">
          <Stat value={summary.total_claims} label="总在研 Claim" />
        </div>
        <div className="card p-5 cursor-pointer hover:border-red-400 transition-colors" onClick={() => setUrgencyFilter(urgencyFilter === 'critical' ? 'all' : 'critical')}>
          <Stat value={summary.critical_claims} label="高危竞争冲突" tone="red" />
        </div>
        <div className="card p-5 cursor-pointer hover:border-amber-400 transition-colors" onClick={() => setUrgencyFilter(urgencyFilter === 'review' ? 'all' : 'review')}>
          <Stat value={summary.review_claims} label="边界待审 Claim" tone="orange" />
        </div>
        <div className="card p-5 cursor-pointer hover:border-green-400 transition-colors" onClick={() => setUrgencyFilter(urgencyFilter === 'clean' ? 'all' : 'clean')}>
          <Stat value={summary.clean_claims} label="安全/持续支持" tone="teal" />
        </div>
      </div>

      {/* Risk Filter Tabs */}
      <div className="flex flex-wrap items-center gap-2 pt-2">
        <span className="text-xs text-neutral-400 dark:text-white/60 mr-1">风险视图:</span>
        <button
          className={`rounded-full px-3 py-1 text-xs font-medium transition-colors ${
            urgencyFilter === 'all'
              ? 'bg-ink text-white dark:bg-white dark:text-ink'
              : 'border border-hairline text-neutral-600 hover:bg-neutral-50 dark:border-white/10 dark:text-white/80 dark:hover:bg-white/[0.04]'
          }`}
          onClick={() => setUrgencyFilter('all')}
        >
          全部 ({data.rows.length})
        </button>
        <button
          className={`rounded-full px-3 py-1 text-xs font-medium transition-colors ${
            urgencyFilter === 'critical'
              ? 'bg-red-600 text-white'
              : 'border border-hairline text-red-600 hover:bg-red-50 dark:border-red-500/20 dark:text-red-400'
          }`}
          onClick={() => setUrgencyFilter('critical')}
        >
          🚨 高危冲突 ({summary.critical_claims})
        </button>
        <button
          className={`rounded-full px-3 py-1 text-xs font-medium transition-colors ${
            urgencyFilter === 'review'
              ? 'bg-amber-600 text-white'
              : 'border border-hairline text-amber-600 hover:bg-amber-50 dark:border-amber-500/20 dark:text-amber-400'
          }`}
          onClick={() => setUrgencyFilter('review')}
        >
          ⚠️ 边界审查 ({summary.review_claims})
        </button>
        <button
          className={`rounded-full px-3 py-1 text-xs font-medium transition-colors ${
            urgencyFilter === 'clean'
              ? 'bg-teal-600 text-white'
              : 'border border-hairline text-teal-600 hover:bg-teal-50 dark:border-teal-500/20 dark:text-teal-400'
          }`}
          onClick={() => setUrgencyFilter('clean')}
        >
          ✓ 安全/支持 ({summary.clean_claims})
        </button>
      </div>

      {data.rows.length === 0 ? (
        <Empty text="还没有已确认或已编辑的 Claim。先在论文页完成 Claim 审核。" />
      ) : rows.length === 0 ? (
        <Empty text="当前筛选条件下没有匹配的 Claim。" />
      ) : (
        <div className="card overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full min-w-[1180px] border-collapse text-left text-[12px]">
              <thead className="border-b border-hairline bg-neutral-50 dark:border-white/[0.08] dark:bg-white/[0.03]">
                <tr>
                  {columns.map((column) => (
                    <th key={column.key} className="whitespace-nowrap px-3 py-3 font-medium text-neutral-500 dark:text-white/70">
                      {column.label}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => {
                  const meta = urgencyMeta(row.urgency);
                  const Icon = meta.icon;
                  return (
                    <tr
                      key={`${row.claim_id}-${row.stable_key}`}
                      className={`border-b border-hairline last:border-0 dark:border-white/[0.06] ${
                        row.urgency === 'critical'
                          ? 'bg-red-50/20 dark:bg-red-500/[0.03]'
                          : row.urgency === 'review'
                          ? 'bg-amber-50/20 dark:bg-amber-500/[0.03]'
                          : ''
                      }`}
                    >
                      {columns.map((column) => {
                        if (column.key === 'urgency') {
                          return (
                            <td key="urgency" className="px-3 py-3 align-top whitespace-nowrap">
                              <span className={`inline-flex items-center gap-1 ${meta.className}`}>
                                <Icon size={11} /> {meta.label}
                              </span>
                            </td>
                          );
                        }
                        if (column.key === 'review_state') {
                          return (
                            <td key="review_state" className="px-3 py-3 align-top">
                              <span className={`${stateClass(row.review_state)} font-mono`}>{row.review_state}</span>
                            </td>
                          );
                        }
                        if (column.key === 'statement') {
                          return (
                            <td key="statement" className="min-w-[280px] max-w-[340px] px-3 py-3 align-top text-[13px] leading-relaxed text-ink dark:text-white font-medium">
                              {row.statement}
                            </td>
                          );
                        }
                        return (
                          <td key={column.key} className={`max-w-[220px] px-3 py-3 align-top ${column.key === 'stable_key' ? 'font-mono text-[11px]' : 'text-neutral-600 dark:text-white/80'}`}>
                            {cell(row, column.key)}
                          </td>
                        );
                      })}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <div className="border-t border-hairline px-4 py-2.5 text-[11px] text-neutral-400 dark:border-white/[0.06] dark:text-white/60 flex items-center justify-between">
            <span>显示 {rows.length} / {data.rows.length} 条 Claim · 跨 {data.cases.length} 个课题聚合</span>
            <span>Research Radar Lab Panorama Engine</span>
          </div>
        </div>
      )}
    </div>
  );
}
