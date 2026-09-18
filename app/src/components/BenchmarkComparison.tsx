import { useState } from "react";
import {
  ResponsiveContainer,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  Legend,
  CartesianGrid,
} from "recharts";
import {
  AlertTriangle,
  Check,
  Copy,
  BarChart2,
  Table as TableIcon,
  TrendingUp,
  TrendingDown,
  Sparkles,
} from "lucide-react";
import type { BenchmarkItem } from "../data/mock";

interface BenchmarkComparisonProps {
  data?: BenchmarkItem[];
  paperTitle?: string;
}

export function BenchmarkComparison({ data = [], paperTitle }: BenchmarkComparisonProps) {
  const [viewMode, setViewMode] = useState<"chart" | "table">("chart");
  const [copied, setCopied] = useState(false);

  // If no data, render graceful fallback notice
  if (!data || data.length === 0) {
    return (
      <div className="rounded-lg border border-hairline bg-neutral-50/50 p-6 text-center dark:border-white/[0.08] dark:bg-white/[0.02]">
        <BarChart2 className="mx-auto size-8 text-neutral-400 mb-2" />
        <p className="text-xs text-neutral-500 dark:text-white/60">
          该文献暂无提取到的量化实验对比表格（纯文本摘要模式已优雅降级）。
        </p>
      </div>
    );
  }

  // Format data for Recharts
  const chartData = data.map((item) => ({
    name: `${item.dataset} (${item.metric})`,
    "在研模型 (Ours)": item.our_value,
    "外部竞品 (Competitor)": item.competitor_value,
    unit: item.unit || "%",
    delta: item.delta,
    warning: item.warning,
    level: item.warning_level,
  }));

  const hasWarning = data.some((d) => d.warning);
  const hasDominance = data.some((d) => d.empirical_dominance || d.warning_level === "critical");
  const criticalCount = data.filter((d) => d.warning_level === "critical").length;

  const generateLatexTable = () => {
    const caption = paperTitle
      ? `Benchmark Comparison with ${paperTitle}`
      : "Benchmark Comparison with Concurrent Competitor";
    const rows = data
      .map((d) => {
        const sign = d.delta > 0 ? "+" : "";
        const deltaStr = `${sign}${d.delta}${d.unit || ""}`;
        const fmtDelta = d.warning ? `\\textbf{${deltaStr}}` : deltaStr;
        return `    ${d.dataset} & ${d.metric} & ${d.our_value}${d.unit || ""} & ${d.competitor_value}${d.unit || ""} & ${fmtDelta} \\\\`;
      })
      .join("\n");

    return `\\begin{table}[t]
  \\centering
  \\caption{${caption}}
  \\label{tab:benchmark_comparison}
  \\begin{tabular}{lcccc}
    \\toprule
    \\textbf{Dataset} & \\textbf{Metric} & \\textbf{Our Model} & \\textbf{Competitor} & \\textbf{$\\Delta$} \\\\
    \\midrule
${rows}
    \\bottomrule
  \\end{tabular}
\\end{table}`;
  };

  const handleCopyLatex = async () => {
    try {
      await navigator.clipboard.writeText(generateLatexTable());
      setCopied(true);
      setTimeout(() => setCopied(false), 2500);
    } catch {
      // ignore
    }
  };

  return (
    <div className="space-y-4">
      {/* Alert Header if competitor outperforms */}
      {hasWarning ? (
        <div className="rounded-lg border border-amber-200 bg-amber-50/70 p-3.5 text-xs text-amber-900 dark:border-amber-500/25 dark:bg-amber-500/10 dark:text-amber-200 flex items-start gap-2.5">
          <AlertTriangle className="size-4 shrink-0 text-amber-600 dark:text-amber-400 mt-0.5" />
          <div className="space-y-1">
            <div className="font-semibold flex items-center gap-1.5 flex-wrap">
              <span>量化基准风险警报</span>
              {hasDominance && (
                <span className="badge-red font-mono text-[10px] px-1.5 py-0.5 font-bold">
                  EMPIRICAL_DOMINANCE
                </span>
              )}
              {criticalCount > 0 && (
                <span className="badge-red text-[10px] px-1.5 py-0.2">
                  {criticalCount} 项指标被显著超越
                </span>
              )}
            </div>
            <p className="leading-relaxed text-neutral-700 dark:text-white/80 text-[11.5px]">
              外部论文在相同公共基准上取得了更高的数值指标。建议在后续论文改写中收窄在研
              Claim 的泛化边界，或在 Discussion 中加入对该实验设置的差异化声明。
            </p>
          </div>
        </div>
      ) : (
        <div className="rounded-lg border border-teal/20 bg-teal/5 p-3 text-xs text-teal flex items-center gap-2">
          <Sparkles className="size-4 shrink-0" />
          <span>在研模型在当前已抽取的公共 Benchmark 指标上均保持领先或持平优势。</span>
        </div>
      )}

      {/* Controls bar */}
      <div className="flex flex-wrap items-center justify-between gap-2 pt-1">
        <div className="flex items-center gap-1 bg-neutral-100 p-1 rounded-lg dark:bg-white/[0.06]">
          <button
            onClick={() => setViewMode("chart")}
            className={`flex items-center gap-1.5 px-2.5 py-1 text-xs rounded-md font-medium transition-colors ${
              viewMode === "chart"
                ? "bg-white text-neutral-900 shadow-xs dark:bg-white/10 dark:text-white"
                : "text-neutral-500 hover:text-neutral-900 dark:text-white/60 dark:hover:text-white"
            }`}
          >
            <BarChart2 className="size-3.5" />
            <span>柱状对比图</span>
          </button>
          <button
            onClick={() => setViewMode("table")}
            className={`flex items-center gap-1.5 px-2.5 py-1 text-xs rounded-md font-medium transition-colors ${
              viewMode === "table"
                ? "bg-white text-neutral-900 shadow-xs dark:bg-white/10 dark:text-white"
                : "text-neutral-500 hover:text-neutral-900 dark:text-white/60 dark:hover:text-white"
            }`}
          >
            <TableIcon className="size-3.5" />
            <span>数据明细表</span>
          </button>
        </div>

        <button
          onClick={handleCopyLatex}
          className="btn-ghost text-xs flex items-center gap-1.5 py-1.5 px-3"
          title="导出学术论文 LaTeX 三线表代码 (booktabs 格式)"
        >
          {copied ? <Check className="size-3.5 text-green-600" /> : <Copy className="size-3.5" />}
          <span>{copied ? "已复制 LaTeX 三线表" : "复制 LaTeX 三线表"}</span>
        </button>
      </div>

      {/* Visualization Panel */}
      {viewMode === "chart" ? (
        <div className="rounded-lg border border-hairline bg-white p-4 dark:border-white/[0.08] dark:bg-white/[0.02]">
          <div className="h-64 w-full">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart
                data={chartData}
                margin={{ top: 15, right: 20, left: -10, bottom: 5 }}
              >
                <CartesianGrid strokeDasharray="3 3" opacity={0.15} />
                <XAxis
                  dataKey="name"
                  tick={{ fontSize: 11 }}
                  interval={0}
                  tickLine={false}
                />
                <YAxis tick={{ fontSize: 11 }} tickLine={false} />
                <Tooltip
                  content={({ active, payload, label }) => {
                    if (active && payload && payload.length) {
                      const ours = Number(payload[0]?.value ?? 0);
                      const comp = Number(payload[1]?.value ?? 0);
                      const unit = payload[0]?.payload?.unit || "%";
                      const delta = Number(payload[0]?.payload?.delta ?? 0);
                      const isWarn = Boolean(payload[0]?.payload?.warning);

                      return (
                        <div className="rounded-md border border-hairline bg-white/95 p-2.5 shadow-md text-xs backdrop-blur-xs dark:border-white/10 dark:bg-neutral-900/95 dark:text-white space-y-1.5">
                          <div className="font-semibold border-b border-hairline pb-1 dark:border-white/10">
                            {label}
                          </div>
                          <div className="flex items-center justify-between gap-4 text-teal">
                            <span>在研模型 (Ours):</span>
                            <span className="font-mono font-medium">
                              {ours}
                              {unit}
                            </span>
                          </div>
                          <div className="flex items-center justify-between gap-4 text-rose-500">
                            <span>外部竞品 (Competitor):</span>
                            <span className="font-mono font-medium">
                              {comp}
                              {unit}
                            </span>
                          </div>
                          <div className="pt-1 border-t border-hairline dark:border-white/10 flex items-center justify-between text-[11px]">
                            <span>指标差距 (Delta):</span>
                            <span
                              className={`font-mono font-bold ${
                                isWarn ? "text-rose-600" : "text-teal"
                              }`}
                            >
                              {delta > 0 ? `+${delta}` : delta}
                              {unit}
                            </span>
                          </div>
                        </div>
                      );
                    }
                    return null;
                  }}
                />
                <Legend
                  wrapperStyle={{ fontSize: 12, paddingTop: 10 }}
                  iconType="circle"
                />
                <Bar
                  dataKey="在研模型 (Ours)"
                  fill="#0D9488"
                  radius={[4, 4, 0, 0]}
                  barSize={24}
                />
                <Bar
                  dataKey="外部竞品 (Competitor)"
                  fill="#E11D48"
                  radius={[4, 4, 0, 0]}
                  barSize={24}
                />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-hairline dark:border-white/[0.08]">
          <table className="table-base min-w-[500px]">
            <thead>
              <tr>
                <th>数据集 (Dataset)</th>
                <th>评估指标 (Metric)</th>
                <th>在研模型 (Ours)</th>
                <th>外部竞品</th>
                <th>差值 (Delta)</th>
                <th>态势评估</th>
              </tr>
            </thead>
            <tbody>
              {data.map((item, idx) => {
                const isWarn = item.warning;
                const sign = item.delta > 0 ? "+" : "";

                return (
                  <tr
                    key={idx}
                    className={
                      isWarn
                        ? "bg-rose-50/40 dark:bg-rose-500/[0.04]"
                        : "bg-teal-50/20 dark:bg-teal-500/[0.02]"
                    }
                  >
                    <td className="font-medium dark:text-white">{item.dataset}</td>
                    <td className="text-neutral-600 dark:text-white/80">{item.metric}</td>
                    <td className="font-mono text-teal font-medium">
                      {item.our_value}
                      {item.unit || "%"}
                    </td>
                    <td className="font-mono text-neutral-800 dark:text-white/95 font-medium">
                      {item.competitor_value}
                      {item.unit || "%"}
                    </td>
                    <td className="font-mono">
                      <span
                        className={`inline-flex items-center gap-0.5 px-2 py-0.5 rounded text-[11px] font-semibold ${
                          isWarn
                            ? "bg-rose-100 text-rose-700 dark:bg-rose-500/20 dark:text-rose-300"
                            : "bg-teal-100 text-teal dark:bg-teal/20 dark:text-teal"
                        }`}
                      >
                        {isWarn ? (
                          <TrendingUp className="size-3" />
                        ) : (
                          <TrendingDown className="size-3" />
                        )}
                        {sign}
                        {item.delta}
                        {item.unit || "%"}
                      </span>
                    </td>
                    <td>
                      {item.empirical_dominance || item.warning_level === "critical" ? (
                        <span className="badge-red font-mono text-[10px] font-bold">
                          EMPIRICAL_DOMINANCE
                        </span>
                      ) : isWarn ? (
                        <span className="badge-red text-[10px]">竞品超越</span>
                      ) : (
                        <span className="badge-green text-[10px]">在研领先</span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
