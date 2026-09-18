import { useEffect, useState, type ElementType } from 'react';
import {
  Activity, ChevronDown, ChevronRight, FlaskConical, FolderKanban,
  LayoutDashboard, LogOut, Plus, Radar, Search, Settings, Shield,
  X,
} from 'lucide-react';
import type { CaseSummary } from '../../api';

interface SidebarNavProps {
  cases: CaseSummary[];
  activeCaseId?: string;
  activePath: string;
  username: string | null;
  role: 'admin' | 'user' | null;
  collapsed?: boolean;
  onHome: () => void;
  onNew: () => void;
  onSearch: () => void;
  onProjectSelect: (id: string) => void;
  onAdmin: () => void;
  onLab: () => void;
  onMetrics: () => void;
  onSettings: () => void;
  onLogout: () => void;
}

function NavItem({ icon: Icon, title, active = false, collapsed = false, badge, onClick }: {
  icon: ElementType<{ className?: string; strokeWidth?: number }>;
  title: string;
  active?: boolean;
  collapsed?: boolean;
  badge?: string | number;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={collapsed ? title : undefined}
      aria-current={active ? 'page' : undefined}
      className={`group flex w-full items-center gap-2.5 rounded-[12px] px-2.5 py-2 text-left text-[13px] transition-colors ${collapsed ? 'justify-center' : ''} ${
        active
          ? 'bg-black/[0.055] font-medium text-ink dark:bg-white/10 dark:text-white'
          : 'text-neutral-500 hover:bg-black/[0.04] hover:text-ink dark:text-white/55 dark:hover:bg-white/[0.055] dark:hover:text-white'
      }`}
    >
      <Icon className="size-4 shrink-0" strokeWidth={1.7} />
      {!collapsed && <span className="min-w-0 flex-1 truncate">{title}</span>}
      {!collapsed && badge !== undefined && (
        <span className="rounded-full bg-black/5 px-2 py-0.5 font-mono text-[10px] text-neutral-500 dark:bg-white/10 dark:text-white/55">{badge}</span>
      )}
    </button>
  );
}

export function DashboardSidebar({
  cases, activeCaseId, activePath, username, role, collapsed = false,
  onHome, onNew, onSearch, onProjectSelect, onAdmin, onLab, onMetrics,
  onSettings, onLogout,
}: SidebarNavProps) {
  const [workspaceOpen, setWorkspaceOpen] = useState(false);
  const [projectsOpen, setProjectsOpen] = useState(true);
  const selected = cases.find((item) => item.id === activeCaseId);

  return (
    <div className={`flex h-full min-h-0 w-full flex-col border-r border-black/10 bg-white/90 p-3 text-ink dark:border-white/10 dark:bg-[#101012] dark:text-white ${collapsed ? 'items-center' : ''}`}>
      <div className="relative w-full">
        <button
          type="button"
          onClick={() => setWorkspaceOpen((open) => !open)}
          aria-expanded={workspaceOpen}
          aria-label={selected ? `当前监控：${selected.name}` : 'Research Radar 工作区'}
          title={collapsed ? (selected?.name ?? 'Research Radar') : undefined}
          className={`group flex w-full items-center rounded-[12px] px-2 py-2 text-left transition-colors hover:bg-black/[0.04] dark:hover:bg-white/[0.055] ${collapsed ? 'justify-center' : 'gap-3'}`}
        >
          <span className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-[#18181B] text-white dark:border dark:border-white/15 dark:bg-white/10">
            <Radar className="size-[17px]" strokeWidth={1.8} aria-hidden="true" />
          </span>
          {!collapsed && <>
            <span className="flex min-w-0 flex-1 flex-col gap-1">
              <span className="truncate text-[13px] font-semibold leading-none">{selected?.name ?? 'Research Radar'}</span>
              <span className="truncate text-[10px] uppercase tracking-[0.13em] text-neutral-400 dark:text-white/40">{selected ? '当前监控对象' : 'Science intelligence'}</span>
            </span>
            <ChevronDown className={`size-4 shrink-0 text-neutral-400 transition-transform dark:text-white/40 ${workspaceOpen ? 'rotate-180' : ''}`} strokeWidth={1.6} aria-hidden="true" />
          </>}
        </button>
        {workspaceOpen && <>
          <button type="button" aria-label="关闭工作区菜单" className="fixed inset-0 z-40 cursor-default" onClick={() => setWorkspaceOpen(false)} />
          <div className={`absolute top-full z-50 mt-1 max-h-72 overflow-y-auto rounded-xl border border-black/10 bg-white p-1 shadow-xl dark:border-white/10 dark:bg-[#1B1B1F] ${collapsed ? 'left-full ml-2 w-60' : 'left-0 w-full'}`}>
            <button type="button" onClick={() => { onHome(); setWorkspaceOpen(false); }} className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-[12px] text-neutral-600 hover:bg-black/5 dark:text-white/70 dark:hover:bg-white/10">
              <LayoutDashboard className="size-4" strokeWidth={1.7} /> 全部监控
            </button>
            {cases.map((item) => (
              <button key={item.id} type="button" onClick={() => { onProjectSelect(item.id); setWorkspaceOpen(false); }} className={`w-full truncate rounded-[12px] px-3 py-2 text-left text-[12px] transition-colors ${item.id === activeCaseId ? 'bg-black/5 font-medium dark:bg-white/10' : 'hover:bg-black/5 dark:hover:bg-white/10'}`}>
                {item.name}
              </button>
            ))}
            <div className="my-1 border-t border-black/10 dark:border-white/10" />
            <button type="button" onClick={() => { onNew(); setWorkspaceOpen(false); }} className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-[12px] text-neutral-600 hover:bg-black/5 dark:text-white/70 dark:hover:bg-white/10">
              <Plus className="size-4" strokeWidth={1.7} /> 新建监控
            </button>
          </div>
        </>}
      </div>

      <div className="mt-5 flex min-h-0 w-full flex-1 flex-col overflow-y-auto [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
        <div className="space-y-0.5">
          <NavItem icon={Search} title="搜索" collapsed={collapsed} onClick={onSearch} />
          <NavItem icon={LayoutDashboard} title="监控台" active={activePath === '/cases'} collapsed={collapsed} onClick={onHome} />
          <NavItem icon={Plus} title="新建监控" collapsed={collapsed} onClick={onNew} />
        </div>

        {!collapsed && <div className="mb-1 mt-6 px-2.5 text-[10px] font-semibold uppercase tracking-[0.16em] text-neutral-400 dark:text-white/35">工作区</div>}
        {collapsed && <div className="my-4 w-7 border-t border-black/10 dark:border-white/10" />}
        <div className="space-y-0.5">
          {role === 'admin' && <NavItem icon={Shield} title="用户管理" active={activePath === '/admin'} collapsed={collapsed} onClick={onAdmin} />}
          <NavItem icon={FlaskConical} title="课题组矩阵" active={activePath === '/lab/claim-matrix'} collapsed={collapsed} onClick={onLab} />
          {role === 'admin' && <NavItem icon={Activity} title="数据看板" active={activePath === '/metrics'} collapsed={collapsed} onClick={onMetrics} />}
        </div>

        {!collapsed && <div className="mb-1 mt-6 px-2.5 text-[10px] font-semibold uppercase tracking-[0.16em] text-neutral-400 dark:text-white/35">监控对象</div>}
        {collapsed && <div className="my-4 w-7 border-t border-black/10 dark:border-white/10" />}
        <button
          type="button"
          onClick={() => setProjectsOpen((open) => !open)}
          aria-expanded={projectsOpen}
          title={collapsed ? '监控对象' : undefined}
          className={`flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left text-[13px] text-neutral-500 transition-colors hover:bg-black/[0.04] hover:text-ink dark:text-white/55 dark:hover:bg-white/[0.055] dark:hover:text-white ${collapsed ? 'justify-center' : ''}`}
        >
          <FolderKanban className="size-4 shrink-0" strokeWidth={1.7} />
          {!collapsed && <><span className="min-w-0 flex-1">我的监控</span><span className="font-mono text-[10px]">{cases.length}</span><ChevronRight className={`size-3.5 transition-transform ${projectsOpen ? 'rotate-90' : ''}`} /></>}
        </button>
        {projectsOpen && <div className={`${collapsed ? 'mt-1 space-y-1' : 'ml-[18px] mt-1 space-y-0.5 border-l border-black/10 pl-2 dark:border-white/10'}`}>
          {cases.length === 0 && !collapsed && <p className="px-2.5 py-3 text-[11px] text-neutral-400 dark:text-white/40">还没有监控对象</p>}
          {cases.map((item) => (
            <button
              key={item.id}
              type="button"
              onClick={() => onProjectSelect(item.id)}
              title={collapsed ? item.name : undefined}
              aria-current={item.id === activeCaseId ? 'page' : undefined}
              className={`flex w-full items-center gap-2 rounded-[12px] px-2.5 py-2 text-left text-[12px] transition-colors ${collapsed ? 'justify-center' : ''} ${item.id === activeCaseId ? 'bg-black/[0.055] font-medium text-ink dark:bg-white/10 dark:text-white' : 'text-neutral-500 hover:bg-black/[0.04] hover:text-ink dark:text-white/55 dark:hover:bg-white/[0.055] dark:hover:text-white'}`}
            >
              <span className={`size-1.5 shrink-0 rounded-full ${item.urgent > 0 ? 'bg-red-500' : 'bg-neutral-400 dark:bg-white/40'}`} aria-hidden="true" />
              {!collapsed && <span className="min-w-0 flex-1 truncate">{item.name}</span>}
              {!collapsed && <span className="font-mono text-[10px] text-neutral-400 dark:text-white/35">{item.version}</span>}
            </button>
          ))}
        </div>}
      </div>

      <div className="w-full border-t border-black/10 pt-3 dark:border-white/10">
        {!collapsed && <div className="mb-2 flex items-center gap-2 px-2.5 py-1.5">
          <span className="flex size-7 shrink-0 items-center justify-center rounded-full bg-black/5 text-[11px] font-semibold dark:bg-white/10">{username?.[0]?.toUpperCase() ?? '?'}</span>
          <span className="min-w-0 flex-1 truncate text-[12px] font-medium">{username}</span>
          {role && <span className="rounded-full bg-black/5 px-1.5 py-0.5 text-[9px] uppercase text-neutral-500 dark:bg-white/10 dark:text-white/55">{role}</span>}
        </div>}
        <NavItem icon={Settings} title="设置" active={activePath === '/settings'} collapsed={collapsed} onClick={onSettings} />
        <NavItem icon={LogOut} title="退出登录" collapsed={collapsed} onClick={onLogout} />
      </div>
    </div>
  );
}

export function SidebarSearchDialog({ open, cases, onClose, onProjectSelect, onNew }: {
  open: boolean;
  cases: CaseSummary[];
  onClose: () => void;
  onProjectSelect: (id: string) => void;
  onNew: () => void;
}) {
  const [query, setQuery] = useState('');
  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onClose]);
  if (!open) return null;
  const q = query.trim().toLowerCase();
  const results = q
    ? cases.filter((item) => `${item.name} ${item.question} ${item.file} ${item.topics.join(' ')}`.toLowerCase().includes(q))
    : cases;
  return (
    <div className="fixed inset-0 z-[80] flex justify-center bg-black/35 px-4 pt-[12vh] backdrop-blur-sm dark:bg-black/60" role="presentation">
      <button type="button" aria-label="关闭搜索" className="absolute inset-0 cursor-default" onClick={onClose} />
      <div role="dialog" aria-modal="true" aria-label="搜索监控对象" className="relative h-fit w-full max-w-xl overflow-hidden rounded-2xl border border-black/10 bg-white shadow-2xl dark:border-white/15 dark:bg-[#1B1B1F]">
        <div className="flex items-center gap-3 border-b border-black/10 px-4 dark:border-white/10">
          <Search className="size-[18px] shrink-0 text-neutral-400 dark:text-white/45" strokeWidth={1.7} aria-hidden="true" />
          <input autoFocus value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索监控对象…" className="min-w-0 flex-1 bg-transparent py-4 text-[14px] text-ink outline-none placeholder:text-neutral-400 dark:text-white dark:placeholder:text-white/40" />
          <button type="button" onClick={onClose} aria-label="关闭搜索" className="rounded-md p-1 text-neutral-400 hover:bg-black/5 hover:text-ink dark:text-white/45 dark:hover:bg-white/10 dark:hover:text-white"><X className="size-4" /></button>
        </div>
        <div className="max-h-[50vh] overflow-y-auto p-2">
          {results.length === 0 && <p className="px-3 py-8 text-center text-[12px] text-neutral-500 dark:text-white/50">没有匹配的监控对象</p>}
          {results.map((item) => <button key={item.id} type="button" onClick={() => { onProjectSelect(item.id); onClose(); setQuery(''); }} className="flex w-full items-center gap-3 rounded-lg px-3 py-3 text-left text-[13px] hover:bg-black/5 dark:hover:bg-white/10"><FolderKanban className="size-4 shrink-0 text-neutral-400 dark:text-white/45" /><span className="min-w-0 flex-1 truncate">{item.name}</span><span className="font-mono text-[10px] text-neutral-400 dark:text-white/40">{item.version}</span></button>)}
          <button type="button" onClick={() => { onNew(); onClose(); setQuery(''); }} className="mt-1 flex w-full items-center gap-3 rounded-lg border-t border-black/10 px-3 py-3 text-left text-[13px] text-neutral-500 hover:bg-black/5 dark:border-white/10 dark:text-white/60 dark:hover:bg-white/10"><Plus className="size-4" /> 新建监控</button>
        </div>
      </div>
    </div>
  );
}
