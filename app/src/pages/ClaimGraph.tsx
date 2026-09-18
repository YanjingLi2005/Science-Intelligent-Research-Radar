import { useMemo, useState } from 'react';
import { useNavigate } from 'react-router';
import { ArrowRight, Sparkles, FileText, Globe } from 'lucide-react';
import { Empty, Reveal, SectionHead } from '../components/chrome';
import ErrorRetry from '../components/ErrorRetry';
import { Skeleton } from '../components/ui/skeleton';
import { getClaimGraph, useApi, type ClaimGraphNode } from '../api';
import { useProject } from '../contexts/ProjectContext';

interface PositionedNode {
  id: string;
  label: string;
  type: string;
  x: number;
  y: number;
  width: number;
  height: number;
  color: string;
  raw: ClaimGraphNode;
}

const claimColors: Record<string, string> = {
  confirmed: '#22c55e',
  edited: '#22c55e',
  candidate: '#f59e0b',
  rejected: '#ef4444',
  dismissed: '#ef4444',
};
const sourceColors: Record<string, string> = {
  retracted: '#ef4444',
  expression_of_concern: '#f97316',
  corrected: '#f97316',
  normal: '#9ca3af',
};

function getColor(node: ClaimGraphNode) {
  const state = String(node.data.review_state ?? node.data.integrity_state ?? 'normal');
  return node.type === 'claim' ? claimColors[state] ?? '#f59e0b' : sourceColors[state] ?? '#9ca3af';
}

function nodeLabel(node: ClaimGraphNode) {
  return node.label || (node.type === 'source' ? '未命名来源' : '未命名 Claim');
}

function ClaimGraphSkeleton() {
  return (
    <div className="space-y-6 animate-pulse">
      <div className="flex items-center justify-between">
        <div>
          <Skeleton className="h-4 w-24 mb-2" />
          <Skeleton className="h-7 w-36" />
        </div>
        <Skeleton className="h-9 w-28 rounded-lg" />
      </div>
      <div className="flex items-center gap-4">
        <Skeleton className="h-4 w-12" />
        <Skeleton className="h-4 w-20 rounded-full" />
        <Skeleton className="h-4 w-20 rounded-full" />
        <Skeleton className="h-4 w-20 rounded-full" />
      </div>
      <div className="card min-h-[380px] p-6 flex flex-col justify-between">
        <div className="grid grid-cols-2 gap-12">
          <div className="space-y-4">
            <Skeleton className="h-14 w-full rounded-xl" />
            <Skeleton className="h-14 w-full rounded-xl" />
            <Skeleton className="h-14 w-full rounded-xl" />
          </div>
          <div className="space-y-4">
            <Skeleton className="h-14 w-full rounded-xl" />
            <Skeleton className="h-14 w-full rounded-xl" />
            <Skeleton className="h-14 w-full rounded-xl" />
          </div>
        </div>
        <Skeleton className="h-4 w-64 mt-6" />
      </div>
    </div>
  );
}

export default function ClaimGraph() {
  const navigate = useNavigate();
  const { projectId } = useProject();
  const { data, loading, error, refetch } = useApi(() => getClaimGraph(projectId), [projectId]);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);

  const layout = useMemo(() => {
    const nodes: PositionedNode[] = [];
    const claims = (data?.nodes ?? []).filter((node) => node.type === 'claim').slice(0, 60);
    const sources = (data?.nodes ?? []).filter((node) => node.type === 'source').slice(0, 40);
    claims.forEach((node, index) => {
      nodes.push({
        id: node.id,
        label: nodeLabel(node),
        type: node.type,
        x: 20,
        y: 30 + index * 70,
        width: 260,
        height: 58,
        color: getColor(node),
        raw: node,
      });
    });
    sources.forEach((node, index) => {
      nodes.push({
        id: node.id,
        label: nodeLabel(node),
        type: node.type,
        x: 360,
        y: 30 + index * 66,
        width: 240,
        height: 52,
        color: getColor(node),
        raw: node,
      });
    });
    const byId = new Map(nodes.map((node) => [node.id, node]));
    const edges = (data?.edges ?? []).filter((edge) => byId.has(edge.source) && byId.has(edge.target)).slice(0, 200);
    const width = 640;
    const height = Math.max(220, Math.max(claims.length, sources.length) * 70 + 60);
    return { nodes, edges, byId, width, height };
  }, [data]);

  const selectedNode = selectedNodeId ? layout.byId.get(selectedNodeId) : null;

  if (loading) {
    return <ClaimGraphSkeleton />;
  }

  if (error) {
    return (
      <div className="space-y-6">
        <SectionHead
          kicker="Claim · Source"
          title="演化图谱"
          right={<button className="btn-secondary" onClick={() => navigate(`/cases/${projectId}/paper`)}>返回论文 Claim</button>}
        />
        <div className="card p-6">
          <ErrorRetry message={`图谱加载失败：${error.message}`} onRetry={() => void refetch()} />
        </div>
      </div>
    );
  }

  return (
    <Reveal>
      <SectionHead
        kicker="Claim · Source"
        title="演化图谱"
        right={<button className="btn-secondary" onClick={() => navigate(`/cases/${projectId}/paper`)}>返回论文 Claim</button>}
      />
      {(layout.nodes.length === 0) ? (
        <div className="card mt-6 p-8 text-center">
          <Empty text="还没有可视化数据。提取 Claim 并关联公开来源后，演化图谱将在此呈现。" />
        </div>
      ) : (
        <>
          <div className="mt-5 flex flex-wrap items-center justify-between gap-y-2">
            <div className="flex flex-wrap items-center gap-x-5 gap-y-2 text-[11px] text-neutral-500">
              <span className="font-medium text-neutral-700 dark:text-white/90">图例</span>
              <span><i className="mr-1 inline-block h-2.5 w-2.5 rounded-full bg-green-500" />已确认 Claim</span>
              <span><i className="mr-1 inline-block h-2.5 w-2.5 rounded-full bg-amber-500" />候选 Claim</span>
              <span><i className="mr-1 inline-block h-2.5 w-2.5 rounded-full bg-red-500" />拒绝 / 冲突 / 撤稿</span>
              <span><i className="mr-1 inline-block h-2.5 w-2.5 rounded-full bg-gray-400" />普通来源</span>
            </div>
            <span className="text-[11px] text-neutral-400 dark:text-white/60">
              点击节点查看详情与快捷联动
            </span>
          </div>

          <div className="mt-4 overflow-auto rounded-xl border border-hairline bg-neutral-50 dark:bg-[#151518] shadow-sm">
            <svg
              width={layout.width}
              height={layout.height}
              viewBox={`0 0 ${layout.width} ${layout.height}`}
              className="block h-auto min-w-[640px]"
              role="img"
              aria-label="Claim 与来源演化图谱"
            >
              {layout.edges.map((edge, index) => {
                const source = layout.byId.get(edge.source);
                const target = layout.byId.get(edge.target);
                if (!source || !target) return null;
                const isConnectedToSelected = selectedNodeId && (edge.source === selectedNodeId || edge.target === selectedNodeId);
                const sx = source.x + source.width;
                const sy = source.y + source.height / 2;
                const tx = target.x;
                const ty = target.y + target.height / 2;
                const relation = edge.data?.relation ?? '';
                const baseColor = relation === 'supersedes' ? '#8b5cf6' : '#9ca3af';
                const strokeColor = isConnectedToSelected ? '#3b82f6' : baseColor;
                const strokeWidth = isConnectedToSelected ? 2.5 : 1.5;
                return (
                  <g key={`${edge.id ?? index}`}>
                    <line x1={sx} y1={sy} x2={tx} y2={ty} stroke={strokeColor} strokeWidth={strokeWidth} strokeDasharray={isConnectedToSelected ? '4 2' : undefined} />
                    <circle cx={tx} cy={ty} r={isConnectedToSelected ? 4.5 : 3} fill={strokeColor} />
                    {relation && <text x={(sx + tx) / 2} y={(sy + ty) / 2 - 4} fontSize={9} fill={isConnectedToSelected ? '#3b82f6' : '#71717a'} fontWeight={isConnectedToSelected ? 600 : 400} textAnchor="middle">{relation}</text>}
                  </g>
                );
              })}
              {layout.nodes.map((node) => {
                const isSelected = selectedNodeId === node.id;
                return (
                  <g
                    key={node.id}
                    className="cursor-pointer transition-transform hover:opacity-90"
                    onClick={() => setSelectedNodeId(isSelected ? null : node.id)}
                  >
                    <rect
                      x={node.x}
                      y={node.y}
                      width={node.width}
                      height={node.height}
                      rx={9}
                      fill="var(--graph-node-bg, #ffffff)"
                      stroke={isSelected ? '#3b82f6' : node.color}
                      strokeWidth={isSelected ? 2.5 : 1.5}
                      filter={isSelected ? 'drop-shadow(0 4px 6px rgba(59, 130, 246, 0.2))' : undefined}
                    />
                    <rect x={node.x} y={node.y} width={5} height={node.height} rx={2} fill={node.color} />
                    <text x={node.x + 14} y={node.y + 20} fontSize={11} fontWeight={600} fill="var(--graph-node-text, #18181b)">
                      {node.type === 'claim' ? 'Claim' : 'Source'}
                      {isSelected && ' ✓'}
                    </text>
                    <text x={node.x + 14} y={node.y + 38} fontSize={10} fill="var(--graph-node-text, #18181b)">
                      {node.label.length > 34 ? `${node.label.slice(0, 34)}…` : node.label}
                    </text>
                  </g>
                );
              })}
            </svg>
          </div>

          {/* Node detail inspection & action drawer */}
          {selectedNode && (
            <div className="mt-4 rounded-xl border border-blue-200 bg-blue-50/70 p-4 transition-all duration-200 dark:border-blue-500/20 dark:bg-blue-500/10">
              <div className="flex flex-col md:flex-row md:items-center justify-between gap-3">
                <div className="space-y-1">
                  <div className="flex items-center gap-2">
                    <span className="font-mono text-[11px] font-semibold text-blue-800 dark:text-blue-200 uppercase tracking-wider">
                      {selectedNode.type === 'claim' ? 'Claim 详情' : '文献来源详情'}
                    </span>
                    <span className="chip text-[10px] font-mono">{selectedNode.id}</span>
                  </div>
                  <p className="text-xs text-neutral-800 dark:text-white/95 leading-relaxed font-medium">
                    {selectedNode.label}
                  </p>
                </div>
                <div className="flex items-center gap-2 shrink-0">
                  {selectedNode.type === 'claim' ? (
                    <>
                      <button
                        onClick={() => navigate(`/cases/${projectId}/improve`)}
                        className="btn-primary !py-1.5 !px-3 text-xs flex items-center gap-1"
                      >
                        <Sparkles size={13} /> 前往改写补丁 (Improve)
                      </button>
                      <button
                        onClick={() => navigate(`/cases/${projectId}/paper`)}
                        className="btn-secondary !py-1.5 !px-3 text-xs flex items-center gap-1"
                      >
                        <FileText size={13} /> 论文 Claim 列表
                      </button>
                    </>
                  ) : (
                    <button
                      onClick={() => navigate(`/cases/${projectId}/radar`)}
                      className="btn-primary !py-1.5 !px-3 text-xs flex items-center gap-1"
                    >
                      <Globe size={13} /> 前往文献雷达
                    </button>
                  )}
                  <button
                    onClick={() => setSelectedNodeId(null)}
                    className="btn-quiet !py-1.5 !px-2 text-xs"
                  >
                    关闭
                  </button>
                </div>
              </div>
            </div>
          )}

          <div className="mt-3 flex items-center gap-2 text-[11px] text-neutral-400 dark:text-white/60">
            <ArrowRight size={12} />
            点击图谱中的任意节点可高亮关联链路并一键跳转至论文改造工作台。
          </div>
        </>
      )}
    </Reveal>
  );
}
