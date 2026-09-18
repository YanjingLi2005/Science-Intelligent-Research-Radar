import { useState } from 'react';
import { Check, Clipboard, Download, FileText } from 'lucide-react';
import { getDigest, useApi } from '../api';
import { useProject } from '../contexts/ProjectContext';
import { Reveal, SectionHead } from '../components/chrome';
import ErrorRetry from '../components/ErrorRetry';
import { Spinner } from '../components/ui/spinner';

function dateLabel(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleString('zh-CN', { dateStyle: 'medium', timeStyle: 'short' });
}

export default function Digest() {
  const { projectId, loading: projectLoading } = useProject();
  const { data: digest, loading, error, refetch } = useApi(() => getDigest(projectId), [projectId]);
  const [copied, setCopied] = useState(false);

  const downloadMarkdown = () => {
    if (!digest) return;
    const blob = new Blob([digest.markdown], { type: 'text/markdown;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = `${digest.title || 'research-radar-digest'}.md`;
    anchor.click();
    URL.revokeObjectURL(url);
  };

  const copyMarkdown = async () => {
    if (!digest) return;
    try {
      await navigator.clipboard.writeText(digest.markdown);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1800);
    } catch {
      setCopied(false);
    }
  };

  if (projectLoading || loading) {
    return (
      <div aria-busy="true" aria-live="polite" className="flex justify-center py-24">
        <Spinner className="size-8 text-neutral-400" />
      </div>
    );
  }

  if (error) {
    return <div className="card p-8"><ErrorRetry message={`周报加载失败：${error.message}`} onRetry={() => void refetch()} /></div>;
  }

  if (!digest) {
    return <div className="card p-8 text-center text-sm text-neutral-500 dark:text-white/75">暂无可阅读的周报。</div>;
  }

  return (
    <div>
      <Reveal>
        <SectionHead
          kicker="Weekly digest · Radar"
          title={digest.title || '雷达周报'}
          right={
            <div className="flex flex-wrap justify-end gap-2">
              <button type="button" className="btn-secondary" onClick={copyMarkdown}>
                {copied ? <Check size={14} /> : <Clipboard size={14} />}
                {copied ? '已复制' : '复制 Markdown'}
              </button>
              <button type="button" className="btn-primary" onClick={downloadMarkdown}>
                <Download size={14} /> 下载 Markdown
              </button>
            </div>
          }
        />
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[12px] text-neutral-500 dark:text-white/75">
          <span>生成于 {dateLabel(digest.generated_at)}</span>
          <span className="text-neutral-300 dark:text-white/30">·</span>
          <span>{digest.sections.length} 个情报模块</span>
        </div>
      </Reveal>

      <Reveal delay={70} className="mt-8">
        <div className="mb-4 flex items-center gap-2 text-[12px] text-neutral-500 dark:text-white/70">
          <FileText size={14} className="text-teal" />
          <span>本周情报摘要</span>
        </div>
        <div className="grid gap-4 md:grid-cols-2">
          {digest.sections.map((section, index) => (
            <article key={`${section.heading}-${index}`} className="card card-hover p-6">
              <div className="mb-4 flex items-start gap-3">
                <span className="flex size-7 shrink-0 items-center justify-center rounded-md bg-teal/10 font-mono text-[11px] text-teal dark:bg-teal/15">
                  {String(index + 1).padStart(2, '0')}
                </span>
                <h2 className="pt-1 text-[15px] font-semibold tracking-tight">{section.heading}</h2>
              </div>
              {section.items.length > 0 ? (
                <ul className="space-y-3 border-t border-hairline pt-4 dark:border-white/[0.08]">
                  {section.items.map((item, itemIndex) => (
                    <li key={`${item}-${itemIndex}`} className="flex gap-2.5 text-[13px] leading-relaxed text-neutral-700 dark:text-white/90">
                      <span className="mt-[0.55em] size-1.5 shrink-0 rounded-full bg-teal/70" />
                      <span>{item}</span>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="border-t border-hairline pt-4 text-[13px] text-neutral-400 dark:border-white/[0.08] dark:text-white/60">本模块暂无更新。</p>
              )}
            </article>
          ))}
        </div>
      </Reveal>
    </div>
  );
}
