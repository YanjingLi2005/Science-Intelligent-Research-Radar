import { useEffect, useMemo, useRef, useState } from 'react';
import { ArrowUpRight, BookOpen, RotateCcw, Send, Square } from 'lucide-react';
import { useNavigate } from 'react-router';
import {
  askLiteratureStream,
  askLiterature,
  type LiteratureQAResult,
  type LiteratureQASource,
} from '../api';
import { useProject } from '../contexts/ProjectContext';
import { Empty, Reveal, SectionHead } from '../components/chrome';
import { Spinner } from '../components/ui/spinner';
import ErrorRetry from '../components/ErrorRetry';
import { StreamRenderer } from '../components/StreamRenderer';

type UserMessage = { id: string; role: 'user'; text: string };
type AssistantMessage = { id: string; role: 'assistant'; question: string; result: LiteratureQAResult };
type Message = UserMessage | AssistantMessage;

const historyKey = (projectId: string) => `rr-qa-history-${projectId}`;

function readHistory(projectId: string): Message[] {
  if (!projectId) return [];
  try {
    const value: unknown = JSON.parse(sessionStorage.getItem(historyKey(projectId)) ?? '[]');
    if (!Array.isArray(value)) return [];
    return value.filter((message): message is Message =>
      Boolean(message && typeof message === 'object' && 'id' in message && 'role' in message),
    );
  } catch {
    return [];
  }
}

function formatAuthors(authors: LiteratureQASource['authors']): string | null {
  if (Array.isArray(authors)) return authors.join(', ');
  return authors || null;
}

function formatList(value: string[] | string | null | undefined): string[] {
  return Array.isArray(value) ? value : value ? [value] : [];
}

function SourceCard({ source }: { source: LiteratureQASource }) {
  const authors = formatAuthors(source.authors);
  const findings = formatList(source.key_findings);
  return (
    <article className="rounded-lg border border-hairline p-4 dark:border-white/[0.08]">
      <div className="flex items-start gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h4 className="font-medium leading-snug text-neutral-800 dark:text-white/95">{source.title}</h4>
            {source.year && <span className="locator">{source.year}</span>}
          </div>
          {(authors || source.venue) && (
            <p className="mt-1 text-xs text-neutral-500 dark:text-white/75">
              {[authors, source.venue].filter(Boolean).join(' · ')}
            </p>
          )}
        </div>
        {source.relevance !== null && source.relevance !== undefined && (
          <span className="badge-gray shrink-0">相关度 {source.relevance}</span>
        )}
      </div>
      {(source.doi || source.url) && (
        <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-xs">
          {source.doi && <span className="locator break-all">DOI: {source.doi}</span>}
          {source.url && (
            <a href={source.url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-teal hover:underline">
              打开来源 <ArrowUpRight size={12} />
            </a>
          )}
        </div>
      )}
      {(findings.length > 0 || source.evidence_snippets.length > 0) && (
        <div className="mt-3 space-y-1.5 border-t border-hairline pt-3 dark:border-white/[0.07]">
          {findings.map((finding) => <p key={finding} className="text-xs leading-relaxed text-neutral-600 dark:text-white/85">要点：{finding}</p>)}
          {source.evidence_snippets.map((snippet) => <p key={snippet} className="text-xs leading-relaxed text-neutral-500 dark:text-white/75">“{snippet}”</p>)}
        </div>
      )}
    </article>
  );
}

export default function LiteratureQA() {
  const navigate = useNavigate();
  const { project, projectId, loading: projectLoading } = useProject();
  const [messages, setMessages] = useState<Message[]>([]);
  const [question, setQuestion] = useState('');
  const [loading, setLoading] = useState(false);
  const [isStreaming, setIsStreaming] = useState(false);
  const [streamingText, setStreamingText] = useState('');
  const [streamingSources, setStreamingSources] = useState<LiteratureQASource[]>([]);
  const [error, setError] = useState('');
  const [failedQuestion, setFailedQuestion] = useState('');
  const abortControllerRef = useRef<AbortController | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => setMessages(readHistory(projectId)), [projectId]);

  useEffect(() => {
    if (!projectId) return;
    try {
      // Keep up to 30 most recent turns to prevent browser storage bloat
      const capped = messages.slice(-30);
      sessionStorage.setItem(historyKey(projectId), JSON.stringify(capped));
    } catch {
      // Session storage may be unavailable in private browsing; chat still works.
    }
  }, [messages, projectId]);

  // Clean up any running stream on unmount
  useEffect(() => {
    return () => {
      if (abortControllerRef.current) {
        abortControllerRef.current.abort();
      }
    };
  }, []);

  const scrollToBottom = () => {
    requestAnimationFrame(() => {
      bottomRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' });
    });
  };

  const suggestions = useMemo(() => [
    '哪些方法在不同数据集上表现最稳定？',
    '现有研究的主要局限和未来方向是什么？',
  ], []);

  const handleStop = () => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
      abortControllerRef.current = null;
    }
  };

  const handleResetSession = () => {
    setMessages([]);
    if (projectId) {
      try {
        sessionStorage.removeItem(historyKey(projectId));
      } catch {
        // Ignore storage removal errors
      }
    }
  };

  const ask = async (rawQuestion = question) => {
    const text = rawQuestion.trim();
    if (!projectId || !text || loading || isStreaming) return;

    // Prepare multi-turn history payload for context management
    const historyPayload = messages.slice(-10).map((msg) => ({
      role: msg.role,
      content: msg.role === 'user' ? msg.text : msg.result.answer,
    }));

    setQuestion('');
    setError('');
    setFailedQuestion('');
    setMessages((current) => [...current, { id: `${Date.now()}-user`, role: 'user', text }]);
    setLoading(true);
    setIsStreaming(true);
    setStreamingText('');
    setStreamingSources([]);
    scrollToBottom();

    const controller = new AbortController();
    abortControllerRef.current = controller;

    try {
      const result = await askLiteratureStream(
        projectId,
        text,
        5,
        {
          onSources: (sources) => {
            setStreamingSources(sources);
            scrollToBottom();
          },
          onToken: (_delta, accumulated) => {
            setStreamingText(accumulated);
            scrollToBottom();
          },
          onDone: (finalResult) => {
            setMessages((current) => [
              ...current,
              { id: `${Date.now()}-assistant`, role: 'assistant', question: text, result: finalResult },
            ]);
            setIsStreaming(false);
            setStreamingText('');
            setStreamingSources([]);
            scrollToBottom();
          },
        },
        controller.signal,
        historyPayload,
      );
      if (controller.signal.aborted) {
        setMessages((current) => [
          ...current,
          { id: `${Date.now()}-assistant`, role: 'assistant', question: text, result },
        ]);
        setIsStreaming(false);
        setStreamingText('');
        setStreamingSources([]);
      }
    } catch (e: unknown) {
      if (controller.signal.aborted) {
        setIsStreaming(false);
        return;
      }
      // If stream fails before emitting anything, fallback to standard batch endpoint
      if (!streamingText) {
        try {
          const fallbackResult = await askLiterature(projectId, text, 5, historyPayload);
          setMessages((current) => [
            ...current,
            { id: `${Date.now()}-assistant`, role: 'assistant', question: text, result: fallbackResult },
          ]);
          setIsStreaming(false);
          setStreamingText('');
          setStreamingSources([]);
          scrollToBottom();
          return;
        } catch {
          // Ignore fallback error and surface original error
        }
      }
      setError(e instanceof Error ? e.message : String(e));
      setFailedQuestion(text);
      setIsStreaming(false);
    } finally {
      setLoading(false);
      abortControllerRef.current = null;
    }
  };

  if (projectLoading && !project) return <div className="py-12 text-center"><Spinner className="mx-auto" /></div>;

  return (
    <div className="space-y-6">
      <Reveal>
        <SectionHead
          kicker="Literature intelligence"
          title="文献问答"
          right={
            <div className="flex items-center gap-2">
              {messages.length > 0 && (
                <button
                  type="button"
                  className="btn-secondary !text-xs"
                  onClick={handleResetSession}
                >
                  <RotateCcw size={12} /> 开启新会话
                </button>
              )}
              <button className="btn-secondary" onClick={() => navigate(`/cases/${projectId}/radar`)}>
                返回文献雷达
              </button>
            </div>
          }
        />
        <p className="-mt-3 text-sm text-neutral-500 dark:text-white/80">围绕当前监控对象已收集的论文提问，支持连续多轮追问，答案实时流式生成并附上最多 5 篇来源与证据。</p>
      </Reveal>

      {messages.length === 0 && !loading && !isStreaming ? (
        <Reveal delay={60}>
          <div className="card p-7">
            <Empty text="还没有问题。试着问问论文中的方法、证据、局限或研究空白。" />
            <div className="mt-5 flex flex-wrap gap-2">
              {suggestions.map((suggestion) => (
                <button
                  key={suggestion}
                  className="btn-secondary text-left"
                  onClick={() => {
                    setQuestion(suggestion);
                    void ask(suggestion);
                  }}
                >
                  {suggestion}
                </button>
              ))}
            </div>
          </div>
        </Reveal>
      ) : (
        <div className="space-y-4">
          {messages.map((message) => message.role === 'user' ? (
            <Reveal key={message.id}>
              <div className="ml-auto max-w-[82%] rounded-xl bg-[#18181B] px-4 py-3 text-sm text-white dark:bg-white dark:text-[#111113]">{message.text}</div>
            </Reveal>
          ) : (
            <Reveal key={message.id}>
              <div className="card p-5">
                <div className="flex items-center gap-2"><BookOpen size={14} className="text-teal" /><div className="kicker">Literature answer</div></div>
                <p className="mt-3 whitespace-pre-wrap text-sm leading-7 text-neutral-700 dark:text-white/95">{message.result.answer}</p>
                {message.result.uncertainty && <p className="mt-3 rounded-lg bg-neutral-50 px-3 py-2 text-xs text-neutral-600 dark:bg-white/[0.04] dark:text-white/80">不确定性：{message.result.uncertainty}</p>}
                {message.result.sources.length > 0 && <div className="mt-5 space-y-2"><div className="kicker">来源（{Math.min(message.result.sources.length, 5)}）</div>{message.result.sources.slice(0, 5).map((source) => <SourceCard key={source.source_id} source={source} />)}</div>}
                {message.result.warnings.length > 0 && <div className="mt-4 rounded-lg border border-amber-200 bg-amber-50 px-3.5 py-2.5 text-xs text-amber-800 dark:border-amber-500/25 dark:bg-amber-500/10 dark:text-amber-200">{message.result.warnings.join('；')}</div>}
              </div>
            </Reveal>
          ))}

          {/* Active Streaming Bubble */}
          {isStreaming && (
            <div className="card border-teal/30 p-5 shadow-sm">
              <div className="flex items-center justify-between gap-2">
                <div className="flex items-center gap-2">
                  <BookOpen size={14} className="text-teal animate-pulse" />
                  <div className="kicker text-teal">Literature answer · 正在生成</div>
                </div>
                <button
                  type="button"
                  onClick={handleStop}
                  className="inline-flex items-center gap-1.5 rounded border border-hairline px-2.5 py-1 text-[11px] font-medium text-neutral-600 hover:bg-neutral-100 dark:border-white/10 dark:text-white/80 dark:hover:bg-white/[0.06]"
                >
                  <Square size={11} className="fill-current text-red-500" />
                  <span>停止生成</span>
                </button>
              </div>

              <div className="mt-3">
                <StreamRenderer
                  text={streamingText}
                  isStreaming={true}
                  placeholder="正在检索已收集文献并组织答案…"
                />
              </div>

              {streamingSources.length > 0 && (
                <div className="mt-5 space-y-2 border-t border-hairline pt-4 dark:border-white/[0.08]">
                  <div className="kicker">相关来源（{Math.min(streamingSources.length, 5)}）</div>
                  {streamingSources.slice(0, 5).map((source) => (
                    <SourceCard key={source.source_id} source={source} />
                  ))}
                </div>
              )}
            </div>
          )}

          <div ref={bottomRef} />
        </div>
      )}

      {error && (
        <ErrorRetry message={error} onRetry={() => void ask(failedQuestion)} />
      )}

      <Reveal delay={80}>
        <form className="card sticky bottom-4 flex items-end gap-2 p-2.5 shadow-sm" onSubmit={(e) => { e.preventDefault(); void ask(); }}>
          <textarea
            className="min-h-[42px] flex-1 resize-none border-0 bg-transparent px-2 py-2 text-sm outline-none placeholder:text-neutral-400 dark:placeholder:text-white/55"
            placeholder="向当前文献库提问…（Enter 发送，Shift+Enter 换行）"
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); void ask(); } }}
            disabled={loading || isStreaming || !project}
            rows={1}
          />
          {isStreaming ? (
            <button
              type="button"
              className="btn-secondary !text-red-600 dark:!text-red-400"
              onClick={handleStop}
            >
              <Square size={13} className="fill-current text-red-500" /> 停止
            </button>
          ) : (
            <button
              type="submit"
              className="btn-primary"
              disabled={loading || isStreaming || !question.trim() || !project}
            >
              <Send size={14} /> 提问
            </button>
          )}
        </form>
      </Reveal>
    </div>
  );
}

