import { useEffect, useRef, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router';
import { ArrowRight, FileText, Plus, Radar, Trash2, FlaskConical } from 'lucide-react';
import { useProject } from '../contexts/ProjectContext';
import { createCase, deleteCase } from '../api';
import { useLanguage } from '../contexts/languageState';

export default function HomeView() {
  const { locale } = useLanguage();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const { caseList, refreshCaseList, loading, error } = useProject();
  const [title, setTitle] = useState('');
  const [question, setQuestion] = useState('');
  const [fileName, setFileName] = useState('');
  const [created, setCreated] = useState<'idle' | 'submitting' | 'done' | 'error'>('idle');
  const [deleting, setDeleting] = useState<string | null>(null);
  const [operationError, setOperationError] = useState('');
  const fileRef = useRef<HTMLInputElement>(null);
  const titleRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (searchParams.get('new') === '1') {
      // focus the composer after mount
      requestAnimationFrame(() => titleRef.current?.focus());
    }
  }, [searchParams]);

  const handleCreate = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const form = e.currentTarget;
    const manuscript = fileRef.current?.files?.[0];
    if (!title.trim() || !manuscript) return;

    setCreated('submitting');
    setOperationError('');
    try {
      const createdCase = await createCase(title.trim(), question.trim(), manuscript);
      setCreated('done');
      // The case already exists even if refreshing the sidebar list fails.
      try {
        await refreshCaseList();
      } catch {
        // The project route loads the new case directly by id.
      }
      form.reset();
      setTitle('');
      setQuestion('');
      setFileName('');
      navigate(`/cases/${createdCase.id}`);
    } catch (err) {
      setCreated('error');
      setOperationError(err instanceof Error ? err.message : String(err));
    }
  };

  const handleDelete = async (id: string, name: string) => {
    if (!confirm(locale === 'en'
      ? `Delete watch “${name}”? This action cannot be undone.`
      : `确定要删除监控对象「${name}」吗？此操作不可撤销。`)) return;
    setDeleting(id);
    setOperationError('');
    try {
      await deleteCase(id);
      await refreshCaseList();
    } catch (err) {
      setOperationError(err instanceof Error ? err.message : String(err));
    } finally {
      setDeleting(null);
    }
  };

  return (
    <div className="relative mx-auto max-w-[880px] pb-16">
      <div className="pointer-events-none absolute inset-x-0 top-0 h-[520px] bg-[radial-gradient(ellipse_55%_55%_at_50%_0%,rgba(0,0,0,0.04),transparent)] dark:bg-[radial-gradient(ellipse_55%_55%_at_50%_0%,rgba(255,255,255,0.055),transparent)]" aria-hidden="true" />

      <section className="relative pt-4 md:pt-8" aria-labelledby="home-title">
        <div className="border-b border-black/10 pb-8 dark:border-white/10">
          <p className="mb-4 flex items-center gap-3 text-[11px] font-medium uppercase tracking-[0.2em] text-neutral-500 dark:text-white/45">
            <span className="h-px w-7 bg-black/30 dark:bg-white/40" aria-hidden="true" /> Research workspace
          </p>
          <h1 id="home-title" className="text-[clamp(2rem,3.2vw,3rem)] font-semibold leading-[1.16] tracking-[-0.045em]">
            你的研究监控台<span className="text-neutral-400 dark:text-white/45">。</span>
          </h1>
          <p className="mt-4 max-w-2xl text-[14px] leading-7 text-neutral-600 dark:text-white/55">
            从论文出发，持续关注新文献对核心主张的影响。创建一个监控对象，开始整理证据与下一步行动。
          </p>
        </div>

        <form className="relative mt-8 overflow-hidden rounded-[1.75rem] border border-black/10 bg-white/85 p-6 shadow-[0_24px_90px_rgba(0,0,0,0.08)] backdrop-blur-xl dark:border-white/10 dark:bg-white/[0.035] dark:shadow-[0_24px_90px_rgba(0,0,0,0.25)] sm:p-8" onSubmit={handleCreate}>
          <div className="pointer-events-none absolute inset-x-0 top-0 h-32 bg-[radial-gradient(ellipse_65%_100%_at_50%_0%,rgba(0,0,0,0.025),transparent)] dark:bg-[radial-gradient(ellipse_65%_100%_at_50%_0%,rgba(255,255,255,0.07),transparent)]" aria-hidden="true" />
          <div className="relative">
            <span className="text-[10px] font-medium uppercase tracking-[0.2em] text-neutral-400 dark:text-white/40">New watch</span>
            <h2 className="mt-2 text-[22px] font-semibold tracking-tight">创建新的监控对象</h2>
            <p className="mt-2 text-[13px] leading-relaxed text-neutral-500 dark:text-white/50">上传论文后，系统会提取需要你确认的核心主张。</p>

            <div className="mt-7 grid gap-5 md:grid-cols-2">
              <div>
                <label htmlFor="watch-title" className="mb-2 block text-[12px] font-medium text-neutral-700 dark:text-white/75">论文标题或监控名称</label>
                <input
                  id="watch-title"
                  ref={titleRef}
                  value={title}
                  onChange={(e) => setTitle(e.target.value)}
                  placeholder="输入论文标题"
                  disabled={created === 'submitting'}
                  className="w-full rounded-xl border border-black/10 bg-black/[0.025] px-4 py-3 text-sm text-ink outline-none transition-colors placeholder:text-neutral-400 focus:border-black/35 focus:bg-white dark:border-white/10 dark:bg-white/[0.045] dark:text-white dark:placeholder:text-white/30 dark:focus:border-white/40 dark:focus:bg-white/[0.07]"
                />
              </div>
              <div>
                <label htmlFor="watch-question" className="mb-2 block text-[12px] font-medium text-neutral-700 dark:text-white/75">核心研究问题 <span className="font-normal text-neutral-400 dark:text-white/40">（可选）</span></label>
                <textarea
                  id="watch-question"
                  value={question}
                  onChange={(e) => setQuestion(e.target.value)}
                  placeholder="雷达将围绕它提炼检索主题"
                  rows={2}
                  disabled={created === 'submitting'}
                  className="w-full resize-none rounded-xl border border-black/10 bg-black/[0.025] px-4 py-3 text-sm leading-relaxed text-ink outline-none transition-colors placeholder:text-neutral-400 focus:border-black/35 focus:bg-white dark:border-white/10 dark:bg-white/[0.045] dark:text-white dark:placeholder:text-white/30 dark:focus:border-white/40 dark:focus:bg-white/[0.07]"
                />
              </div>
              <div className="md:col-span-2">
                <span className="mb-2 block text-[12px] font-medium text-neutral-700 dark:text-white/75">研究文稿</span>
                <button
                  type="button"
                  onClick={() => fileRef.current?.click()}
                  disabled={created === 'submitting'}
                  className="flex w-full min-w-0 items-center gap-2 rounded-xl border border-dashed border-black/15 bg-black/[0.015] px-4 py-3 text-left text-[12.5px] text-neutral-600 transition-colors hover:border-black/30 hover:bg-black/[0.035] dark:border-white/15 dark:bg-white/[0.025] dark:text-white/65 dark:hover:border-white/30 dark:hover:bg-white/[0.05]"
                >
                  {fileName ? <FileText size={15} className="shrink-0" /> : <Plus size={15} className="shrink-0" />}
                  <span className="truncate">{fileName || '上传 .tex、.md 或 .pdf 文件'}</span>
                </button>
                <input
                  ref={fileRef}
                  type="file"
                  accept=".tex,.md,.pdf"
                  className="sr-only"
                  tabIndex={-1}
                  disabled={created === 'submitting'}
                  onChange={(e) => setFileName(e.target.files?.[0]?.name ?? '')}
                />
              </div>
            </div>

            <div className="mt-6 flex flex-wrap items-center justify-between gap-4 border-t border-black/10 pt-5 dark:border-white/10">
              <span className="text-[11px] text-neutral-500 dark:text-white/45">支持 .tex、.md、.pdf 文稿</span>
              <button
                type="submit"
                disabled={!title.trim() || !fileName || created === 'submitting'}
                className="group flex items-center justify-center gap-2 rounded-xl bg-[#18181B] px-5 py-3 text-[13px] font-semibold text-white transition-all hover:bg-[#303034] disabled:opacity-40 dark:bg-white dark:text-[#111113] dark:hover:bg-white/90"
              >
                {created === 'submitting' ? '上传并创建中…' : '上传论文并创建监控'}
                {created !== 'submitting' && <ArrowRight size={16} className="transition-transform group-hover:translate-x-0.5" aria-hidden="true" />}
              </button>
            </div>
          </div>
        </form>
      </section>

      {(operationError || error) && (
        <div className="mt-3 rounded-lg border border-red-200 bg-red-50 px-3.5 py-2.5 text-[12.5px] text-red-700 dark:border-red-500/25 dark:bg-red-500/10 dark:text-red-300">
          {operationError || error?.message}
        </div>
      )}
      {created === 'done' && (
        <p className="badge-green badge-dot mt-3">已加入监控 · Claim 提取任务已排队</p>
      )}

      {/* monitoring list */}
      <div className="relative mt-16 flex items-end justify-between gap-4 border-b border-black/10 pb-4 dark:border-white/10">
        <div>
          <p className="text-[10px] font-medium uppercase tracking-[0.2em] text-neutral-400 dark:text-white/40">Your research</p>
          <h2 className="mt-2 text-[21px] font-semibold tracking-tight">
            正在监控
          </h2>
        </div>
        <div className="flex items-center gap-3">
          {caseList.length > 0 && (
            <button onClick={() => navigate('/lab/claim-matrix')} className="btn-ghost !px-2.5 !py-1.5 !text-[12px]">
              <FlaskConical size={13} /> 课题组矩阵
            </button>
          )}
          <span className="text-[11px] text-neutral-500 dark:text-white/45">{caseList.length} 个对象 · 定期雷达扫描</span>
        </div>
      </div>

      {loading && caseList.length === 0 && (
        <div className="mt-5 space-y-3" aria-busy="true">
          {Array.from({ length: 3 }, (_, i) => (
            <div key={i} className="h-[88px] animate-pulse rounded-2xl bg-neutral-100 dark:bg-white/[0.05]" />
          ))}
        </div>
      )}

      {!loading && caseList.length === 0 && (
        <div className="mt-5 rounded-[1.5rem] border border-dashed border-black/15 bg-white/40 px-6 py-12 text-center dark:border-white/10 dark:bg-white/[0.02]">
          <span className="mx-auto flex size-11 items-center justify-center rounded-xl border border-black/10 bg-black/[0.035] dark:border-white/10 dark:bg-white/[0.05]">
            <Radar size={19} strokeWidth={1.7} className="text-neutral-500 dark:text-white/55" aria-hidden="true" />
          </span>
          <h3 className="mt-5 text-[16px] font-semibold tracking-tight">还没有监控对象</h3>
          <p className="mx-auto mt-2 max-w-md text-[13px] leading-6 text-neutral-500 dark:text-white/50">
            在上方上传论文并创建监控。确认系统提取的主张后，雷达就会持续追踪相关文献。
          </p>
        </div>
      )}

      <div className="mt-5 grid gap-3 md:grid-cols-2">
        {caseList.map((p) => (
          <div
            key={p.id}
            className="group flex items-center gap-2 rounded-2xl border border-black/10 bg-white/75 px-5 py-4 transition-colors hover:border-black/20 hover:bg-white dark:border-white/10 dark:bg-white/[0.035] dark:hover:border-white/20 dark:hover:bg-white/[0.06]"
          >
            <button onClick={() => navigate(`/cases/${p.id}`)} className="min-w-0 flex-1 text-left">
              <div className="flex items-center gap-2">
                <span
                  className={`status-dot ${p.urgent > 0 ? 'red pulse' : 'gray'}`}
                  title={p.urgent > 0 ? `${p.urgent} 条紧急影响` : '常规监控中'}
                />
                <span className="truncate text-[14px] font-medium">{p.name}</span>
                {p.urgent > 0 && <span className="badge-red badge-dot shrink-0">{p.urgent} 紧急</span>}
                <span className="locator shrink-0 hidden sm:inline">{p.version}</span>
              </div>
              <div className="mt-0.5 truncate pl-[18px] text-[12px] text-neutral-500 dark:text-white/80">
                {p.question || p.file} · 上次扫描 {p.lastScan}
              </div>
              {p.topics.length > 0 && (
                <div className="mt-1.5 flex flex-wrap gap-1 pl-[18px]">
                  {p.topics.slice(0, 4).map((t) => (
                    <span key={t} className="chip font-mono !text-[10px]">{t}</span>
                  ))}
                  {p.topics.length > 4 && (
                    <span className="text-[10px] text-neutral-400 dark:text-white/70">+{p.topics.length - 4}</span>
                  )}
                </div>
              )}
            </button>
            <button
              onClick={() => handleDelete(p.id, p.name)}
              disabled={deleting === p.id}
              aria-label={`删除监控对象 ${p.name}`}
              title="删除监控对象"
              className="shrink-0 rounded-md p-2 text-neutral-300 opacity-0 transition-all hover:bg-red-50 hover:text-red-600 group-hover:opacity-100 disabled:opacity-40 dark:text-white/60 dark:hover:bg-red-500/10 dark:hover:text-red-400"
            >
              {deleting === p.id ? <span className="text-[11px]">删除中…</span> : <Trash2 size={14} />}
            </button>
          </div>
        ))}
      </div>

    </div>
  );
}
