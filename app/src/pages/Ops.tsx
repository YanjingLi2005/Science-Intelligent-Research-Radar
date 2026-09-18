import { useEffect, useState } from 'react';
import { Bell, Check, GitBranch, Mail, RefreshCw, Save, Send, Webhook } from 'lucide-react';
import {
  getCostAlert,
  getDigestWebhook,
  getEmailNotify,
  getNotificationHistory,
  getScanWebhook,
  setCostAlert,
  setDigestWebhook,
  setEmailNotify,
  setScanWebhook,
  testCostAlert,
  testDigestWebhook,
  testEmailNotify,
  testScanWebhook,
  getGitSync,
  setGitSync,
  type GitSyncConfig,
  type CostAlertConfig,
  type DigestWebhookConfig,
  type EmailNotifyConfig,
  type NotificationHistoryItem,
  type ScanWebhookConfig,
  useApi,
} from '../api';
import { useProject } from '../contexts/ProjectContext';
import { SectionHead } from '../components/chrome';
import { Spinner } from '../components/ui/spinner';

type ActionState = 'idle' | 'saving' | 'testing' | 'saved' | 'tested' | 'error';

function messageFor(error: unknown) {
  return error instanceof Error ? error.message : String(error);
}

function Status({ state, error }: { state: ActionState; error: string | null }) {
  if (state === 'saving') return <span className="badge badge-blue badge-dot">保存中</span>;
  if (state === 'testing') return <span className="badge badge-blue badge-dot">测试中</span>;
  if (state === 'saved') return <span className="badge badge-green badge-dot"><Check size={11} /> 已保存</span>;
  if (state === 'tested') return <span className="badge badge-green badge-dot"><Check size={11} /> 测试已发送</span>;
  if (state === 'error') return <span className="badge badge-red badge-dot">{error || '操作失败'}</span>;
  return null;
}

function Card({
  title,
  description,
  icon,
  status,
  error,
  children,
}: {
  title: string;
  description: string;
  icon: React.ReactNode;
  status: ActionState;
  error: string | null;
  children: React.ReactNode;
}) {
  return (
    <section className="card p-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex items-start gap-3">
          <span className="mt-0.5 rounded-lg bg-neutral-100 p-2 text-neutral-600 dark:bg-white/[0.07] dark:text-white/80">{icon}</span>
          <div>
            <h2 className="text-[14px] font-semibold">{title}</h2>
            <p className="mt-1 text-[12px] leading-relaxed text-neutral-500 dark:text-white/70">{description}</p>
          </div>
        </div>
        <Status state={status} error={error} />
      </div>
      <div className="mt-5">{children}</div>
    </section>
  );
}

function Toggle({ enabled, onChange }: { enabled: boolean; onChange: () => void }) {
  return <button type="button" className={enabled ? 'badge badge-green badge-dot' : 'badge badge-gray badge-dot'} onClick={onChange}>{enabled ? '已开启' : '已关闭'}</button>;
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return <label className="block"><span className="mb-1.5 block text-[11px] font-medium text-neutral-500 dark:text-white/70">{label}</span>{children}</label>;
}

export default function Ops() {
  const { project, loading: projectLoading, error: projectError } = useProject();
  const caseId = project?.id ?? '';
  const digest = useApi(() => getDigestWebhook(caseId), [caseId]);
  const scan = useApi(() => getScanWebhook(caseId), [caseId]);
  const email = useApi(() => getEmailNotify(caseId), [caseId]);
  const cost = useApi(() => getCostAlert(caseId), [caseId]);
  const history = useApi(() => getNotificationHistory(caseId), [caseId]);
  const git = useApi(() => getGitSync(caseId), [caseId]);
  const [digestForm, setDigestForm] = useState<DigestWebhookConfig | null>(null);
  const [scanForm, setScanForm] = useState<ScanWebhookConfig | null>(null);
  const [emailForm, setEmailForm] = useState<EmailNotifyConfig | null>(null);
  const [costForm, setCostForm] = useState<CostAlertConfig | null>(null);
  const [gitForm, setGitForm] = useState<GitSyncConfig | null>(null);
  const [states, setStates] = useState<Record<string, ActionState>>({});
  const [errors, setErrors] = useState<Record<string, string | null>>({});

  useEffect(() => { if (digest.data) setDigestForm(digest.data); }, [digest.data]);
  useEffect(() => { if (scan.data) setScanForm(scan.data); }, [scan.data]);
  useEffect(() => { if (email.data) setEmailForm(email.data); }, [email.data]);
  useEffect(() => { if (cost.data) setCostForm(cost.data); }, [cost.data]);
  useEffect(() => { if (git.data) setGitForm(git.data); }, [git.data]);

  const run = async (key: string, action: () => Promise<void>, success: ActionState) => {
    setStates((current) => ({ ...current, [key]: key.endsWith('Test') ? 'testing' : 'saving' }));
    setErrors((current) => ({ ...current, [key]: null }));
    try { await action(); setStates((current) => ({ ...current, [key]: success })); }
    catch (error) { setStates((current) => ({ ...current, [key]: 'error' })); setErrors((current) => ({ ...current, [key]: messageFor(error) })); }
  };
  const save = (key: string, action: () => Promise<void>) => void run(key, action, 'saved');
  const test = (key: string, action: () => Promise<void>) => void run(`${key}Test`, action, 'tested');
  const status = (key: string) => states[key] ?? 'idle';
  const statusError = (key: string) => errors[key] ?? null;

  if (projectLoading && !project) return <div className="flex justify-center py-20"><Spinner className="size-7 text-neutral-400" /></div>;
  if (projectError || !project) return <div className="card p-8 text-center text-sm text-red-600 dark:text-red-300">运维页面加载失败：{projectError?.message ?? '未找到项目'}</div>;

  const apiError = (result: { error: Error | null; data: unknown }) => !result.data && result.error ? <p className="mt-3 text-[12px] text-red-600 dark:text-red-300">加载失败：{result.error.message}</p> : null;
  const allHistory = history.data ?? [];

  return (
    <div className="space-y-6">
      <SectionHead kicker="Operations · 项目级通知" title="运维" right={<button className="btn-quiet" aria-label="刷新运维数据" onClick={() => { digest.refetch(); scan.refetch(); email.refetch(); cost.refetch(); git.refetch(); history.refetch(); }}><RefreshCw size={14} /> 刷新</button>} />
      <p className="-mt-3 text-sm leading-relaxed text-neutral-500 dark:text-white/75">管理这个项目的通知出口、成本保护和发送记录。保存后可立即发送测试消息。</p>

      <Card title="Overleaf / Git 远端" description="保存论文仓库的远端地址和分支，供 Git 补丁导出后的人工应用流程参考。" icon={<GitBranch size={16} />} status={status('git')} error={statusError('git')}>
        {git.loading && !gitForm ? <Spinner /> : gitForm ? <div className="space-y-4">
          <div className="flex items-center justify-between"><span className="text-[12px] text-neutral-600 dark:text-white/80">启用远端配置</span><Toggle enabled={gitForm.enabled} onChange={() => setGitForm({ ...gitForm, enabled: !gitForm.enabled })} /></div>
          <Field label="Remote URL"><input className="input" value={gitForm.remote_url} onChange={(e) => setGitForm({ ...gitForm, remote_url: e.target.value })} placeholder="https://github.com/org/paper.git 或 Overleaf Git URL" /></Field>
          <Field label="Branch"><input className="input" value={gitForm.branch} onChange={(e) => setGitForm({ ...gitForm, branch: e.target.value })} placeholder="main" /></Field>
          <Actions saveLabel="保存 Git 远端配置" saving={status('git') === 'saving'} onSave={() => save('git', async () => setGitForm(await setGitSync(caseId, gitForm)))} />
        </div> : apiError(git)}
      </Card>

      <Card title="Digest webhook" description="按周期发送项目情报周报或摘要。" icon={<Webhook size={16} />} status={status('digest')} error={statusError('digest')}>
        {digest.loading && !digestForm ? <Spinner /> : digestForm ? <div className="space-y-4"><div className="flex items-center justify-between"><span className="text-[12px] text-neutral-600 dark:text-white/80">启用自动发送</span><Toggle enabled={digestForm.enabled} onChange={() => setDigestForm({ ...digestForm, enabled: !digestForm.enabled })} /></div><Field label="Webhook URL"><input className="input" value={digestForm.webhook_url} onChange={(e) => setDigestForm({ ...digestForm, webhook_url: e.target.value })} placeholder="https://example.com/digest" /></Field><Field label="发送周期"><select className="input" value={digestForm.schedule} onChange={(e) => setDigestForm({ ...digestForm, schedule: e.target.value })}><option value="daily">每天</option><option value="weekly">每周</option><option value="monthly">每月</option></select></Field><Actions saveLabel="保存 Digest" saving={status('digest') === 'saving'} testing={status('digestTest') === 'testing'} onSave={() => save('digest', async () => setDigestForm(await setDigestWebhook(caseId, digestForm)))} onTest={() => test('digest', async () => { await testDigestWebhook(caseId); })} testDisabled={!digestForm.enabled || !digestForm.webhook_url.trim()} /></div> : apiError(digest)}
      </Card>

      <Card title="Scan webhook" description="在扫描完成、失败或任意状态变化时通知外部系统。" icon={<Webhook size={16} />} status={status('scan')} error={statusError('scan')}>
        {scan.loading && !scanForm ? <Spinner /> : scanForm ? <div className="space-y-4"><div className="flex items-center justify-between"><span className="text-[12px] text-neutral-600 dark:text-white/80">启用扫描通知</span><Toggle enabled={scanForm.enabled} onChange={() => setScanForm({ ...scanForm, enabled: !scanForm.enabled })} /></div><Field label="Webhook URL"><input className="input" value={scanForm.webhook_url} onChange={(e) => setScanForm({ ...scanForm, webhook_url: e.target.value })} placeholder="https://example.com/scan" /></Field><Field label="通知条件"><select className="input" value={scanForm.notify_on} onChange={(e) => setScanForm({ ...scanForm, notify_on: e.target.value })}><option value="completed">完成时</option><option value="failed">失败时</option><option value="all">全部</option></select></Field><Actions saveLabel="保存 Scan" saving={status('scan') === 'saving'} testing={status('scanTest') === 'testing'} onSave={() => save('scan', async () => setScanForm(await setScanWebhook(caseId, scanForm)))} onTest={() => test('scan', async () => { await testScanWebhook(caseId); })} testDisabled={!scanForm.enabled || !scanForm.webhook_url.trim()} /></div> : apiError(scan)}
      </Card>

      <Card title="Email notify" description="使用 SMTP 向指定收件人发送项目通知。" icon={<Mail size={16} />} status={status('email')} error={statusError('email')}>
        {email.loading && !emailForm ? <Spinner /> : emailForm ? <div className="grid gap-4 sm:grid-cols-2"><div className="sm:col-span-2 flex items-center justify-between"><span className="text-[12px] text-neutral-600 dark:text-white/80">启用邮件通知</span><Toggle enabled={emailForm.enabled} onChange={() => setEmailForm({ ...emailForm, enabled: !emailForm.enabled })} /></div><Field label="SMTP 服务器"><input className="input" value={emailForm.smtp_host} onChange={(e) => setEmailForm({ ...emailForm, smtp_host: e.target.value })} placeholder="smtp.example.com" /></Field><Field label="端口"><input className="input" type="number" value={emailForm.smtp_port} onChange={(e) => setEmailForm({ ...emailForm, smtp_port: Number(e.target.value) || 587 })} /></Field><Field label="用户名"><input className="input" value={emailForm.username} onChange={(e) => setEmailForm({ ...emailForm, username: e.target.value })} /></Field><Field label="密码"><input className="input" type="password" value={emailForm.password === '***' ? '' : emailForm.password} placeholder={emailForm.password === '***' ? '已保存，留空保持不变' : 'SMTP 密码'} onChange={(e) => setEmailForm({ ...emailForm, password: e.target.value })} /></Field><Field label="收件人"><input className="input" value={emailForm.recipient} onChange={(e) => setEmailForm({ ...emailForm, recipient: e.target.value })} placeholder="you@example.com" /></Field><Field label="主题前缀"><input className="input" value={emailForm.subject_prefix} onChange={(e) => setEmailForm({ ...emailForm, subject_prefix: e.target.value })} /></Field><div className="sm:col-span-2"><Actions saveLabel="保存 Email" saving={status('email') === 'saving'} testing={status('emailTest') === 'testing'} onSave={() => save('email', async () => { const payload = { ...emailForm, password: emailForm.password === '***' ? undefined : emailForm.password }; setEmailForm(await setEmailNotify(caseId, payload)); })} onTest={() => test('email', async () => { await testEmailNotify(caseId); })} testDisabled={!emailForm.enabled} /></div></div> : apiError(email)}
      </Card>

      <Card title="Cost alert" description="超过月度预算时，通过 Webhook 发送成本告警。" icon={<Bell size={16} />} status={status('cost')} error={statusError('cost')}>
        {cost.loading && !costForm ? <Spinner /> : costForm ? <div className="space-y-4"><div className="flex items-center justify-between"><span className="text-[12px] text-neutral-600 dark:text-white/80">启用成本告警</span><Toggle enabled={costForm.enabled} onChange={() => setCostForm({ ...costForm, enabled: !costForm.enabled })} /></div><div className="grid gap-4 sm:grid-cols-2"><Field label="月度预算（USD）"><input className="input" type="number" min="0" step="0.01" value={costForm.monthly_budget_usd} onChange={(e) => setCostForm({ ...costForm, monthly_budget_usd: Number(e.target.value) || 0 })} /></Field><Field label="Webhook URL"><input className="input" value={costForm.webhook_url ?? ''} onChange={(e) => setCostForm({ ...costForm, webhook_url: e.target.value })} placeholder="https://example.com/cost" /></Field></div><Actions saveLabel="保存成本告警" saving={status('cost') === 'saving'} testing={status('costTest') === 'testing'} onSave={() => save('cost', async () => setCostForm(await setCostAlert(caseId, costForm)))} onTest={() => test('cost', async () => { await testCostAlert(caseId); })} testDisabled={!costForm.enabled || !costForm.webhook_url?.trim()} /></div> : apiError(cost)}
      </Card>

      <section className="card overflow-hidden">
        <div className="flex items-center justify-between gap-3 border-b border-hairline p-6 dark:border-white/[0.08]"><div><h2 className="text-[14px] font-semibold">通知历史</h2><p className="mt-1 text-[12px] text-neutral-500 dark:text-white/70">最近的通知发送与失败记录。</p></div><button className="btn-quiet !p-2" onClick={() => history.refetch()} aria-label="刷新通知历史"><RefreshCw size={14} /></button></div>
        {history.loading && !history.data ? <div className="flex justify-center p-8"><Spinner /></div> : history.error && !history.data ? <p className="p-6 text-[12px] text-red-600 dark:text-red-300">加载失败：{history.error.message}</p> : allHistory.length === 0 ? <p className="p-8 text-center text-sm text-neutral-400 dark:text-white/70">暂无通知记录</p> : <div className="divide-y divide-neutral-100 dark:divide-white/[0.06]">{allHistory.map((item) => <HistoryRow key={item.id} item={item} />)}</div>}
      </section>
    </div>
  );
}

function Actions({ saveLabel, saving, testing, onSave, onTest, testDisabled = false }: { saveLabel: string; saving: boolean; testing?: boolean; onSave: () => void; onTest?: () => void; testDisabled?: boolean }) {
  return <div className="flex flex-wrap items-center gap-2"><button className="btn-primary" type="button" disabled={saving} onClick={onSave}><Save size={14} /> {saving ? '保存中…' : saveLabel}</button>{onTest && <button className="btn-secondary" type="button" disabled={testing || testDisabled} onClick={onTest}><Send size={14} /> {testing ? '测试中…' : '测试发送'}</button>}</div>;
}

function HistoryRow({ item }: { item: NotificationHistoryItem }) {
  const failed = item.event_type.includes('failed');
  return <div className="flex flex-wrap items-center justify-between gap-3 px-6 py-4"><div className="flex items-center gap-3"><span className={failed ? 'badge badge-red badge-dot' : 'badge badge-green badge-dot'}>{failed ? '失败' : '已发送'}</span><span className="text-[12.5px] font-medium text-neutral-700 dark:text-white/90">{item.event_type}</span></div><span className="locator">{item.created_at ? new Date(item.created_at).toLocaleString('zh-CN', { hour12: false }) : '—'}</span></div>;
}
