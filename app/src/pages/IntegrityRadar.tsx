import { useState } from 'react';
import { Download, ExternalLink, FileText, FileWarning, ShieldCheck } from 'lucide-react';
import { downloadComplianceReport, getIntegrityRadar, useApi } from '../api';
import { useProject } from '../contexts/ProjectContext';
import { Empty, Reveal, SectionHead, Stat } from '../components/chrome';
import ErrorRetry from '../components/ErrorRetry';
import { Spinner } from '../components/ui/spinner';
import { toast } from 'sonner';

const stateMeta: Record<string, { label: string; className: string }> = {
  retracted: { label: '已撤稿', className: 'badge-red' },
  expression_of_concern: { label: '关注声明', className: 'badge-orange' },
  corrected: { label: '已更正', className: 'badge-gray' },
};

export default function IntegrityRadar() {
  const { projectId, loading: projectLoading } = useProject();
  const { data, loading, error, refetch } = useApi(() => getIntegrityRadar(projectId), [projectId]);
  const [downloading, setDownloading] = useState<string | null>(null);

  const handleDownload = async (format: 'markdown' | 'html') => {
    setDownloading(format);
    try {
      await downloadComplianceReport(projectId, format);
      toast.success(`科研合规审计报告 (${format.toUpperCase()}) 已导出`);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '导出报告失败');
    } finally {
      setDownloading(null);
    }
  };

  if (projectLoading || loading) {
    return <div className="flex justify-center py-24" aria-busy="true"><Spinner className="size-8 text-neutral-400" /></div>;
  }
  if (error) {
    return <div className="card p-8"><ErrorRetry message={`诚信雷达加载失败：${error.message}`} onRetry={() => void refetch()} /></div>;
  }
  if (!data) return null;

  const counts = data.counts;
  const hasRisks = data.flagged_sources.length > 0 || data.retraction_impacts.length > 0;

  return (
    <div>
      <Reveal>
        <SectionHead
          kicker="Integrity · Retraction Radar"
          title="诚信雷达"
          right={
            <div className="flex items-center gap-2">
              <span className="badge-teal"><ShieldCheck size={13} /> 只读监测</span>
              <button
                className="btn-ghost text-xs flex items-center gap-1.5"
                disabled={Boolean(downloading)}
                onClick={() => void handleDownload('markdown')}
              >
                <FileText size={13} />
                {downloading === 'markdown' ? '生成中…' : '导出合规报告 (MD)'}
              </button>
              <button
                className="btn-primary text-xs flex items-center gap-1.5"
                disabled={Boolean(downloading)}
                onClick={() => void handleDownload('html')}
              >
                <Download size={13} />
                {downloading === 'html' ? '生成中…' : '导出合规报告 (HTML)'}
              </button>
            </div>
          }
        />
        <p className="max-w-2xl text-sm leading-relaxed text-neutral-500 dark:text-white/80">
          汇总本项目引用来源的撤稿、关注声明与更正状态，并标出可能受影响的 Claim 和研究影响。支持一键导出完整《科研诚信与引用核查报告》。
        </p>
      </Reveal>

      <Reveal delay={60} className="mt-8">
        <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
          <div className="card p-5"><Stat value={counts.retracted} label="已撤稿" tone="red" /></div>
          <div className="card p-5"><Stat value={counts.expression_of_concern} label="关注声明" tone="orange" /></div>
          <div className="card p-5"><Stat value={counts.corrected} label="已更正" tone="teal" /></div>
          <div className="card p-5"><Stat value={counts.normal} label="正常来源" /></div>
        </div>
      </Reveal>

      {!hasRisks ? (
        <Reveal delay={100} className="mt-8"><Empty text="暂无诚信风险" /></Reveal>
      ) : (
        <>
          <Reveal delay={100} className="mt-10">
            <SectionHead kicker="High risk sources" title="风险来源" />
            <div className="space-y-3">
              {data.flagged_sources.map((source) => {
                const meta = stateMeta[source.integrity_state] ?? { label: source.integrity_state, className: 'badge-gray' };
                const pubPeer = source.doi ? `https://pubpeer.com/search?q=${encodeURIComponent(source.doi)}` : null;
                return (
                  <article key={source.source_id} className="card card-hover p-5">
                    <div className="flex flex-wrap items-start justify-between gap-3">
                      <div className="flex min-w-0 items-start gap-3">
                        <FileWarning size={17} className="mt-0.5 shrink-0 text-red-500" />
                        <div className="min-w-0">
                          <h3 className="text-[14px] font-semibold leading-snug">{source.title || '未命名来源'}</h3>
                          <div className="mt-2 flex flex-wrap items-center gap-2 text-[11px] text-neutral-500 dark:text-white/70">
                            <span className={meta.className}>{meta.label}</span>
                            {source.doi && <span className="font-mono break-all">DOI: {source.doi}</span>}
                            {pubPeer && <a className="inline-flex items-center gap-1 text-teal hover:underline" href={pubPeer} target="_blank" rel="noreferrer">PubPeer <ExternalLink size={11} /></a>}
                          </div>
                        </div>
                      </div>
                      <span className="locator shrink-0">{source.snapshot_count} 个快照</span>
                    </div>
                    <div className="mt-4 flex flex-wrap gap-x-5 gap-y-1 border-t border-hairline pt-3 text-[11.5px] text-neutral-500 dark:border-white/[0.08] dark:text-white/75">
                      <span>相关 Claim：{source.related_claim_ids.length ? source.related_claim_ids.join('、') : '—'}</span>
                      <span>相关影响：{source.related_impact_ids.length ? source.related_impact_ids.join('、') : '—'}</span>
                    </div>
                  </article>
                );
              })}
            </div>
          </Reveal>

          {data.retraction_impacts.length > 0 && (
            <Reveal delay={140} className="mt-10">
              <SectionHead kicker="Research integrity impacts" title="撤稿影响" />
              <div className="card divide-y divide-hairline dark:divide-white/[0.08]">
                {data.retraction_impacts.map((impact) => (
                  <div key={impact.id} className="flex flex-wrap items-center justify-between gap-3 p-4">
                    <div>
                      <div className="text-[13px] font-medium">{impact.title || impact.source_title}</div>
                      <div className="mt-1 text-[11px] text-neutral-500 dark:text-white/70">来源：{impact.source_title} · {impact.id}</div>
                    </div>
                    <span className="badge-orange">{impact.state || 'candidate'}</span>
                  </div>
                ))}
              </div>
            </Reveal>
          )}
        </>
      )}
    </div>
  );
}
