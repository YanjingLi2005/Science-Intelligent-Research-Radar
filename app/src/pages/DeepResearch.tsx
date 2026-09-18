import { useMemo, useState } from 'react';
import { ArrowRight, BookOpen, ExternalLink } from 'lucide-react';
import { useNavigate } from 'react-router';
import { listScans, type DeepResearchBrief, type ScanStatus, useApi } from '../api';
import { useProject } from '../contexts/ProjectContext';
import { Empty, Reveal, SectionHead } from '../components/chrome';
import { Spinner } from '../components/ui/spinner';
import ErrorRetry from '../components/ErrorRetry';

function briefFrom(scan: ScanStatus): DeepResearchBrief | null {
  const value = scan.stats?.research_brief;
  return value && typeof value === 'object' ? value as DeepResearchBrief : null;
}

function dateLabel(value: string | null | undefined): string {
  if (!value) return '时间未知';
  return new Date(value).toLocaleString('zh-CN', { dateStyle: 'medium', timeStyle: 'short' });
}

function costLabel(scan: ScanStatus): string {
  const stats = scan.stats ?? {};
  const value = scan.estimated_cost
    ?? (typeof stats.cost_usd === 'number' ? stats.cost_usd : undefined)
    ?? (typeof stats.total_cost_usd === 'number' ? stats.total_cost_usd : undefined);
  return typeof value === 'number' ? `$${value.toFixed(3)}` : '成本未知';
}

function statusMeta(status: string): { label: string; className: string } {
  if (status === 'completed') return { label: '已完成', className: 'badge-green badge-dot' };
  if (status === 'failed' || status === 'interrupted') return { label: '失败', className: 'badge-red badge-dot' };
  if (status === 'cancelled') return { label: '已取消', className: 'badge-gray badge-dot' };
  return { label: '进行中', className: 'badge-blue badge-dot' };
}

function BulletSection({ title, items, tone = 'normal' }: { title: string; items: string[]; tone?: 'normal' | 'warning' }) {
  if (!items.length) return null;
  return (
    <section>
      <div className="kicker mb-2">{title}</div>
      <ul className="space-y-2">
        {items.map((item, index) => (
          <li key={`${item}-${index}`} className={`text-[13px] leading-relaxed ${tone === 'warning' ? 'text-amber-700 dark:text-amber-300/90' : 'text-neutral-700 dark:text-white/90'}`}><span className="mr-2 text-neutral-400">·</span>{item}</li>
        ))}
      </ul>
    </section>
  );
}

function BriefDetail({ scan }: { scan: ScanStatus }) {
  const brief = briefFrom(scan);
  if (!brief) {
    const meta = statusMeta(scan.status);
    return (
      <div className="card flex min-h-[360px] flex-col items-center justify-center p-8 text-center">
        <BookOpen size={22} className="mb-3 text-neutral-300 dark:text-white/40" />
        <span className={meta.className}>{meta.label}</span>
        <p className="mt-3 max-w-sm text-[13px] leading-relaxed text-neutral-500 dark:text-white/75">{scan.error_message || '这次运行还没有可展示的研究简报。'}</p>
      </div>
    );
  }
  return (
    <div className="space-y-5">
      <div className="card p-6 md:p-7">
        <div className="flex items-start justify-between gap-4"><div><div className="kicker mb-2">Deep research brief</div><h2 className="display-md">{brief.title || '深度调研简报'}</h2></div><span className="badge-green badge-dot shrink-0">已完成</span></div>
        {brief.executive_summary && <p className="mt-5 border-l-2 border-[#4E8AFB]/50 pl-4 text-[14px] leading-7 text-neutral-700 dark:text-white/90">{brief.executive_summary}</p>}
      </div>
      {brief.sections?.length > 0 && <div className="card space-y-5 p-6 md:p-7">{brief.sections.map((section, index) => <section key={`${section.heading}-${index}`}><div className="kicker mb-2">{section.heading}</div><ul className="space-y-2">{section.findings.map((finding, findingIndex) => <li key={`${finding}-${findingIndex}`} className="text-[13px] leading-relaxed text-neutral-700 dark:text-white/90"><span className="mr-2 text-neutral-400">·</span>{finding}</li>)}</ul></section>)}</div>}
      <div className="card grid gap-6 p-6 md:grid-cols-2 md:p-7"><BulletSection title="关键洞察" items={brief.key_insights ?? []} /><BulletSection title="矛盾 / 张力" items={brief.contradictions ?? []} tone="warning" /><BulletSection title="研究空白" items={brief.research_gaps ?? []} /><BulletSection title="建议下一步" items={brief.recommended_next_steps ?? []} /></div>
      {brief.sources?.length > 0 && <div className="card p-6 md:p-7"><div className="kicker mb-3">来源论文 · {brief.sources.length}</div><div className="space-y-3">{brief.sources.map((source, index) => <article key={`${source.source_id}-${index}`} className="rounded-lg border border-hairline p-4 dark:border-white/[0.08]"><div className="flex items-start gap-3"><div className="min-w-0 flex-1"><h3 className="text-[13px] font-medium leading-relaxed text-neutral-800 dark:text-white/95">{source.title}</h3><p className="mt-1 text-[11.5px] text-neutral-500 dark:text-white/70">{source.authors?.join(', ') || '作者未知'}{source.year ? ` · ${source.year}` : ''}{source.venue ? ` · ${source.venue}` : ''}</p></div>{source.url && <a href={source.url} target="_blank" rel="noreferrer" className="shrink-0 text-[#4E8AFB]" aria-label={`打开来源：${source.title}`}><ExternalLink size={14} /></a>}</div>{source.relevance && <p className="mt-3 text-[12.5px] leading-relaxed text-neutral-600 dark:text-white/85">{source.relevance}</p>}{source.key_findings?.length > 0 && <div className="mt-3"><BulletSection title="核心发现" items={source.key_findings} /></div>}<div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-neutral-400 dark:text-white/60">{source.doi && <span>DOI: {source.doi}</span>}{source.url && <a href={source.url} target="_blank" rel="noreferrer" className="text-[#4E8AFB] hover:underline">查看原文</a>}</div></article>)}</div></div>}
      <BulletSection title="注意事项" items={brief.warnings ?? []} tone="warning" />
    </div>
  );
}

export default function DeepResearch() {
  const navigate = useNavigate();
  const { project, projectId, loading: projectLoading } = useProject();
  const { data: scans, loading, error, refetch } = useApi(() => listScans(projectId), [projectId]);
  const deepRuns = useMemo(() => (scans ?? []).filter((scan) => scan.mode === 'deep_research').sort((a, b) => Date.parse(b.started_at) - Date.parse(a.started_at)), [scans]);
  const [selectedId, setSelectedId] = useState('');
  const selected = deepRuns.find((run) => run.id === selectedId) ?? deepRuns[0];

  if (projectLoading || loading) return <div aria-busy="true" aria-live="polite" className="flex justify-center py-24"><Spinner className="size-8 text-neutral-400" /></div>;
  if (error) return <div className="card p-8"><ErrorRetry message={`加载失败：${error.message}`} onRetry={() => void refetch()} /></div>;
  return (
    <div><Reveal><SectionHead kicker="Deep research" title="深度调研历史" right={<span className="locator">{deepRuns.length} 次运行</span>} /><p className="text-sm leading-relaxed text-neutral-500 dark:text-white/80">查看这个项目历次深度调研生成的结构化研究简报。</p>{project?.question && <p className="mt-2 text-[12px] text-neutral-400 dark:text-white/65">研究问题：{project.question}</p>}</Reveal>
      {deepRuns.length === 0 ? <Reveal delay={80} className="mt-8"><Empty text="还没有深度调研记录" /><div className="mt-5 flex justify-center"><button className="btn-primary" onClick={() => navigate(`/cases/${projectId}/radar`)}><BookOpen size={14} /> 发起深度调研 <ArrowRight size={14} /></button></div></Reveal> :
        <Reveal delay={80} className="mt-8"><div className="grid gap-5 lg:grid-cols-[280px_minmax(0,1fr)]"><aside className="card h-fit overflow-hidden p-2 lg:sticky lg:top-24"><div className="px-3 py-2"><div className="kicker">Run history</div></div><div className="space-y-1">{deepRuns.map((run) => { const meta = statusMeta(run.status); const brief = briefFrom(run); return <button key={run.id} onClick={() => setSelectedId(run.id)} className={`w-full rounded-lg p-3 text-left transition-colors ${run.id === selected?.id ? 'bg-neutral-100 dark:bg-white/[0.08]' : 'hover:bg-neutral-50 dark:hover:bg-white/[0.04]'}`}><div className="flex items-start justify-between gap-2"><span className="line-clamp-2 text-[12.5px] font-medium leading-relaxed">{brief?.title || '未命名深度调研'}</span><span className={`${meta.className} shrink-0 !text-[9px]`}>{meta.label}</span></div><div className="mt-2 text-[11px] text-neutral-400 dark:text-white/65">{dateLabel(run.finished_at || run.started_at)}</div><div className="mt-1 flex items-center justify-between gap-2"><span className="locator !text-[10px]">{costLabel(run)}</span><span className="locator !text-[10px]">{run.status}</span></div></button>; })}</div></aside><main className="min-w-0"><BriefDetail scan={selected} /></main></div></Reveal>}
    </div>
  );
}
