// Internal product-monitoring dashboard: aggregate usage, spend, failures and
// system health across every tenant. Read-only; intended for the team, not
// end users. Layout is intentionally plain — this is a working prototype to
// validate which metrics matter before investing in visual design.

import { useState } from 'react';
import { getMetricsOverview, useApi, type MetricsOverview } from '../api';
import { Spinner } from '../components/ui/spinner';
import ErrorRetry from '../components/ErrorRetry';

const WINDOWS = [
  { days: 7, label: '7 天' },
  { days: 30, label: '30 天' },
  { days: 90, label: '90 天' },
  { days: 3650, label: '全部' },
];

function Stat({
  label,
  value,
  hint,
  tone = 'normal',
}: {
  label: string;
  value: string;
  hint?: string;
  tone?: 'normal' | 'warn';
}) {
  return (
    <div className="card p-5">
      <div className="text-xs uppercase tracking-wide text-neutral-400 dark:text-white/60">{label}</div>
      <div
        className={`mt-2 text-3xl font-light ${tone === 'warn' ? 'text-red-600 dark:text-red-400' : 'text-neutral-900 dark:text-white'}`}
      >
        {value}
      </div>
      {hint ? <div className="mt-1 text-xs text-neutral-500 dark:text-white/70">{hint}</div> : null}
    </div>
  );
}

function BarList({
  title,
  rows,
  empty,
}: {
  title: string;
  rows: { label: string; value: number; display: string }[];
  empty: string;
}) {
  const max = rows.reduce((peak, row) => Math.max(peak, row.value), 0);
  return (
    <div className="card p-5">
      <div className="text-xs uppercase tracking-wide text-neutral-400 dark:text-white/60">{title}</div>
      {rows.length === 0 ? (
        <div className="mt-3 text-sm text-neutral-400 dark:text-white/60">{empty}</div>
      ) : (
        <div className="mt-3 space-y-2">
          {rows.map((row) => (
            <div key={row.label}>
              <div className="flex items-baseline justify-between text-sm">
                <span className="truncate pr-3 text-neutral-700 dark:text-white/90">{row.label}</span>
                <span className="shrink-0 tabular-nums text-neutral-500 dark:text-white/70">{row.display}</span>
              </div>
              <div className="mt-1 h-1 w-full rounded bg-neutral-100 dark:bg-white/10">
                <div
                  className="h-1 rounded bg-neutral-400 dark:bg-white/30"
                  style={{ width: max > 0 ? `${(row.value / max) * 100}%` : '0%' }}
                />
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function percent(value: number | null): string {
  return value === null ? '—' : `${(value * 100).toFixed(1)}%`;
}

function seconds(value: number | null): string {
  if (value === null) return '—';
  return value >= 60 ? `${(value / 60).toFixed(1)} 分` : `${value.toFixed(0)} 秒`;
}

export default function Metrics() {
  const [windowDays, setWindowDays] = useState(30);
  const { data, loading, error, refetch } = useApi<MetricsOverview>(
    () => getMetricsOverview(windowDays),
    [windowDays],
  );

  if (loading && !data) {
    return (
      <div aria-busy="true" aria-live="polite" className="flex items-center justify-center py-20">
        <Spinner className="size-8 text-neutral-400 dark:text-white/60" />
      </div>
    );
  }
  if (error || !data) {
    return (
      <div className="card p-8 text-center text-sm text-red-600 dark:text-red-400">
        <ErrorRetry message={`指标加载失败：${error?.message ?? '未知错误'}`} onRetry={() => void refetch()} />
      </div>
    );
  }

  const { users, cost, problems, health } = data;
  // Surface reliability as a problem signal when it drops below a level worth
  // investigating, rather than leaving the reader to judge the raw number.
  const successTone = health.success_rate !== null && health.success_rate < 0.8 ? 'warn' : 'normal';

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-4 border-b border-black/10 pb-6 dark:border-white/10">
        <div>
          <div className="kicker">Monitoring</div>
          <h1 className="mt-2 text-[clamp(2rem,3vw,2.75rem)] font-semibold tracking-[-0.04em]">产品数据看板</h1>
          <p className="mt-1 text-sm text-neutral-500 dark:text-white/70">
            覆盖全部 {data.tenant_count} 个账号数据库 · 最近更新{' '}
            {new Date(data.generated_at).toLocaleString()}
          </p>
        </div>
        <div className="flex items-center gap-2">
          {WINDOWS.map((option) => (
            <button
              key={option.days}
              type="button"
              onClick={() => setWindowDays(option.days)}
              className={`rounded-xl border px-3 py-1.5 text-sm transition-colors ${
                windowDays === option.days
                  ? 'border-neutral-800 bg-neutral-900 text-white dark:border-white/15 dark:bg-white/10 dark:text-white'
                  : 'border-neutral-200 text-neutral-600 hover:border-neutral-400 dark:border-white/10 dark:text-white/65 dark:hover:border-white/30'
              }`}
            >
              {option.label}
            </button>
          ))}
          <button
            type="button"
            onClick={refetch}
            className="rounded-xl border border-neutral-200 px-3 py-1.5 text-sm text-neutral-600 transition-colors hover:border-neutral-400 dark:border-white/10 dark:text-white/65 dark:hover:border-white/30"
          >
            刷新
          </button>
        </div>
      </header>

      <section className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Stat
          label="活跃账号"
          value={String(users.active_in_window)}
          hint={`共 ${users.total} 个账号 · 新增 ${users.new_in_window}`}
        />
        <Stat
          label="Token 消耗"
          value={cost.total_tokens.toLocaleString()}
          hint={`${cost.llm_calls} 次调用 · $${cost.total_usd}`}
        />
        <Stat
          label="扫描成功率"
          value={percent(health.success_rate)}
          hint={`${health.total_scans} 次扫描 · 失败 ${problems.failed_scans}`}
          tone={successTone}
        />
        <Stat
          label="平均扫描耗时"
          value={seconds(health.avg_scan_seconds)}
          hint={
            health.avg_llm_latency_ms === null
              ? undefined
              : `模型平均延迟 ${(health.avg_llm_latency_ms / 1000).toFixed(1)} 秒`
          }
        />
      </section>

      <section className="grid gap-4 lg:grid-cols-2">
        <BarList
          title="用户遇到的问题"
          rows={problems.top_errors.map((row) => ({
            label: row.reason,
            value: row.count,
            display: `${row.count} 次`,
          }))}
          empty="窗口内没有记录到失败。"
        />
        <BarList
          title="成本分布（按阶段）"
          rows={cost.by_stage.map((row) => ({
            label: row.stage,
            value: row.usd,
            display: `$${row.usd}`,
          }))}
          empty="窗口内没有模型调用。"
        />
        <BarList
          title="扫描状态分布"
          rows={Object.entries(health.scans_by_status).map(([status, count]) => ({
            label: status,
            value: count,
            display: `${count} 次`,
          }))}
          empty="窗口内没有扫描。"
        />
        <BarList
          title="Token 消耗（按模型）"
          rows={cost.by_model.map((row) => ({
            label: row.model,
            value: row.tokens,
            display: row.tokens.toLocaleString(),
          }))}
          empty="窗口内没有模型调用。"
        />
      </section>

      <p className="text-xs text-neutral-400 dark:text-white/60">
        活跃账号按窗口内登录去重统计；产品内的真实日活需要新增请求级埋点，尚未接入。
      </p>
    </div>
  );
}
