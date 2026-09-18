import { useState } from 'react';
import {
  getAudit,
  getSkillRuns,
  getSkillPipelineRuns,
  approvePatch,
  rejectPatch,
  generatePatch,
  exportPatchDiff,
  exportGitPatch,
  exportUnifiedDiff,
  applyLocalPatches,
  generateMultiLocationPatch,
  executeSkill,
  runSkillPipeline,
  simulateArena,
  useApi,
} from '../api';
import type { ArenaSimulationResult, MultiLocationEdit, SkillPipelineResult } from '../api';
import { claimStatusMeta, verdictMeta, sourceLabel } from '../data/mock';
import { Check, CheckCircle2, Copy, Download } from 'lucide-react';
import { Reveal, SectionHead } from '../components/chrome';
import { Spinner } from '../components/ui/spinner';
import { useProject } from '../contexts/ProjectContext';
import { SkillResultView } from '../components/SkillResult';
import ErrorRetry from '../components/ErrorRetry';

const SKILLS = [
  { name: 'ccf_paper_writer', label: '论文写作', actionType: 'writing', desc: '根据影响修改正文、润色、压缩或调整定位' },
  { name: 'ccf_paper_reviewer', label: '投稿评审', actionType: 'review', desc: '评估投稿风险和 novety 影响' },
  { name: 'ccf_integrity_auditor', label: '完整性审计', actionType: 'audit', desc: '检查 claim/数字/引用一致性' },
  { name: 'ccf_experiment_designer', label: '实验设计', actionType: 'experiment', desc: '设计对照实验、消融、结果表' },
  { name: 'ccf_rebuttal_writer', label: 'Rebuttal 准备', actionType: 'rebuttal', desc: '基于影响准备审稿回应' },
];

const ARENA_PERSONAS = [
  { id: 'method_strict', label: '理论/方法严苛型', description: '追问假设、方法设计与论证链条。' },
  { id: 'innovation_skeptic', label: '创新质疑型', description: '审视新颖性、先前工作与贡献边界。' },
  { id: 'experiment_generalization', label: '实验泛化型', description: '检查实验充分性、公平性与泛化能力。' },
  { id: 'ac_arbiter', label: 'AC 仲裁者', description: '综合权衡贡献、证据、风险与可回应性。' },
];

export default function Improve() {
  const { project, loading: projectLoading, error: projectError, projectId, refreshProject } = useProject();
  const { data: audit, loading: auditLoading, error: auditError, refetch: refetchAudit } = useApi(
    () => getAudit(projectId),
    [projectId],
  );
  const { data: skillRuns, loading: skillRunsLoading, error: skillRunsError, refetch: refetchSkillRuns } = useApi(
    () => getSkillRuns(projectId),
    [projectId],
  );
  const { data: pipelineRuns, loading: pipelineRunsLoading, error: pipelineRunsError, refetch: refetchPipelineRuns } = useApi(
    () => getSkillPipelineRuns(projectId),
    [projectId],
  );
  const [verdict, setVerdict] = useState<'none' | 'approved' | 'rejected'>('none');
  const [exported, setExported] = useState(false);
  const [patchSubmitting, setPatchSubmitting] = useState(false);
  const [patchError, setPatchError] = useState('');
  const [gitInstructions, setGitInstructions] = useState('');
  const [copiedLatex, setCopiedLatex] = useState(false);
  const [applySuccessMessage, setApplySuccessMessage] = useState('');

  // Multi-location patch state
  const [multiEdits, setMultiEdits] = useState<MultiLocationEdit[]>([]);
  const [multiRationale, setMultiRationale] = useState('');
  const [multiLoading, setMultiLoading] = useState(false);
  const [multiError, setMultiError] = useState('');

  // Skill execution state
  const [skillResult, setSkillResult] = useState<Record<string, unknown> | null>(null);
  const [skillLoading, setSkillLoading] = useState<string | null>(null);
  const [skillError, setSkillError] = useState('');
  const [arenaPersonas, setArenaPersonas] = useState<string[]>(ARENA_PERSONAS.map((persona) => persona.id));
  const [arenaFocus, setArenaFocus] = useState('');
  const [arenaResult, setArenaResult] = useState<ArenaSimulationResult | null>(null);
  const [arenaLoading, setArenaLoading] = useState(false);
  const [arenaError, setArenaError] = useState('');
  const [pipelineResult, setPipelineResult] = useState<SkillPipelineResult | null>(null);
  const [pipelineLoading, setPipelineLoading] = useState(false);
  const [pipelineError, setPipelineError] = useState('');
  const [pipelineSkills, setPipelineSkills] = useState<string[]>([
    'ccf_paper_writer',
    'ccf_integrity_auditor',
    'ccf_paper_reviewer',
  ]);
  const [templateName, setTemplateName] = useState('');
  const [savedTemplates, setSavedTemplates] = useState<{ name: string; skills: string[] }[]>(() => {
    try {
      return JSON.parse(localStorage.getItem('radar_pipeline_templates') ?? '[]');
    } catch {
      return [];
    }
  });

  if (projectLoading) {
    return (
      <div className="flex items-center justify-center py-20">
        <Spinner className="size-6 text-neutral-400" />
      </div>
    );
  }

  if (projectError || !project) {
    return (
      <div className="card p-6">
        <div className="rounded-lg border border-red-200 bg-red-50 px-3.5 py-2.5 text-[12.5px] text-red-700 dark:border-red-500/25 dark:bg-red-500/10 dark:text-red-300">
          <ErrorRetry message={projectError?.message ?? '未找到项目'} onRetry={() => void refreshProject()} />
        </div>
      </div>
    );
  }

  const exportJSON = () => {
    const data = { project: project.name, exportedAt: new Date().toISOString(), claims: project.claims };
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `evidence-pack-${project.id}.json`;
    a.click();
    URL.revokeObjectURL(a.href);
    setExported(true);
  };

  const downloadJSON = (data: unknown, filename: string) => {
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = filename;
    a.click();
    URL.revokeObjectURL(a.href);
  };

  const downloadMarkdown = (data: unknown, filename: string) => {
    const markdown = `# CCF-A Skill Output\n\n\`\`\`json\n${JSON.stringify(data, null, 2)}\n\`\`\`\n`;
    const blob = new Blob([markdown], { type: 'text/markdown;charset=utf-8' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = filename;
    a.click();
    URL.revokeObjectURL(a.href);
  };

  const handleApprove = async () => {
    if (!rw.patchId) {
      setPatchError('当前没有可审批的改写补丁。请先从已确认的影响生成补丁。');
      return;
    }
    setPatchSubmitting(true);
    setPatchError('');
    try {
      await approvePatch(projectId, rw.patchId);
      setVerdict('approved');
    } catch (e) {
      setPatchError(e instanceof Error ? e.message : String(e));
    } finally {
      setPatchSubmitting(false);
    }
  };

  const handleGenerate = async () => {
    const impact = (project?.papers ?? []).find(
      (p) => p.reviewState === 'confirmed' || p.reviewState === 'edited',
    );
    if (!impact) {
      setPatchError('没有已采纳的影响，请先确认影响判断再生成改写。');
      return;
    }
    setPatchSubmitting(true);
    setPatchError('');
    try {
      await generatePatch(projectId, impact.id);
      setVerdict('none');
      await refreshProject();
    } catch (e) {
      setPatchError(e instanceof Error ? e.message : String(e));
    } finally {
      setPatchSubmitting(false);
    }
  };

  const handleExportDiff = async () => {
    if (!rw.patchId) return;
    setPatchSubmitting(true);
    setPatchError('');
    try {
      const result = await exportPatchDiff(projectId, rw.patchId);
      const blob = new Blob([result.diff], { type: 'text/plain;charset=utf-8' });
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = `patch-${rw.patchId}.diff.txt`;
      a.click();
      URL.revokeObjectURL(a.href);
    } catch (e) {
      setPatchError(e instanceof Error ? e.message : String(e));
    } finally {
      setPatchSubmitting(false);
    }
  };

  const handleExportGitPatch = async () => {
    setPatchSubmitting(true);
    setPatchError('');
    try {
      const result = await exportGitPatch(projectId);
      const blob = new Blob([result.patch_content], { type: 'text/x-patch;charset=utf-8' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = result.filename;
      a.click();
      URL.revokeObjectURL(url);
      setGitInstructions(result.apply_instructions);
    } catch (e) {
      setPatchError(e instanceof Error ? e.message : String(e));
    } finally {
      setPatchSubmitting(false);
    }
  };

  const handleDownloadUnifiedPatch = async () => {
    setPatchSubmitting(true);
    setPatchError('');
    try {
      const result = await exportUnifiedDiff(projectId);
      const blob = new Blob([result.diff], { type: 'text/x-diff;charset=utf-8' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = result.filename;
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      setPatchError(e instanceof Error ? e.message : String(e));
    } finally {
      setPatchSubmitting(false);
    }
  };

  const handleCopyLatexBlock = async () => {
    try {
      const text = rw.patchedSentence || rw.after;
      await navigator.clipboard.writeText(text);
      setCopiedLatex(true);
      setTimeout(() => setCopiedLatex(false), 2500);
    } catch {
      setPatchError('复制到剪贴板失败，请手动选择复制。');
    }
  };

  const handleApplyLocal = async () => {
    setPatchSubmitting(true);
    setPatchError('');
    setApplySuccessMessage('');
    try {
      const result = await applyLocalPatches(projectId);
      if (result.success) {
        setApplySuccessMessage(
          `已成功将 ${result.applied_count} 处补丁应用至 ${result.file_name}` +
          (result.bib_updated ? ` 并同步追加更新了 BibTeX 引用库！` : '！')
        );
        refreshProject();
      } else {
        setPatchError(result.message || '没有可应用的改写补丁。');
      }
    } catch (e) {
      setPatchError(e instanceof Error ? e.message : String(e));
    } finally {
      setPatchSubmitting(false);
    }
  };

  const handleReject = async () => {
    if (!rw.patchId) {
      setPatchError('当前没有可驳回的改写补丁。');
      return;
    }
    setPatchSubmitting(true);
    setPatchError('');
    try {
      await rejectPatch(projectId, rw.patchId);
      setVerdict('rejected');
    } catch (e) {
      setPatchError(e instanceof Error ? e.message : String(e));
    } finally {
      setPatchSubmitting(false);
    }
  };

  const handleMultiLocationPatch = async (impactId: string) => {
    setMultiLoading(true);
    setMultiError('');
    try {
      const result = await generateMultiLocationPatch(projectId, impactId);
      setMultiEdits(result.edits);
      setMultiRationale(result.global_rationale);
    } catch (e) {
      setMultiError(e instanceof Error ? e.message : String(e));
    } finally {
      setMultiLoading(false);
    }
  };

  const handleSkillExecute = async (skillName: string, actionType: string) => {
    setSkillLoading(skillName);
    setSkillError('');
    try {
      // Evidence kinds are support/challenge/completeness — every confirmed
      // impact linked to a claim is relevant context for the skills.
      const impactIds = project.claims.flatMap((c) =>
        c.evidence.map((e) => e.paperId)
      );
      const result = await executeSkill(projectId, skillName, actionType, impactIds);
      setSkillResult(result.content);
    } catch (e) {
      setSkillError(e instanceof Error ? e.message : String(e));
    } finally {
      setSkillLoading(null);
    }
  };

  const handlePipeline = async (skillNames?: string[]) => {
    setPipelineLoading(true);
    setPipelineError('');
    setPipelineResult(null);
    try {
      const impactIds = project.claims.flatMap((c) =>
        c.evidence.map((e) => e.paperId)
      );
      const result = await runSkillPipeline(projectId, {
        pipeline: skillNames ? 'custom' : 'revision',
        skill_names: skillNames,
        impact_ids: impactIds,
      });
      setPipelineResult(result);
    } catch (e) {
      setPipelineError(e instanceof Error ? e.message : String(e));
    } finally {
      setPipelineLoading(false);
    }
  };

  const handleArena = async () => {
    if (arenaPersonas.length === 0) return;
    setArenaLoading(true);
    setArenaError('');
    setArenaResult(null);
    try {
      setArenaResult(await simulateArena(projectId, { personas: arenaPersonas, focus: arenaFocus }));
    } catch (e) {
      setArenaError(e instanceof Error ? e.message : String(e));
    } finally {
      setArenaLoading(false);
    }
  };

  const saveTemplate = () => {
    const name = templateName.trim();
    if (!name || pipelineSkills.length === 0) return;
    const next = [...savedTemplates.filter((t) => t.name !== name), { name, skills: pipelineSkills }];
    setSavedTemplates(next);
    localStorage.setItem('radar_pipeline_templates', JSON.stringify(next));
    setTemplateName('');
  };

  const loadTemplate = (name: string) => {
    const template = savedTemplates.find((t) => t.name === name);
    if (template) setPipelineSkills(template.skills);
  };

  const rw = project.rewrite;
  const allOk = rw.checks.every((c) => c.ok);

  return (
    <div>
      <Reveal>
        <div className="flex flex-wrap items-end justify-between gap-4 mb-8">
          <div>
            <div className="kicker mb-1.5">Improve · Transformation</div>
            <h2 className="text-[17px] font-semibold tracking-tight">改造工作台</h2>
            <p className="mt-2 text-[12.5px] text-neutral-500 dark:text-white/80">
              把雷达确认的新证据，改造成论文的下一步：补实验、改表述、补引用。
            </p>
          </div>
          <button className="btn-ghost" onClick={exportJSON}>
            导出证据包 JSON
          </button>
        </div>
        {exported && <p className="badge-green badge-dot mb-6">已导出 evidence-pack-{project.id}.json</p>}
      </Reveal>

      {/* claim ledger */}
      <Reveal delay={60}>
        <SectionHead kicker="Claims" title="Claim 账本" />
        <div className="card overflow-x-auto">
          <table className="table-base min-w-[760px]">
            <thead>
              <tr>
                <th>Claim</th>
                <th>状态</th>
                <th>支持</th>
                <th>挑战</th>
                <th>完整性</th>
              </tr>
            </thead>
            <tbody>
              {project.claims.map((c) => (
                <tr key={c.id}>
                  <td className="max-w-[340px]">
                    <div className="flex items-center gap-2">
                      <span className="font-mono text-[11px] text-neutral-400 dark:text-white/80 shrink-0">{c.id}</span>
                      {c.radarWatch && <span className="badge-teal">Radar 关注</span>}
                    </div>
                    <div className="mt-1.5 text-[13px] text-neutral-700 dark:text-white/95 leading-relaxed">{c.text}</div>
                  </td>
                  <td>
                    <span className={claimStatusMeta[c.status].cls}>{claimStatusMeta[c.status].label}</span>
                  </td>
                  {(['support', 'challenge', 'completeness'] as const).map((k) => (
                    <td key={k} className="max-w-[190px]">
                      {c.evidence.filter((e) => e.kind === k).length === 0 && <span className="text-neutral-300 dark:text-white/60">—</span>}
                      <ul className="space-y-1.5">
                        {c.evidence
                          .filter((e) => e.kind === k)
                          .map((e) => {
                            const pp = project.papers.find((p) => p.id === e.paperId);
                            return (
                              <li key={e.paperId} className="text-xs text-neutral-500 dark:text-white/85 leading-snug">
                                {e.note}
                                {pp && <span className="locator block">{sourceLabel(pp)} · {verdictMeta[pp.verdict].label}</span>}
                              </li>
                            );
                          })}
                      </ul>
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Reveal>

      {/* CCF-A Skills Panel */}
      <Reveal delay={80} className="mt-12">
        <SectionHead
          kicker="Agent Skills"
          title="CCF-A 智能技能"
          right={
            <button
              className="btn-ghost text-xs"
              disabled={pipelineLoading}
              onClick={() => void handlePipeline()}
            >
              {pipelineLoading ? '流水线运行中…' : '⚡ 一键技能流水线'}
            </button>
          }
        />
        {pipelineError && (
          <div role="alert" className="mt-3 rounded-lg border border-red-200 bg-red-50 px-3.5 py-2.5 text-[12.5px] text-red-700 dark:border-red-500/25 dark:bg-red-500/10 dark:text-red-300">
            {pipelineError}
          </div>
        )}
        {pipelineResult && (
          <div className="card mt-4 p-6">
            <h3 className="text-sm font-semibold text-ink dark:text-white mb-3">
              技能流水线结果 · {pipelineResult.pipeline}
            </h3>
            <div className="space-y-3">
              {pipelineResult.results.map((r) => (
                <div key={r.skill} className="rounded-lg border border-hairline p-3 dark:border-white/[0.08]">
                  <div className="flex items-center gap-2">
                    <span className="font-mono text-[12px]">{r.skill}</span>
                    <span className="locator">{r.artifact_type}</span>
                  </div>
                  <SkillResultView output={r.content} className="mt-3" />
                </div>
              ))}
            </div>
          </div>
        )}
        <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {SKILLS.map((skill) => (
            <div key={skill.name} className="card card-hover p-5">
              <h3 className="text-sm font-semibold text-ink dark:text-white mb-1.5">{skill.label}</h3>
              <p className="text-xs text-neutral-500 dark:text-white/85 mb-4 leading-relaxed">{skill.desc}</p>
              <button
                className="btn-ghost text-xs"
                disabled={skillLoading === skill.name}
                onClick={() => handleSkillExecute(skill.name, skill.actionType)}
              >
                {skillLoading === skill.name ? '执行中...' : `执行 ${skill.label}`}
              </button>
            </div>
          ))}
        </div>
        <div className="card mt-4 p-5">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-xs font-medium text-neutral-500 dark:text-white/85">自定义流水线：</span>
            {SKILLS.map((skill) => {
              const active = pipelineSkills.includes(skill.name);
              return (
                <button
                  key={skill.name}
                  className={`rounded-full border px-2.5 py-1 text-[10px] transition-colors ${
                    active
                      ? 'border-ink bg-ink text-white dark:border-white dark:bg-white dark:text-[#111113]'
                      : 'border-hairline text-neutral-500 hover:border-neutral-400 dark:border-white/10 dark:text-white/85'
                  }`}
                  onClick={() =>
                    setPipelineSkills((current) =>
                      active
                        ? current.filter((name) => name !== skill.name)
                        : [...current, skill.name]
                    )
                  }
                >
                  {skill.label}
                </button>
              );
            })}
            <button
              className="btn-ghost text-xs ml-auto"
              disabled={pipelineLoading || pipelineSkills.length === 0}
              onClick={() => void handlePipeline(pipelineSkills)}
            >
              {pipelineLoading ? '运行中…' : '运行自定义流水线'}
            </button>
          </div>
          <div className="mt-3 flex flex-wrap items-center gap-2 pt-3 hairline-t">
            <input
              className="input !w-44 !py-1.5 text-xs"
              placeholder="模板名称"
              value={templateName}
              onChange={(e) => setTemplateName(e.target.value)}
            />
            <button
              className="btn-ghost text-xs"
              disabled={!templateName.trim() || pipelineSkills.length === 0}
              onClick={saveTemplate}
            >
              保存模板
            </button>
            {savedTemplates.length > 0 && (
              <select
                className="input !w-44 !py-1.5 text-xs ml-auto"
                value=""
                onChange={(e) => { if (e.target.value) loadTemplate(e.target.value); }}
              >
                <option value="">加载模板…</option>
                {savedTemplates.map((t) => (
                  <option key={t.name} value={t.name}>{t.name}</option>
                ))}
              </select>
            )}
          </div>
        </div>
        {skillError && (
          <div role="alert" className="mt-3 rounded-lg border border-red-200 bg-red-50 px-3.5 py-2.5 text-[12.5px] text-red-700 dark:border-red-500/25 dark:bg-red-500/10 dark:text-red-300">
            {skillError}
          </div>
        )}
        {skillResult && (
          <div className="card mt-4 p-6">
            <h3 className="text-sm font-semibold text-ink dark:text-white mb-3">技能执行结果</h3>
            <SkillResultView output={skillResult} />
          </div>
        )}
      </Reveal>

      {/* Rebuttal Arena */}
      <Reveal delay={90} className="mt-12">
        <SectionHead
          kicker="Rebuttal Arena"
          title="Rebuttal 竞技场"
          right={<button className="btn-primary text-xs" disabled={arenaLoading || arenaPersonas.length === 0} onClick={() => void handleArena()}>{arenaLoading ? '模拟中…' : '开始模拟'}</button>}
        />
        <div className="card p-5">
          <p className="text-xs text-neutral-500 dark:text-white/80 mb-4">选择审稿人视角，先发现不同类型的质疑，再生成对应 rebuttal 要点。</p>
          <div className="grid sm:grid-cols-2 lg:grid-cols-4 gap-3">
            {ARENA_PERSONAS.map((persona) => {
              const selected = arenaPersonas.includes(persona.id);
              return <button key={persona.id} type="button" onClick={() => setArenaPersonas((current) => selected ? current.filter((id) => id !== persona.id) : [...current, persona.id])} className={`text-left rounded-lg border p-4 transition-colors ${selected ? 'border-ink bg-ink/[0.04] dark:border-white/50 dark:bg-white/[0.08]' : 'border-hairline opacity-60 dark:border-white/10'}`}>
                <div className="flex items-center justify-between gap-2"><span className="text-xs font-semibold text-ink dark:text-white">{persona.label}</span><span className={`size-3 rounded-full border ${selected ? 'border-ink bg-ink dark:border-white dark:bg-white' : 'border-neutral-300 dark:border-white/30'}`} /></div>
                <p className="mt-2 text-[11px] leading-relaxed text-neutral-500 dark:text-white/75">{persona.description}</p>
              </button>;
            })}
          </div>
          <input className="input mt-4 w-full text-xs" placeholder="可选：补充本轮评审关注点" value={arenaFocus} onChange={(e) => setArenaFocus(e.target.value)} />
        </div>
        {arenaError && <div role="alert" className="mt-3 rounded-lg border border-red-200 bg-red-50 px-3.5 py-2.5 text-[12.5px] text-red-700 dark:border-red-500/25 dark:bg-red-500/10 dark:text-red-300">{arenaError}</div>}
        {arenaResult && <div className="mt-4 space-y-4">
          <div className="flex items-center gap-3 text-xs text-neutral-500 dark:text-white/80"><span>{arenaResult.overall.persona_count} 个视角</span><span>平均分：{arenaResult.overall.average_score ?? '—'} / 5</span>{arenaResult.overall.warnings.length > 0 && <span className="badge-orange">部分结果有警告</span>}</div>
          {arenaResult.personas.map((persona) => <div key={persona.id} className="card p-5">
            <div className="flex flex-wrap items-baseline gap-2"><h3 className="text-sm font-semibold text-ink dark:text-white">{persona.label}</h3><span className="text-[11px] text-neutral-500 dark:text-white/70">{persona.description}</span><span className="ml-auto text-lg font-semibold tabular text-ink dark:text-white">{persona.review.overall_score ?? '—'}<span className="text-xs font-normal text-neutral-400"> / 5</span></span></div>
            <div className="mt-4 grid lg:grid-cols-2 gap-4">
              <div><div className="locator mb-2 uppercase">评审维度</div><div className="space-y-2">{persona.review.dimensions.map((dimension) => <div key={dimension.name} className="rounded-md bg-neutral-50 p-3 dark:bg-white/[0.05]"><div className="flex justify-between text-xs font-medium"><span>{dimension.name}</span><span>{dimension.score}/5</span></div><p className="mt-1 text-[11px] leading-relaxed text-neutral-500 dark:text-white/75">{dimension.comment}</p></div>)}{persona.review.dimensions.length === 0 && <p className="text-xs text-neutral-400">暂无结构化维度（可能未配置 LLM）</p>}</div></div>
              <div><div className="locator mb-2 uppercase">风险与回应</div><div className="space-y-2">{persona.review.risks.map((risk, index) => <div key={`${risk.risk_type}-${index}`} className="rounded-md border border-amber-200 bg-amber-50 p-3 dark:border-amber-500/20 dark:bg-amber-500/10"><div className="text-xs font-medium">{risk.risk_type} · {risk.severity}</div><p className="mt-1 text-[11px] text-neutral-600 dark:text-white/80">{risk.description}</p><p className="mt-1 text-[11px] text-neutral-500 dark:text-white/70">建议：{risk.recommendation}</p></div>)}{persona.review.risks.length === 0 && <p className="text-xs text-neutral-400">暂无风险条目</p>}</div></div>
            </div>
            <div className="mt-5 hairline-t pt-4"><div className="locator mb-2 uppercase">Rebuttal 要点 · {persona.rebuttal.strength_assessment}</div><div className="space-y-3">{persona.rebuttal.rebuttal_points.map((point, index) => <div key={index} className="rounded-md border border-hairline p-3 dark:border-white/10"><div className="text-xs font-medium text-ink dark:text-white">{point.reviewer_comment_summary}</div><p className="mt-1 text-[11px] leading-relaxed text-neutral-600 dark:text-white/80">回应：{point.response}</p><p className="mt-1 text-[11px] leading-relaxed text-neutral-500 dark:text-white/70">修改：{point.manuscript_change}</p></div>)}{persona.rebuttal.rebuttal_points.length === 0 && <p className="text-xs text-neutral-400">暂无 rebuttal 要点</p>}</div></div>
          </div>)}
        </div>}
      </Reveal>

      {/* Multi-location patch */}
      <Reveal delay={100} className="mt-12">
        <SectionHead
          kicker="Multi-Location Patch"
          title="多位置修改建议"
          right={
            <button
              className="btn-primary text-xs"
              disabled={multiLoading}
              onClick={() =>
                handleMultiLocationPatch(
                  project.claims[0]?.evidence?.[0]?.paperId ?? ''
                )
              }
            >
              {multiLoading ? '生成中...' : '生成多位置修改'}
            </button>
          }
        />
        {multiError && (
          <div role="alert" className="mb-3 rounded-lg border border-red-200 bg-red-50 px-3.5 py-2.5 text-[12.5px] text-red-700 dark:border-red-500/25 dark:bg-red-500/10 dark:text-red-300">
            {multiError}
          </div>
        )}
        {multiRationale && (
          <div className="mb-4 rounded-lg border border-amber-200 bg-amber-50 p-4 dark:border-amber-500/25 dark:bg-amber-500/10">
            <p className="text-xs text-amber-800 dark:text-amber-200 leading-relaxed">{multiRationale}</p>
          </div>
        )}
        <div className="space-y-4">
          {multiEdits.map((edit, i) => (
            <div key={i} className="card overflow-hidden">
              <div className="flex items-center gap-2 px-4 py-2.5 hairline-b">
                <span className="badge-teal text-xs">{edit.section}</span>
                <span className="badge-gray text-xs">{edit.edit_class}</span>
                <span className="ml-auto text-xs italic text-neutral-500 dark:text-white/85">{edit.reason}</span>
              </div>
              <div className="diff-grid">
                <div className="diff-cell diff-before">
                  <div className="text-[10px] text-red-400 dark:text-red-300 uppercase mb-1">Before</div>
                  <p className="diff-removed">
                    {edit.before_text.length > 300
                      ? edit.before_text.slice(0, 300) + '...'
                      : edit.before_text}
                  </p>
                </div>
                <div className="diff-cell diff-after">
                  <div className="text-[10px] text-green-400 dark:text-green-300 uppercase mb-1">After</div>
                  <p className="diff-added">
                    {edit.after_text.length > 300
                      ? edit.after_text.slice(0, 300) + '...'
                      : edit.after_text}
                  </p>
                </div>
              </div>
            </div>
          ))}
        </div>
      </Reveal>

      {/* minimal rewrite */}
      <Reveal delay={120} className="mt-12">
        <SectionHead
          kicker="Rewrite"
          title="最小改写方案"
          right={
            <div className="flex items-center gap-2">
              {rw.patchType && <span className="badge-teal text-xs font-mono">{rw.patchType}</span>}
              <span className="locator">{rw.claimId} · {rw.targetLocation || rw.loc}</span>
            </div>
          }
        />
        <div className="grid md:grid-cols-2 gap-4">
          <div className="card p-6">
            <div className="locator mb-3 uppercase">改写前 (Original Sentence)</div>
            <p className="quote quote-against text-[13.5px]">{rw.originalSentence || rw.before}</p>
          </div>
          <div className="card p-6">
            <div className="locator mb-3 uppercase">改写后 (Patched Sentence)</div>
            <p className="quote text-[13.5px]">{rw.patchedSentence || rw.after}</p>
          </div>
        </div>

        <div className="card mt-4 p-6">
          <div className="grid md:grid-cols-3 gap-x-8 gap-y-2.5">
            {rw.checks.map((c) => (
              <div key={c.label} className="flex items-center gap-2 text-[13px]">
                <span className={c.ok ? 'text-[#067647] dark:text-green-400' : 'text-[#B42318] dark:text-red-400'}>{c.ok ? '✓' : '✗'}</span>
                <span className={c.ok ? 'text-neutral-600 dark:text-white/90' : 'text-[#B42318] dark:text-red-400'}>{c.label}</span>
              </div>
            ))}
          </div>
          {patchError && (
            <div role="alert" className="mt-4 rounded-lg border border-red-200 bg-red-50 px-3.5 py-2.5 text-[12.5px] text-red-700 dark:border-red-500/25 dark:bg-red-500/10 dark:text-red-300">
              {patchError}
            </div>
          )}
          <div className="mt-6 pt-5 hairline-t flex flex-wrap items-center gap-3">
            {verdict === 'none' && (
              <>
                <button
                  className="btn-primary"
                  disabled={!rw.patchId || !allOk || patchSubmitting}
                  onClick={handleApprove}
                  title={allOk ? '' : '存在未通过的验证项'}
                >
                  批准并允许导出
                </button>
                <button className="btn-quiet" disabled={patchSubmitting} onClick={handleReject}>
                  拒绝这版改写
                </button>
                {!allOk && <span className="text-xs text-[#B54708] dark:text-amber-300">存在未通过的验证项，需先修正</span>}
                {!rw.patchId ? (
                  <button
                    className="btn-primary text-xs"
                    disabled={patchSubmitting}
                    onClick={handleGenerate}
                  >
                    生成最小改写
                  </button>
                ) : (
                  <button
                    className="btn-ghost text-xs"
                    disabled={patchSubmitting}
                    onClick={handleExportDiff}
                  >
                    导出补丁 Diff
                  </button>
                )}
              </>
            )}
            {verdict === 'approved' && <span className="badge-green badge-dot">已批准 · 可导出至文稿</span>}
            {verdict === 'rejected' && (
              <>
                <span className="badge-red badge-dot">已拒绝 · 已退回模型重生成</span>
                <button className="btn-quiet" onClick={() => setVerdict('none')}>撤销</button>
              </>
            )}
            <button className="btn-ghost text-xs" disabled={patchSubmitting} onClick={handleExportGitPatch}>
              导出 Git 补丁
            </button>
            <button
              className="btn-ghost text-xs flex items-center gap-1.5"
              disabled={patchSubmitting}
              onClick={handleCopyLatexBlock}
              title="复制修改后的 LaTeX 语句至剪贴板"
            >
              {copiedLatex ? <Check size={13} className="text-green-600" /> : <Copy size={13} />}
              {copiedLatex ? '已复制 LaTeX' : '复制 LaTeX'}
            </button>
            <button
              className="btn-ghost text-xs flex items-center gap-1.5"
              disabled={patchSubmitting}
              onClick={handleDownloadUnifiedPatch}
              title="下载标准 POSIX Unified Diff (.patch) 补丁"
            >
              <Download size={13} />
              下载 .patch 补丁
            </button>
            <button
              className="btn-primary text-xs flex items-center gap-1.5"
              disabled={patchSubmitting}
              onClick={handleApplyLocal}
              title="将已确认的改写与 BibTeX 直接写入本地论文文件"
            >
              <CheckCircle2 size={13} />
              一键应用到本地文件
            </button>
          </div>
          {applySuccessMessage && (
            <div role="status" className="mt-4 rounded-lg border border-green-200 bg-green-50 px-3.5 py-2.5 text-[12.5px] text-green-800 dark:border-green-500/25 dark:bg-green-500/10 dark:text-green-300 flex items-center gap-2">
              <CheckCircle2 size={15} className="shrink-0 text-green-600 dark:text-green-400" />
              <span>{applySuccessMessage}</span>
            </div>
          )}
          {gitInstructions && (
            <div className="mt-4 rounded-lg border border-blue-200 bg-blue-50 p-3 text-xs text-blue-800 dark:border-blue-500/25 dark:bg-blue-500/10 dark:text-blue-200">
              <div className="mb-1 font-medium">应用说明</div>
              <pre className="whitespace-pre-wrap font-mono">{gitInstructions}</pre>
              <button className="btn-quiet mt-2 text-xs" onClick={() => void navigator.clipboard?.writeText(gitInstructions)}>复制说明</button>
            </div>
          )}
        </div>
      </Reveal>

      {/* skill execution history */}
      <Reveal delay={130} className="mt-12">
        <SectionHead kicker="Skill History" title="CCF-A 技能执行历史" />
        <div className="card p-7">
          {skillRunsLoading ? (
            <div className="flex items-center justify-center py-8">
              <Spinner className="size-5 text-neutral-300" />
            </div>
          ) : skillRunsError ? (
            <div className="rounded-lg border border-red-200 bg-red-50 px-3.5 py-2.5 text-[12.5px] text-red-700 dark:border-red-500/25 dark:bg-red-500/10 dark:text-red-300">
              <ErrorRetry message={`加载技能历史失败: ${skillRunsError.message}`} onRetry={() => void refetchSkillRuns()} />
            </div>
          ) : !skillRuns || skillRuns.length === 0 ? (
            <p className="text-neutral-400 dark:text-white/75 text-sm text-center py-8">还没有执行过 CCF-A 技能</p>
          ) : (
            <div className="space-y-2">
              {skillRuns.map((run) => (
                <div key={run.id} className="rounded-lg border border-hairline px-4 py-3 dark:border-white/[0.08]">
                  <div className="flex items-center gap-2">
                    <span className="font-mono text-[12px] text-ink dark:text-white">{run.stage.replace('skill_', '')}</span>
                    <span className="locator">{run.provider} · {run.model}</span>
                    <span className="locator ml-auto">{run.created_at ? new Date(run.created_at).toLocaleString() : ''}</span>
                    <button
                      className="btn-quiet !px-2 !py-1 text-[10px]"
                      onClick={() => downloadJSON(run, `skill-${run.stage.replace('skill_', '')}-${run.id.slice(0, 8)}.json`)}
                    >
                      导出 JSON
                    </button>
                    <button
                      className="btn-quiet !px-2 !py-1 text-[10px]"
                      onClick={() => downloadMarkdown(run, `skill-${run.stage.replace('skill_', '')}-${run.id.slice(0, 8)}.md`)}
                    >
                      导出 MD
                    </button>
                  </div>
                  <div className="mt-1 flex items-center gap-3 text-[11px] text-neutral-400 dark:text-white/80">
                    <span>延迟 {run.latency_ms}ms</span>
                    <span>输入 {run.input_tokens} tok</span>
                    <span>输出 {run.output_tokens} tok</span>
                  </div>
                  {run.output && Object.keys(run.output).length > 0 && <SkillResultView output={run.output} className="mt-3" />}
                </div>
              ))}
            </div>
          )}
        </div>
      </Reveal>

      {/* pipeline history */}
      <Reveal delay={135} className="mt-12">
        <SectionHead kicker="Pipeline History" title="CCF-A 技能流水线历史" />
        <div className="card p-7">
          {pipelineRunsLoading ? (
            <div className="flex items-center justify-center py-8">
              <Spinner className="size-5 text-neutral-300" />
            </div>
          ) : pipelineRunsError ? (
            <div className="rounded-lg border border-red-200 bg-red-50 px-3.5 py-2.5 text-[12.5px] text-red-700 dark:border-red-500/25 dark:bg-red-500/10 dark:text-red-300">
              <ErrorRetry message={`加载流水线历史失败: ${pipelineRunsError.message}`} onRetry={() => void refetchPipelineRuns()} />
            </div>
          ) : !pipelineRuns || pipelineRuns.length === 0 ? (
            <p className="text-neutral-400 dark:text-white/75 text-sm text-center py-8">还没有运行过技能流水线</p>
          ) : (
            <div className="space-y-2">
              {pipelineRuns.map((run) => (
                <div key={run.id} className="rounded-lg border border-hairline px-4 py-3 dark:border-white/[0.08]">
                  <div className="flex items-center gap-2">
                    <span className="font-mono text-[12px] text-ink dark:text-white">{run.payload.pipeline ?? 'custom'}</span>
                    <span className="locator ml-auto">{run.created_at ? new Date(run.created_at).toLocaleString() : ''}</span>
                  </div>
                  <div className="mt-1 text-[11px] text-neutral-400 dark:text-white/80">
                    {(run.payload.skills ?? []).join(' → ')}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </Reveal>

      {/* audit log — lab journal */}
      <Reveal delay={140} className="mt-12">
        <SectionHead kicker="Lab Log" title="审计记录与模型运行" />
        <div className="card p-7">
          {auditLoading ? (
            <div className="flex items-center justify-center py-8">
              <Spinner className="size-5 text-neutral-300" />
            </div>
          ) : auditError ? (
            <div className="rounded-lg border border-red-200 bg-red-50 px-3.5 py-2.5 text-[12.5px] text-red-700 dark:border-red-500/25 dark:bg-red-500/10 dark:text-red-300">
              <ErrorRetry message={`加载审计记录失败: ${auditError.message}`} onRetry={() => void refetchAudit()} />
            </div>
          ) : !audit || audit.length === 0 ? (
            <p className="text-neutral-400 dark:text-white/75 text-sm text-center py-8">暂无审计记录</p>
          ) : (
            <ol className="timeline">
              {audit.map((a, i) => (
                <li key={a.stage} className={`timeline-item group ${a.result === 'pass' ? 'pass' : a.result === 'warn' ? 'warn' : 'fail'}`}>
                  <div className="flex items-baseline gap-3 font-mono text-[12.5px] min-w-0">
                    <span className="text-neutral-300 dark:text-white/65 shrink-0">{String(i + 1).padStart(2, '0')}</span>
                    <span className="text-ink dark:text-white shrink-0">{a.stage}</span>
                    <span className="flex-1 border-b border-dotted border-neutral-200 dark:border-white/15 -translate-y-[3px] min-w-6" />
                    <span className="tabular text-neutral-400 dark:text-white/80 shrink-0">{a.duration}</span>
                    <span className="tabular text-neutral-500 dark:text-white/85 shrink-0 w-14 text-right">{a.cost}</span>
                  </div>
                  <div className="mt-1 flex items-center gap-2 pl-9">
                    <span className="locator">
                      {a.provider} · {a.model}
                    </span>
                    {a.result === 'pass' && <span className="badge-green badge-dot">验证通过</span>}
                    {a.result === 'warn' && <span className="badge-orange badge-dot">验证警告</span>}
                    {a.result === 'fail' && <span className="badge-red badge-dot">验证失败</span>}
                  </div>
                </li>
              ))}
            </ol>
          )}
        </div>
      </Reveal>
    </div>
  );
}
