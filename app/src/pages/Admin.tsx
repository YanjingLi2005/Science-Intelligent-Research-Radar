import { useState, type FormEvent } from 'react';
import { Download, FileText, ShieldCheck } from 'lucide-react';
import {
  createAdminUser,
  downloadComplianceReport,
  getAdminUsers,
  getCases,
  setUserRole,
  type AdminUser,
  type CaseSummary,
  useApi,
} from '../api';
import { Spinner } from '../components/ui/spinner';
import ErrorRetry from '../components/ErrorRetry';
import { toast } from 'sonner';

export default function Admin() {
  const { data, loading, error, refetch } = useApi<AdminUser[]>(getAdminUsers, []);
  const { data: cases, loading: casesLoading } = useApi<CaseSummary[]>(getCases, []);
  const [saving, setSaving] = useState<string | null>(null);
  const [message, setMessage] = useState('');
  const [newUsername, setNewUsername] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [creating, setCreating] = useState(false);
  const [createMessage, setCreateMessage] = useState('');
  const [selectedCaseId, setSelectedCaseId] = useState<string>('');
  const [exportingReport, setExportingReport] = useState<string | null>(null);

  async function changeRole(user: AdminUser) {
    const nextRole = user.role === 'admin' ? 'user' : 'admin';
    setSaving(user.id);
    setMessage('');
    try {
      await setUserRole(user.id, nextRole);
      await refetch();
    } catch (err) {
      setMessage(err instanceof Error ? err.message : '角色更新失败');
    } finally {
      setSaving(null);
    }
  }

  async function handleCreateUser(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!newUsername.trim() || !newPassword) return;
    setCreating(true);
    setCreateMessage('');
    try {
      await createAdminUser(newUsername.trim(), newPassword);
      setNewUsername('');
      setNewPassword('');
      await refetch();
      setCreateMessage('用户已创建');
    } catch (err) {
      setCreateMessage(err instanceof Error ? err.message : '创建用户失败');
    } finally {
      setCreating(false);
    }
  }

  async function handleDownloadReport(caseId: string, format: 'markdown' | 'html') {
    if (!caseId) {
      toast.error('请先选择一个研究课题');
      return;
    }
    setExportingReport(format);
    try {
      await downloadComplianceReport(caseId, format);
      toast.success(`科研合规审计报告 (${format.toUpperCase()}) 已导出`);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '导出报告失败');
    } finally {
      setExportingReport(null);
    }
  }

  if (loading && !data) {
    return <div aria-busy="true" className="flex items-center justify-center py-20"><Spinner className="size-8 text-neutral-400" /></div>;
  }
  if (error || !data) {
    return <div className="card p-8 text-center text-sm text-red-600 dark:text-red-300"><ErrorRetry message={`用户列表加载失败：${error?.message ?? '未知错误'}`} onRetry={() => void refetch()} /></div>;
  }

  const effectiveCaseId = selectedCaseId || (cases && cases[0] ? cases[0].id : '');

  return (
    <div className="space-y-6">
      <header className="border-b border-black/10 pb-6 dark:border-white/10">
        <div className="kicker">Administration</div>
        <h1 className="mt-2 text-[clamp(2rem,3vw,2.75rem)] font-semibold tracking-[-0.04em]">管理后台与合规审计中心</h1>
        <p className="mt-2 text-sm text-neutral-500 dark:text-white/70">
          管理账号角色，并集中生成课题级《科研诚信与合规审计报告》。
        </p>
      </header>

      {/* Compliance Audit Center Card */}
      <div className="card p-6">
        <div className="flex items-center gap-2 mb-2 text-ink dark:text-white font-semibold text-sm">
          <ShieldCheck size={16} className="text-teal" /> 科研合规审计与报告中心
        </div>
        <p className="text-xs text-neutral-500 dark:text-white/70 mb-4 leading-relaxed">
          一键导出完整《科研诚信与引用核查报告》，包含 CrossRef DOI 真实性筛查、撤稿清单与审稿人对抗防御自评。
        </p>
        <div className="flex flex-wrap items-center gap-3">
          <select
            value={effectiveCaseId}
            onChange={(e) => setSelectedCaseId(e.target.value)}
            className="input !w-72 text-xs"
            disabled={casesLoading || !cases || cases.length === 0}
          >
            {cases && cases.length > 0 ? (
              cases.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name || c.id}
                </option>
              ))
            ) : (
              <option value="">暂无可用课题</option>
            )}
          </select>
          <button
            className="btn-ghost text-xs flex items-center gap-1.5"
            disabled={!effectiveCaseId || Boolean(exportingReport)}
            onClick={() => void handleDownloadReport(effectiveCaseId, 'markdown')}
          >
            <FileText size={13} />
            {exportingReport === 'markdown' ? '导出中…' : '导出合规报告 (Markdown)'}
          </button>
          <button
            className="btn-primary text-xs flex items-center gap-1.5"
            disabled={!effectiveCaseId || Boolean(exportingReport)}
            onClick={() => void handleDownloadReport(effectiveCaseId, 'html')}
          >
            <Download size={13} />
            {exportingReport === 'html' ? '导出中…' : '导出合规报告 (HTML)'}
          </button>
        </div>
      </div>

      <form onSubmit={(event) => void handleCreateUser(event)} className="card p-5">
        <div className="kicker mb-3">添加用户（公开注册关闭时使用）</div>
        <div className="grid gap-3 md:grid-cols-[1fr_1fr_auto]">
          <input
            value={newUsername}
            onChange={(event) => setNewUsername(event.target.value)}
            placeholder="用户名（2-32 位）"
            className="input"
            disabled={creating}
          />
          <input
            type="password"
            value={newPassword}
            onChange={(event) => setNewPassword(event.target.value)}
            placeholder="密码（至少 8 位）"
            className="input"
            disabled={creating}
          />
          <button
            type="submit"
            className="btn-primary"
            disabled={creating || !newUsername.trim() || !newPassword}
          >
            {creating ? '创建中…' : '创建用户'}
          </button>
        </div>
        {createMessage && (
          <p className={`mt-3 text-[12.5px] ${createMessage === '用户已创建' ? 'text-teal' : 'text-red-600 dark:text-red-300'}`}>
            {createMessage}
          </p>
        )}
      </form>

      {message && <div role="alert" className="rounded-lg bg-red-50 px-4 py-3 text-sm text-red-700 dark:bg-red-500/10 dark:text-red-300">{message}</div>}
      <div className="card divide-y divide-neutral-100 dark:divide-white/[0.08]">
        {data.map((user) => (
          <div key={user.id} className="flex flex-wrap items-center justify-between gap-4 p-4">
            <div className="min-w-0">
              <div className="font-medium text-neutral-900 dark:text-white">{user.username}</div>
              <div className="mt-1 text-xs text-neutral-400 dark:text-white/60">注册于 {new Date(user.created_at).toLocaleString()}</div>
            </div>
            <div className="flex items-center gap-3">
              <span className="rounded-full bg-neutral-100 px-2 py-1 text-[10px] uppercase tracking-wide text-neutral-600 dark:bg-white/10 dark:text-white/75">{user.role}</span>
              <button className="btn-secondary" disabled={saving === user.id} onClick={() => void changeRole(user)}>
                {saving === user.id ? '保存中…' : user.role === 'admin' ? '降为普通用户' : '设为管理员'}
              </button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
