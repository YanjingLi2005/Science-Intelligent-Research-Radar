/** P0 evidence-fidelity panel: surfaces the latest scan's extraction-accuracy
 * measurement (evidence pass rate, LLM faithfulness, NLI second opinion) on
 * the dashboard. Data comes from Project.fidelity (backend aggregates the
 * latest completed scan's fidelity block). */

import type { FidelityStats } from '../api';
import { SectionHead, Stat } from './chrome';

function rate(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—';
  return `${Math.round(value * 100)}%`;
}

export default function FidelityPanel({ fidelity }: { fidelity?: FidelityStats | null }) {
  if (!fidelity || fidelity.pairs_assessed === 0) {
    return (
      <section className="card p-7">
        <SectionHead kicker="Fidelity" title="扫描证据保真度" />
        <p className="text-sm text-neutral-500 mt-2">尚无扫描数据 —— 完成一次文献扫描后，这里会展示证据提取的准确性与判定保真度。</p>
      </section>
    );
  }

  const unfaithfulTotal = fidelity.unfaithful + fidelity.nli_disagreement;
  const verdictBreakdown = [
    { name: '保真', value: fidelity.faithful, color: '#067647' },
    { name: '不保真', value: fidelity.unfaithful, color: '#B42318' },
    { name: 'NLI 判异', value: fidelity.nli_disagreement, color: '#B54708' },
    { name: '未判定', value: fidelity.judge_failed, color: '#98A2B3' },
  ].filter((item) => item.value > 0);

  return (
    <section className="card p-7">
      <SectionHead
        kicker="Fidelity"
        title="扫描证据保真度"
        right={
          <span className="locator">
            评估 {fidelity.pairs_assessed} 对 · 验证 {fidelity.verified} 条
          </span>
        }
      />

      <div className="grid grid-cols-2 md:grid-cols-4 gap-6 mt-5">
        <Stat
          value={rate(fidelity.evidence_pass_rate)}
          label="证据通过率"
          tone={fidelity.evidence_pass_rate !== null && fidelity.evidence_pass_rate >= 0.7 ? 'teal' : 'orange'}
        />
        <Stat
          value={rate(fidelity.faithfulness_rate)}
          label="判定保真率"
          tone={fidelity.faithfulness_rate !== null && fidelity.faithfulness_rate >= 0.7 ? 'teal' : 'orange'}
        />
        <Stat value={fidelity.span_failed} label="引用锚定失败" tone={fidelity.span_failed > 0 ? 'red' : 'ink'} />
        <Stat value={unfaithfulTotal} label="不保真 / 判异" tone={unfaithfulTotal > 0 ? 'red' : 'ink'} />
      </div>

      {/* verdict breakdown bar */}
      {verdictBreakdown.length > 0 && fidelity.judged > 0 && (
        <div className="mt-6">
          <div className="flex items-center gap-2 text-xs text-neutral-500 mb-2">
            <span>LLM 保真判定（{fidelity.judged} 条抽样）</span>
          </div>
          <div className="flex h-2.5 rounded-full overflow-hidden bg-neutral-100 dark:bg-white/10">
            {verdictBreakdown.map((item) => (
              <div
                key={item.name}
                className="h-full"
                style={{
                  width: `${(item.value / fidelity.judged) * 100}%`,
                  background: item.color,
                }}
                title={`${item.name}: ${item.value}`}
              />
            ))}
          </div>
          <div className="flex flex-wrap gap-4 mt-2">
            {verdictBreakdown.map((item) => (
              <span key={item.name} className="text-[11px] text-neutral-500 flex items-center gap-1.5">
                <span className="w-2 h-2 rounded-full" style={{ background: item.color }} />
                {item.name} {item.value}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* NLI + degradation details */}
      <div className="mt-6 pt-5 hairline-t grid md:grid-cols-3 gap-4 text-xs text-neutral-500">
        <div>
          <div className="font-mono uppercase tracking-wider text-[10px] text-neutral-400 mb-1.5">NLI 第二意见</div>
          <div>检查 {fidelity.nli_checked} 条 · 判异 {fidelity.nli_disagreement} · 中性证据 {fidelity.nli_neutral}</div>
          <div className="mt-0.5">模型不可用 {fidelity.nli_unavailable}</div>
        </div>
        <div>
          <div className="font-mono uppercase tracking-wider text-[10px] text-neutral-400 mb-1.5">信任门</div>
          <div>条件阻断 {fidelity.trust_blocked} · 无实质变化 {fidelity.filtered_no_change}</div>
        </div>
        <div>
          <div className="font-mono uppercase tracking-wider text-[10px] text-neutral-400 mb-1.5">判定器</div>
          <div>跳过（无 LLM）{fidelity.judge_skipped_no_llm} · 失败 {fidelity.judge_failed}</div>
        </div>
      </div>
    </section>
  );
}
