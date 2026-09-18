import { useEffect, useMemo, useState, type ChangeEvent } from 'react';
import { ArrowRight, CheckCircle2, ExternalLink, FileCheck2, Link2 } from 'lucide-react';
import {
  checkCitationSupport,
  checkReferences,
  checkReferencesFile,
  type CitationSupportResult,
  type ReferenceCheckResult,
  type ReferenceMatch,
  type ReferenceCheckBatch,
} from '../api';
import ErrorRetry from '../components/ErrorRetry';
import { Empty, Reveal, SectionHead } from '../components/chrome';
import { useProject } from '../contexts/ProjectContext';
import { Spinner } from '../components/ui/spinner';
import { Textarea } from '../components/ui/textarea';

type ReferenceFormat = 'auto' | 'bibtex' | 'text';
type MatchedReferenceResult = ReferenceCheckResult & { matched: ReferenceMatch };

const referenceReasonLabels: Record<string, string> = {
  ambiguous_match: '命中记录存在歧义',
  metadata_incomplete: '参考文献元数据不完整',
  not_found: '未在已查询来源中找到匹配记录',
  source_unavailable: '部分外部来源暂时不可用',
  title_match_low: '标题匹配度偏低',
};

const supportReasonLabels: Record<string, string> = {
  abstract_only_evidence_quality_limited: '仅使用摘要，证据质量受到限制',
  citation_check_failed: '引文支持度核验过程异常',
  empty_claim: '引用句为空或无法提取主张',
  full_text_unavailable: '未能获取论文全文或摘要',
  no_relevant_passage: '未检索到与主张相关的段落',
};

const recommendedActionLabels: Record<string, string> = {
  add_missing_qualifier: '补充必要限定语',
  cite_as_supported: '可按当前证据保留引用',
  fetch_full_text: '补充或获取全文',
  quote_exact_condition: '引用准确的实验条件或适用范围',
  remove_or_rephrase_claim: '删除或改写当前主张',
  replace_citation: '更换支持更充分的引用',
  retry_with_full_text: '使用全文重试',
  use_abstract_only: '仅在明确标注限制时使用摘要',
  verify_manually: '人工核对原文',
};

const supportCategoryMeta: Record<CitationSupportResult['category'], { label: string; className: string }> = {
  supported: { label: 'Supported · 支持', className: 'badge-green' },
  partially_supported: { label: 'Partially supported · 部分支持', className: 'badge-orange' },
  unsupported: { label: 'Unsupported · 不支持', className: 'badge-red' },
  uncertain: { label: 'Uncertain · 不确定', className: 'badge-gray' },
};

const fullTextStatusLabels: Record<CitationSupportResult['full_text_status'], string> = {
  full_text: '全文',
  abstract_only: '仅摘要',
  unavailable: '不可用',
};

function confidenceLabel(value: number): string {
  const confidence = Math.max(0, Math.min(1, Number.isFinite(value) ? value : 0));
  return `${Math.round(confidence * 100)}%`;
}

function withChinesePunctuation(value: string): string {
  return /[。！？；]$/.test(value) ? value : `${value}。`;
}

function reasonLabel(reason: string, labels: Record<string, string>): string {
  return withChinesePunctuation(labels[reason] ?? reason.replaceAll('_', ' '));
}

function isMatchedReference(result: ReferenceCheckResult): result is MatchedReferenceResult {
  return result.matched !== null;
}

function referenceDetails(result: ReferenceCheckResult): string {
  const details = [
    result.entry.authors.length > 0 ? result.entry.authors.join('、') : null,
    result.entry.year ? String(result.entry.year) : null,
    result.entry.venue,
  ].filter(Boolean);
  return details.join(' · ');
}

export default function CitationCheck() {
  const { projectId, loading: projectLoading } = useProject();
  const [references, setReferences] = useState('');
  const [format, setFormat] = useState<ReferenceFormat>('auto');
  const [referenceBatch, setReferenceBatch] = useState<ReferenceCheckBatch | null>(null);
  const [referenceLoading, setReferenceLoading] = useState(false);
  const [referenceError, setReferenceError] = useState<string | null>(null);
  const [selectedReferenceIndex, setSelectedReferenceIndex] = useState<number | null>(null);
  const [citationSentence, setCitationSentence] = useState('');
  const [fullText, setFullText] = useState('');
  const [abstract, setAbstract] = useState('');
  const [supportResult, setSupportResult] = useState<CitationSupportResult | null>(null);
  const [supportLoading, setSupportLoading] = useState(false);
  const [supportError, setSupportError] = useState<string | null>(null);

  const matchedResults = useMemo(
    () => referenceBatch?.results.filter(isMatchedReference) ?? [],
    [referenceBatch],
  );
  const selectedReference = selectedReferenceIndex === null
    ? null
    : matchedResults[selectedReferenceIndex] ?? null;

  useEffect(() => {
    setSelectedReferenceIndex((current) => {
      if (matchedResults.length === 0) return null;
      return current !== null && current < matchedResults.length ? current : 0;
    });
    setSupportResult(null);
    setSupportError(null);
  }, [matchedResults]);

  const handleCheckReferences = async () => {
    if (referenceLoading) return;
    if (!projectId) {
      setReferenceError('当前项目仍在加载，请稍后重试');
      return;
    }
    if (!references.trim()) {
      setReferenceError('请先粘贴至少一条参考文献');
      return;
    }

    setReferenceLoading(true);
    setReferenceError(null);
    try {
      const batch = await checkReferences(projectId, {
        format,
        references: references.trim(),
      });
      setReferenceBatch(batch);
    } catch (caught) {
      setReferenceError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setReferenceLoading(false);
    }
  };

  const handleFileUpload = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file || !projectId) return;
    if (referenceLoading) return;
    setReferenceLoading(true);
    setReferenceError(null);
    try {
      const batch = await checkReferencesFile(projectId, file);
      setReferences(file.name);
      setReferenceBatch(batch);
    } catch (caught) {
      setReferenceError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setReferenceLoading(false);
      event.target.value = '';
    }
  };

  const chooseReference = (result: MatchedReferenceResult) => {
    setSelectedReferenceIndex(matchedResults.indexOf(result));
    setSupportResult(null);
    setSupportError(null);
    document.getElementById('citation-support-form')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  };

  const handleCheckSupport = async () => {
    if (supportLoading) return;
    if (!projectId) {
      setSupportError('当前项目仍在加载，请稍后重试');
      return;
    }
    if (!selectedReference) {
      setSupportError('请先在第一层选择一条已命中的文献');
      return;
    }
    if (!citationSentence.trim()) {
      setSupportError('请输入需要核验的引用句');
      return;
    }

    setSupportLoading(true);
    setSupportError(null);
    try {
      const batch = await checkCitationSupport(projectId, {
        inputs: [{
          reference: selectedReference.matched,
          citation_sentence: citationSentence.trim(),
          full_text: fullText.trim() || null,
          abstract: abstract.trim() || null,
        }],
      });
      const result = batch.results[0];
      if (!result) {
        setSupportError('接口未返回支持度结果，请稍后重试');
        setSupportResult(null);
      } else {
        setSupportResult(result);
      }
    } catch (caught) {
      setSupportError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setSupportLoading(false);
    }
  };

  return (
    <div>
      <Reveal>
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <div className="kicker mb-1.5">Citation Check · Verification-first</div>
            <h1 className="display-md">引文核验</h1>
            <p className="mt-3 max-w-2xl text-sm leading-relaxed text-neutral-500 dark:text-white/80">
              先核验参考文献是否有可靠外部记录，再判断引用句是否被原文支持。Unverified 不等于假，结果应作为人工复核的起点。
            </p>
          </div>
          <span className="badge-teal"><FileCheck2 size={13} /> 项目级核验</span>
        </div>
      </Reveal>

      <Reveal delay={60} className="mt-10">
        <SectionHead
          kicker="Layer 1 · Reference validity"
          title="第一层：参考文献真实性"
          right={referenceBatch && <span className="locator">{referenceBatch.summary.total ?? referenceBatch.results.length} 条 · {referenceBatch.checked_at}</span>}
        />
        <div className="card p-6">
          <div className="grid gap-4 md:grid-cols-[minmax(0,1fr)_168px]">
            <div>
              <label htmlFor="references-input" className="mb-2 block text-xs font-medium text-ink dark:text-white">参考文献内容</label>
              <Textarea
                id="references-input"
                value={references}
                onChange={(event) => setReferences(event.target.value)}
                placeholder="粘贴 BibTeX、纯文本参考文献，或直接上传摘要中的 References 部分"
                className="min-h-40 resize-y text-[13px] leading-relaxed"
              />
            </div>
            <div>
              <label htmlFor="reference-format" className="mb-2 block text-xs font-medium text-ink dark:text-white">输入格式</label>
              <select
                id="reference-format"
                value={format}
                onChange={(event) => setFormat(event.target.value as ReferenceFormat)}
                className="input"
              >
                <option value="auto">自动识别</option>
                <option value="bibtex">BibTeX</option>
                <option value="text">纯文本</option>
              </select>
              <p className="mt-2 text-[11px] leading-relaxed text-neutral-400 dark:text-white/65">服务端会按格式解析后查询公开书目来源。</p>
            </div>
          </div>
          <div className="mt-4">
            <label htmlFor="references-file" className="mb-2 block text-xs font-medium text-ink dark:text-white">或上传参考文献文件</label>
            <input
              id="references-file"
              type="file"
              accept=".bib,.pdf,.tex,.md,.markdown,.txt"
              onChange={(event) => void handleFileUpload(event)}
              disabled={referenceLoading || projectLoading}
              className="block w-full text-xs text-neutral-500 dark:text-white/75 file:mr-3 file:rounded-md file:border-0 file:bg-neutral-100 file:px-3 file:py-1.5 file:text-xs file:font-medium file:text-ink dark:file:bg-white/10 dark:file:text-white"
            />
            <p className="mt-1.5 text-[11px] text-neutral-400 dark:text-white/65">支持 BibTeX、PDF、TeX、Markdown、纯文本；上传后自动解析参考文献并核验。</p>
          </div>
          <div className="mt-4 flex flex-wrap items-center justify-between gap-3 border-t border-hairline pt-4 dark:border-white/[0.08]">
            <span className="text-[11px] text-neutral-400 dark:text-white/65">核验会查询 OpenAlex、Crossref、DBLP、arXiv 等来源。</span>
            <button type="button" className="btn-primary" onClick={() => void handleCheckReferences()} disabled={referenceLoading || projectLoading}>
              {referenceLoading ? <Spinner className="size-3.5" /> : <CheckCircle2 size={14} />}
              {referenceLoading ? '正在核验…' : '开始核验'}
            </button>
          </div>
          {referenceError && <div className="mt-4"><ErrorRetry message={`参考文献核验失败：${referenceError}`} onRetry={() => void handleCheckReferences()} /></div>}

          {referenceLoading && (
            <div className="mt-6 flex items-center justify-center gap-2 rounded-lg border border-dashed border-hairline py-10 text-xs text-neutral-400 dark:border-white/10 dark:text-white/65" aria-live="polite">
              <Spinner className="size-4" /> 正在查询外部书目记录…
            </div>
          )}

          {referenceBatch && !referenceLoading && (
            <div className="mt-6">
              <div className="mb-4 flex flex-wrap gap-2">
                <span className="badge-gray">共 {referenceBatch.summary.total ?? referenceBatch.results.length} 条</span>
                <span className="badge-green">Verified {referenceBatch.summary.verified ?? 0}</span>
                <span className="badge-orange">Unverified {referenceBatch.summary.unverified ?? 0}</span>
              </div>
              {referenceBatch.results.length === 0 ? (
                <Empty text="没有解析到参考文献条目，请检查输入格式" />
              ) : (
                <div className="space-y-3">
                  {referenceBatch.results.map((result, index) => {
                    const matched = result.matched;
                    const title = result.entry.title || matched?.title || '未命名参考文献';
                    const details = referenceDetails(result);
                    return (
                      <article key={`${result.entry.bibtex_key ?? result.entry.raw}-${index}`} className="card card-hover p-5">
                        <div className="flex flex-wrap items-start justify-between gap-3">
                          <div className="min-w-0">
                            <div className="flex flex-wrap items-center gap-2">
                              <span className={result.status === 'verified' ? 'badge-green' : 'badge-orange'}>
                                {result.status === 'verified' ? 'Verified · 已核验' : 'Unverified · 未核验'}
                              </span>
                              <span className="locator">置信度 {confidenceLabel(result.confidence)}</span>
                            </div>
                            <h3 className="mt-3 text-[14px] font-semibold leading-snug text-ink dark:text-white">{title}</h3>
                            {details && <p className="mt-1.5 text-[11.5px] leading-relaxed text-neutral-500 dark:text-white/70">{details}</p>}
                          </div>
                          <span className="locator shrink-0">#{String(index + 1).padStart(2, '0')}</span>
                        </div>

                        {matched ? (
                          <div className="mt-4 flex flex-wrap items-center gap-x-4 gap-y-2 border-t border-hairline pt-3 text-[11px] dark:border-white/[0.08]">
                            <span className="badge-gray">{matched.source_kind}</span>
                            {matched.matched_fields.length > 0 && <span className="text-neutral-500 dark:text-white/70">匹配字段：{matched.matched_fields.join('、')}</span>}
                            {matched.url ? (
                              <a href={matched.url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-teal hover:underline">
                                打开外部记录 <ExternalLink size={11} />
                              </a>
                            ) : <span className="text-neutral-400 dark:text-white/60">外部记录未提供链接</span>}
                            <button type="button" className="btn-secondary !px-2.5 !py-1 text-[11px]" onClick={() => chooseReference({ ...result, matched })}>
                              用此文献 <ArrowRight size={12} />
                            </button>
                          </div>
                        ) : (
                          <div className="mt-4 border-t border-hairline pt-3 text-[11px] text-neutral-400 dark:border-white/[0.08] dark:text-white/65">未找到可用于第二层核验的外部记录。</div>
                        )}

                        <div className="mt-4 flex flex-col gap-2 border-t border-hairline pt-3 dark:border-white/[0.08]">
                          <span className="text-[11px] font-medium text-neutral-500 dark:text-white/70">核验说明</span>
                          {result.reasons.length > 0 ? (
                            <ul className="space-y-1 text-[11.5px] leading-relaxed text-neutral-600 dark:text-white/80">
                              {result.reasons.map((reason) => <li key={reason}>· {reasonLabel(reason, referenceReasonLabels)}</li>)}
                            </ul>
                          ) : <p className="text-[11.5px] text-neutral-400 dark:text-white/65">未命中原因待人工判断</p>}
                        </div>
                      </article>
                    );
                  })}
                </div>
              )}
            </div>
          )}
        </div>
      </Reveal>

      <Reveal delay={100} className="mt-10">
        <div id="citation-support-form">
          <SectionHead
            kicker="Layer 2 · Citation support"
            title="第二层：引用句支持度"
            right={<span className="locator">仅对第一层已命中的文献开放</span>}
          />
          <div className="card p-6">
          {matchedResults.length === 0 ? (
            <div className="rounded-lg border border-dashed border-hairline py-12 text-center dark:border-white/10">
              <Link2 className="mx-auto size-5 text-neutral-300 dark:text-white/50" />
              <p className="mt-3 text-sm text-neutral-500 dark:text-white/75">先完成第一层核验，并选择一条已命中的文献。</p>
            </div>
          ) : (
            <>
              <div>
                <label htmlFor="selected-reference" className="mb-2 block text-xs font-medium text-ink dark:text-white">选择已核验文献</label>
                <select
                  id="selected-reference"
                  value={selectedReferenceIndex ?? ''}
                  onChange={(event) => {
                    setSelectedReferenceIndex(event.target.value === '' ? null : Number(event.target.value));
                    setSupportResult(null);
                    setSupportError(null);
                  }}
                  className="input"
                >
                  {matchedResults.map((result, index) => <option key={`${result.entry.raw}-${index}`} value={index}>{result.entry.title || result.matched.title || '未命名参考文献'}</option>)}
                </select>
              </div>

              {selectedReference && (
                <div className="mt-3 rounded-lg border border-teal/20 bg-teal-soft/35 p-3 dark:border-[#4E8AFB]/25 dark:bg-[#4E8AFB]/10">
                  <div className="flex flex-wrap items-center gap-2 text-[11px] text-neutral-500 dark:text-white/75">
                    <span className="badge-teal">{selectedReference.matched.source_kind}</span>
                    <span className="font-mono break-all">{selectedReference.matched.external_id}</span>
                  </div>
                  <p className="mt-2 text-[13px] font-medium leading-snug text-ink dark:text-white">{selectedReference.matched.title}</p>
                </div>
              )}

              <div className="mt-5">
                <label htmlFor="citation-sentence" className="mb-2 block text-xs font-medium text-ink dark:text-white">需要核验的引用句</label>
                <input
                  id="citation-sentence"
                  value={citationSentence}
                  onChange={(event) => setCitationSentence(event.target.value)}
                  placeholder="例如：Smith 等人发现该方法显著提升了结果。"
                  className="input"
                />
              </div>

              <div className="mt-5 grid gap-4 md:grid-cols-2">
                <div>
                  <label htmlFor="citation-full-text" className="mb-2 block text-xs font-medium text-ink dark:text-white">被引论文全文（可选）</label>
                  <Textarea
                    id="citation-full-text"
                    value={fullText}
                    onChange={(event) => setFullText(event.target.value)}
                    placeholder="留空自动尝试 arXiv / Unpaywall"
                    className="min-h-28 resize-y text-[12px] leading-relaxed"
                  />
                </div>
                <div>
                  <label htmlFor="citation-abstract" className="mb-2 block text-xs font-medium text-ink dark:text-white">论文摘要（可选）</label>
                  <Textarea
                    id="citation-abstract"
                    value={abstract}
                    onChange={(event) => setAbstract(event.target.value)}
                    placeholder="当全文不可用时，可粘贴摘要作为证据"
                    className="min-h-28 resize-y text-[12px] leading-relaxed"
                  />
                </div>
              </div>

              <div className="mt-4 flex flex-wrap items-center justify-between gap-3 border-t border-hairline pt-4 dark:border-white/[0.08]">
                <span className="text-[11px] text-neutral-400 dark:text-white/65">支持度判断会返回规范化主张、证据片段与定位。</span>
                <button type="button" className="btn-primary" onClick={() => void handleCheckSupport()} disabled={supportLoading || projectLoading}>
                  {supportLoading ? <Spinner className="size-3.5" /> : <CheckCircle2 size={14} />}
                  {supportLoading ? '正在核验…' : '核验支持度'}
                </button>
              </div>
              {supportError && <div className="mt-4"><ErrorRetry message={`引用支持度核验失败：${supportError}`} onRetry={() => void handleCheckSupport()} /></div>}
            </>
          )}
          </div>

          {supportResult && (
            <article className="card mt-4 p-5">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <div className="kicker mb-2">Support judgment</div>
                <span className={supportCategoryMeta[supportResult.category].className}>{supportCategoryMeta[supportResult.category].label}</span>
              </div>
              <div className="flex items-center gap-2">
                <span className="badge-gray">证据：{fullTextStatusLabels[supportResult.full_text_status]}</span>
                <span className="locator">置信度 {confidenceLabel(supportResult.confidence)}</span>
              </div>
            </div>

            <div className="mt-5 grid gap-4 md:grid-cols-2">
              <div className="rounded-lg bg-neutral-50 p-4 dark:bg-white/[0.04]">
                <div className="kicker mb-2">Normalized claim · 规范化主张</div>
                <p className="text-[13px] leading-relaxed text-ink dark:text-white">{supportResult.normalized_claim || '—'}</p>
              </div>
              <div className="rounded-lg bg-neutral-50 p-4 dark:bg-white/[0.04]">
                <div className="kicker mb-2">Citation sentence · 原引用句</div>
                <p className="text-[13px] leading-relaxed text-neutral-600 dark:text-white/80">{supportResult.citation_sentence || '—'}</p>
              </div>
            </div>

            <div className="mt-5">
              <div className="kicker mb-2">Evidence · 证据片段</div>
              {supportResult.evidence_quote ? (
                <blockquote className="quote">{supportResult.evidence_quote}</blockquote>
              ) : <p className="text-[12px] text-neutral-400 dark:text-white/65">未返回证据片段。</p>}
              {supportResult.locator && <p className="locator mt-2 pl-4">{supportResult.locator}</p>}
            </div>

            <div className="mt-5 grid gap-5 border-t border-hairline pt-4 md:grid-cols-2 dark:border-white/[0.08]">
              <div>
                <div className="kicker mb-2">Reasons · 判断依据</div>
                {supportResult.reasons.length > 0 ? (
                  <ul className="space-y-1 text-[11.5px] leading-relaxed text-neutral-600 dark:text-white/80">
                    {supportResult.reasons.map((reason) => <li key={reason}>· {reasonLabel(reason, supportReasonLabels)}</li>)}
                  </ul>
                ) : <p className="text-[11.5px] text-neutral-400 dark:text-white/65">暂无额外判断依据。</p>}
              </div>
              <div>
                <div className="kicker mb-2">Recommended actions · 建议动作</div>
                {supportResult.recommended_actions.length > 0 ? (
                  <ul className="space-y-1 text-[11.5px] leading-relaxed text-neutral-600 dark:text-white/80">
                    {supportResult.recommended_actions.map((action) => <li key={action}>· {withChinesePunctuation(recommendedActionLabels[action] ?? action.replaceAll('_', ' '))}</li>)}
                  </ul>
                ) : <p className="text-[11.5px] text-neutral-400 dark:text-white/65">暂无建议动作。</p>}
              </div>
            </div>
            </article>
          )}
        </div>
      </Reveal>
    </div>
  );
}
