type JsonObject = Record<string, unknown>;

interface SkillEdit {
  section?: unknown;
  edit_class?: unknown;
  before_text?: unknown;
  after_text?: unknown;
  rationale?: unknown;
  reason?: unknown;
}

interface SkillResultProps {
  output: unknown;
  excludeKeys?: string[];
  className?: string;
  renderRawFallback?: boolean;
}

const sectionLabels: Record<string, string> = {
  experiments: '实验方案',
  issues: '问题',
  review_points: '评审要点',
  conclusions: '结论',
  warnings: '警告',
  sources: '来源',
  risks: '风险',
  dimensions: '评审维度',
  action_priorities: '行动优先级',
  citation_issues: '引用问题',
  number_issues: '数字一致性问题',
  critical_flags: '关键标记',
  claim_support: 'Claim 支持度',
  rebuttal_points: '回应要点',
  revision_plan: '修改计划',
  result_table_specs: '结果表格',
  evidence_requirements: '证据要求',
  not_adopted: '未采纳建议',
};

const labelFor = (key: string) => sectionLabels[key] ?? key.replace(/_/g, ' ');

function isObject(value: unknown): value is JsonObject {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value);
}

function displayValue(value: unknown): string {
  if (typeof value === 'string') return value;
  if (value === null || value === undefined) return '—';
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  return JSON.stringify(value);
}

function EditCards({ edits }: { edits: SkillEdit[] }) {
  return (
    <div className="space-y-3">
      {edits.map((edit, index) => (
        <article key={index} className="overflow-hidden rounded-lg border border-neutral-100 dark:border-white/10">
          <div className="flex flex-wrap items-center gap-2 bg-neutral-50 px-3.5 py-2 text-xs dark:bg-white/[0.04]">
            <span className="badge-teal">{displayValue(edit.section)}</span>
            {edit.edit_class != null && <span className="text-neutral-400 dark:text-white/75">{displayValue(edit.edit_class)}</span>}
            {(edit.rationale ?? edit.reason) != null && (
              <span className="ml-auto text-neutral-500 dark:text-white/80">{displayValue(edit.rationale ?? edit.reason)}</span>
            )}
          </div>
          <div className="diff-grid text-[13px]">
            <div className="diff-cell diff-before">
              <div className="mb-1 text-[10px] text-red-500 dark:text-red-400">— 删除</div>
              <p className="diff-removed whitespace-pre-wrap">{displayValue(edit.before_text)}</p>
            </div>
            <div className="diff-cell diff-after">
              <div className="mb-1 text-[10px] text-green-600 dark:text-green-400">+ 新增</div>
              <p className="diff-added whitespace-pre-wrap">{displayValue(edit.after_text)}</p>
            </div>
          </div>
        </article>
      ))}
    </div>
  );
}

function GenericItem({ item }: { item: unknown }) {
  if (!isObject(item)) {
    return <li className="text-[12.5px] leading-relaxed text-neutral-600 dark:text-white/85">{displayValue(item)}</li>;
  }
  return (
    <li className="rounded-md bg-neutral-50 px-3 py-2 dark:bg-white/[0.035]">
      <div className="grid gap-x-4 gap-y-1 sm:grid-cols-2">
        {Object.entries(item).map(([key, value]) => (
          <div key={key} className="min-w-0 text-[12px] leading-relaxed">
            <span className="text-neutral-400 dark:text-white/65">{labelFor(key)}：</span>
            <span className="text-neutral-700 dark:text-white/90">{displayValue(value)}</span>
          </div>
        ))}
      </div>
    </li>
  );
}

export function SkillResultView({ output, excludeKeys = [], className = '', renderRawFallback = true }: SkillResultProps) {
  const value = isObject(output) ? output : null;
  if (!value) {
    if (!renderRawFallback) return null;
    return (
      <details className={className}>
        <summary className="cursor-pointer text-[11px] text-neutral-400 dark:text-white/65">查看原始结果</summary>
        <pre className="mt-2 max-h-48 overflow-auto whitespace-pre-wrap break-words rounded-md bg-neutral-50 p-3 font-mono text-[10px] text-neutral-500 dark:bg-white/[0.035] dark:text-white/75">
          {JSON.stringify(output, null, 2)}
        </pre>
      </details>
    );
  }

  const excluded = new Set(excludeKeys);
  const edits = Array.isArray(value.edits)
    ? value.edits.filter(isObject) as SkillEdit[]
    : [];
  const visibleEntries = Object.entries(value).filter(([key]) => !excluded.has(key) && key !== 'edits');
  const arrayEntries = visibleEntries.filter(([, item]) => Array.isArray(item) && item.length > 0);
  const scalarEntries = visibleEntries.filter(([, item]) => !Array.isArray(item) && (typeof item !== 'object' || item === null));
  const hasStructuredContent = edits.length > 0 || arrayEntries.length > 0 || scalarEntries.length > 0;

  if (!hasStructuredContent) {
    if (!renderRawFallback) return null;
    return (
      <details className={className}>
        <summary className="cursor-pointer text-[11px] text-neutral-400 dark:text-white/65">查看原始结果</summary>
        <pre className="mt-2 max-h-48 overflow-auto whitespace-pre-wrap break-words rounded-md bg-neutral-50 p-3 font-mono text-[10px] text-neutral-500 dark:bg-white/[0.035] dark:text-white/75">
          {JSON.stringify(output, null, 2)}
        </pre>
      </details>
    );
  }

  return (
    <div className={`space-y-4 ${className}`}>
      {edits.length > 0 && (
        <section>
          <div className="mb-2 text-xs uppercase tracking-wide text-neutral-500 dark:text-white/75">修改建议 · {edits.length}</div>
          <EditCards edits={edits} />
        </section>
      )}
      {arrayEntries.map(([key, items]) => (
        <section key={key}>
          <div className="mb-2 text-xs uppercase tracking-wide text-neutral-500 dark:text-white/75">{labelFor(key)}</div>
          <ul className="space-y-1.5">
            {(items as unknown[]).map((item, index) => <GenericItem key={index} item={item} />)}
          </ul>
        </section>
      ))}
      {scalarEntries.length > 0 && (
        <dl className="grid gap-2 sm:grid-cols-2">
          {scalarEntries.map(([key, item]) => (
            <div key={key} className="rounded-md bg-neutral-50 px-3 py-2 dark:bg-white/[0.035]">
              <dt className="text-[10px] uppercase tracking-wide text-neutral-400 dark:text-white/65">{labelFor(key)}</dt>
              <dd className="mt-1 text-[12.5px] leading-relaxed text-neutral-700 dark:text-white/90">{displayValue(item)}</dd>
            </div>
          ))}
        </dl>
      )}
      <details>
        <summary className="cursor-pointer text-[11px] text-neutral-400 dark:text-white/65">查看原始结果</summary>
        <pre className="mt-2 max-h-48 overflow-auto whitespace-pre-wrap break-words rounded-md bg-neutral-50 p-3 font-mono text-[10px] text-neutral-500 dark:bg-white/[0.035] dark:text-white/75">
          {JSON.stringify(output, null, 2)}
        </pre>
      </details>
    </div>
  );
}
