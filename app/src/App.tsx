import { lazy, Suspense, useEffect, useRef, useState } from 'react';
import { ChevronDown, Menu, Moon, PanelLeftClose, PanelLeftOpen, Settings as SettingsIcon, Sun, Radar, TrendingUp, Shield } from 'lucide-react';
import { useTheme } from 'next-themes';
import { Toaster } from 'sonner';
import {
  Navigate,
  Outlet,
  Route,
  Routes,
  useLocation,
  useNavigate,
  useParams,
} from 'react-router';
import Login from './pages/Login';
import { ProjectProvider, useProject } from './contexts/ProjectContext';
import { AuthProvider, useAuth } from './contexts/AuthContext';
import HomeView from './pages/HomeView';
import { Sheet, SheetContent, SheetTitle } from './components/ui/sheet';
import OnboardingWizard from './components/OnboardingWizard';
import { Spinner } from './components/ui/spinner';
import { ErrorBoundary } from './components/ErrorBoundary';
import { DashboardSidebar, SidebarSearchDialog } from './components/ui/dashboard-sidebar';
import WorkspaceBackdrop from './components/ui/workspace-backdrop';
import { useLanguage } from './contexts/languageState';

const Dashboard = lazy(() => import('./pages/Dashboard'));
const IntegrityRadar = lazy(() => import('./pages/IntegrityRadar'));
const CitationCheck = lazy(() => import('./pages/CitationCheck'));
const Actions = lazy(() => import('./pages/Actions'));
const RadarPage = lazy(() => import('./pages/Radar'));
const DeepResearch = lazy(() => import('./pages/DeepResearch'));
const Improve = lazy(() => import('./pages/Improve'));
const Paper = lazy(() => import('./pages/Paper'));
const ClaimGraph = lazy(() => import('./pages/ClaimGraph'));
const Settings = lazy(() => import('./pages/Settings'));
const Metrics = lazy(() => import('./pages/Metrics'));
const Admin = lazy(() => import('./pages/Admin'));
const LiteratureQA = lazy(() => import('./pages/LiteratureQA'));
const Ops = lazy(() => import('./pages/Ops'));
const Digest = lazy(() => import('./pages/Digest'));
const LabMatrix = lazy(() => import('./pages/LabMatrix'));

export type PageKey = 'dashboard' | 'digest' | 'actions' | 'radar' | 'integrity' | 'citations' | 'qa' | 'deep-research' | 'improve' | 'paper' | 'graph' | 'ops' | 'settings' | 'admin' | 'lab';

function AdminRoute({ children }: { children: React.ReactNode }) {
  const { role } = useAuth();
  if (role !== 'admin') {
    return <Navigate to="/cases" replace />;
  }
  return <>{children}</>;
}

// Project-scoped nav items (pill tabs when a project is selected)

// Project-scoped nav items (pill tabs when a project is selected)
const PROJECT_NAV: { key: PageKey; label: string; path: string }[] = [
  { key: 'dashboard', label: '情报板', path: '' },
  { key: 'digest', label: '周报', path: 'digest' },
  { key: 'radar', label: '文献雷达', path: 'radar' },
  { key: 'integrity', label: '诚信雷达', path: 'integrity' },
  { key: 'citations', label: '引文核验', path: 'citations' },
  { key: 'qa', label: '文献问答', path: 'qa' },
  { key: 'deep-research', label: '深度调研', path: 'deep-research' },
  { key: 'actions', label: '行动队列', path: 'actions' },
  { key: 'improve', label: '改造工作台', path: 'improve' },
  { key: 'paper', label: '论文 Claim', path: 'paper' },
  { key: 'graph', label: '演化图谱', path: 'graph' },
  { key: 'ops', label: '运维', path: 'ops' },
];

const PRIMARY_KEYS: PageKey[] = ['dashboard', 'paper', 'radar', 'actions'];
const MORE_KEYS: PageKey[] = ['digest', 'integrity', 'qa', 'deep-research', 'improve', 'graph', 'ops', 'citations', 'lab'];

export default function AppWrapper() {

  return (
    <AuthProvider>
      <AppContent />
    </AuthProvider>
  );
}

function AppContent() {
  const { state } = useAuth();

  if (state === 'loading') {
    return (
      <div className="min-h-screen bg-night text-white flex items-center justify-center">
        <span className="font-mono text-[11px] tracking-[0.3em] uppercase text-white/90">Loading…</span>
      </div>
    );
  }
  if (state === 'anon') {
    return <Login />;
  }

  return (
    <ProjectProvider>
      <Suspense fallback={<PageLoadingFallback />}>
        <Routes>
          <Route path="/" element={<Navigate to="/cases" replace />} />
          {/* layout route: sidebar + top bar around project list, project pages and settings */}
          <Route element={<Shell />}>
            <Route path="/cases" element={<HomeView />} />
            <Route path="/settings" element={<Settings />} />
            <Route path="/metrics" element={<AdminRoute><Metrics /></AdminRoute>} />
            <Route path="/lab/claim-matrix" element={<LabMatrix />} />
            <Route path="/admin" element={<AdminRoute><Admin /></AdminRoute>} />
            <Route path="/cases/:caseId" element={<ProjectSync />}>
              <Route index element={<Dashboard />} />
              <Route path="digest" element={<Digest />} />
              <Route path="radar" element={<RadarPage />} />
              <Route path="integrity" element={<IntegrityRadar />} />
              <Route path="citations" element={<CitationCheck />} />
              <Route path="qa" element={<LiteratureQA />} />
              <Route path="deep-research" element={<DeepResearch />} />
              <Route path="actions" element={<Actions />} />
              <Route path="improve" element={<Improve />} />
              <Route path="paper" element={<Paper />} />
              <Route path="graph" element={<ClaimGraph />} />
              <Route path="ops" element={<Ops />} />
            </Route>
          </Route>
          <Route path="*" element={<Navigate to="/cases" replace />} />
        </Routes>
      </Suspense>
      <OnboardingWizard />
      <Toaster position="bottom-right" />
    </ProjectProvider>
  );
}

function PageLoadingFallback() {
  return (
    <div className="flex min-h-[240px] items-center justify-center text-neutral-400 dark:text-white/60" aria-busy="true" aria-live="polite">
      <Spinner className="size-6" />
      <span className="sr-only">Loading…</span>
    </div>
  );
}

/** Reads the caseId from the URL and keeps ProjectContext in sync. */
function ProjectSync() {
  const { caseId } = useParams<{ caseId: string }>();
  const navigate = useNavigate();
  const { projectId, switchProject } = useProject();

  useEffect(() => {
    if (caseId && caseId !== projectId) {
      void switchProject(caseId).catch(() => navigate('/cases', { replace: true }));
    }
  }, [caseId, projectId, switchProject, navigate]);

  return <Outlet />;
}

/** Codex-style chrome: watch-list sidebar + top bar + pill tabs. */
function Shell() {
  const { caseId } = useParams<{ caseId: string }>();
  const location = useLocation();
  const navigate = useNavigate();
  const { project, projectId, caseList, switchProject } = useProject();
  const { username, role, logout } = useAuth();
  const { resolvedTheme, setTheme } = useTheme();
  const { locale, toggleLanguage } = useLanguage();
  const [mobileOpen, setMobileOpen] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);
  const [moreOpen, setMoreOpen] = useState(false);
  const mainRef = useRef<HTMLElement>(null);

  useEffect(() => {
    mainRef.current?.focus();
  }, [location.pathname]);

  // Cmd/Ctrl+K opens the project search dialog.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        setSearchOpen(true);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  const hasProject = Boolean(caseId);
  const projectChip = caseList.find((c) => c.id === (caseId ?? projectId));

  const activeKey: PageKey | 'home' =
    location.pathname === '/settings'
      ? 'settings'
      : location.pathname === '/admin'
        ? 'admin'
      : location.pathname === '/lab/claim-matrix'
        ? 'lab'
      : location.pathname === `/cases/${caseId}`
        ? 'dashboard'
        : (PROJECT_NAV.find((n) => n.path && location.pathname.endsWith(`/${n.path}`))?.key ?? 'dashboard');
  const pageTitle = hasProject
    ? (PROJECT_NAV.find((item) => item.key === activeKey)?.label ?? '情报板')
    : location.pathname === '/settings' ? '设置'
      : location.pathname === '/admin' ? '用户管理'
        : location.pathname === '/metrics' ? '数据看板'
          : location.pathname === '/lab/claim-matrix' ? '课题组矩阵'
            : '监控台';

  const goProject = (page: PageKey) => {
    setMobileOpen(false);
    setMoreOpen(false);
    navigate(`/cases/${caseId}/${page === 'dashboard' ? '' : page}`);
  };
  const goHome = () => {
    setMobileOpen(false);
    navigate('/cases');
  };
  const goNew = () => {
    setMobileOpen(false);
    navigate('/cases?new=1');
  };
  const openSearch = () => {
    setMobileOpen(false);
    setSearchOpen(true);
  };
  const goSettings = () => {
    setMobileOpen(false);
    navigate('/settings');
  };
  const goMetrics = () => {
    setMobileOpen(false);
    navigate('/metrics');
  };
  const goAdmin = () => {
    setMobileOpen(false);
    navigate('/admin');
  };
  const goLabMatrix = () => {
    setMobileOpen(false);
    navigate('/lab/claim-matrix');
  };
  const openProject = (id: string) => {
    setMobileOpen(false);
    void switchProject(id).then(() => navigate(`/cases/${id}`));
  };
  const sidebarProps = {
    cases: caseList,
    activeCaseId: caseId,
    activePath: location.pathname,
    username,
    role,
    onHome: goHome,
    onNew: goNew,
    onSearch: openSearch,
    onProjectSelect: openProject,
    onAdmin: goAdmin,
    onLab: goLabMatrix,
    onMetrics: goMetrics,
    onSettings: goSettings,
    onLogout: () => { void logout(); },
  };

  return (
    <div className="relative isolate flex min-h-screen bg-paper text-ink dark:bg-night dark:text-white">
      <WorkspaceBackdrop />
      <a href="#main-content" className="sr-only z-50 rounded-md bg-white px-3 py-2 text-sm text-ink shadow focus:not-sr-only focus:fixed focus:left-4 focus:top-4">跳转到主要内容</a>
      {/* Workspace sidebar */}
      <aside className={`fixed inset-y-0 left-0 z-40 hidden transition-[width] duration-200 md:flex ${sidebarCollapsed ? 'w-[72px]' : 'w-[260px]'}`}>
        <DashboardSidebar {...sidebarProps} collapsed={sidebarCollapsed} />
      </aside>

      {/* mobile navigation drawer */}
      <Sheet open={mobileOpen} onOpenChange={setMobileOpen}>
        <SheetContent side="left" className="w-[280px] p-0 bg-white dark:bg-[#101012]">
          <SheetTitle className="sr-only">导航菜单</SheetTitle>
          <DashboardSidebar {...sidebarProps} />
        </SheetContent>
      </Sheet>
      <SidebarSearchDialog open={searchOpen} cases={caseList} onClose={() => setSearchOpen(false)} onProjectSelect={openProject} onNew={goNew} />

      {/* ============ main column ============ */}
      <div className={`workspace-main relative z-10 flex min-w-0 flex-1 flex-col transition-[padding] duration-200 ${sidebarCollapsed ? 'md:pl-[72px]' : 'md:pl-[260px]'}`}>
        {/* top bar */}
        <header className="sticky top-0 z-30 border-b border-hairline bg-white/85 backdrop-blur-xl dark:border-white/[0.08] dark:bg-[#101012]/90">
          <div className="flex h-[68px] items-center justify-between gap-3 px-4 md:px-8">
            <div className="flex min-w-0 items-center gap-2">
              <button
                type="button"
                onClick={() => setSidebarCollapsed((value) => !value)}
                className="hidden rounded-lg p-2 text-neutral-500 transition-colors hover:bg-black/5 hover:text-ink dark:text-white/55 dark:hover:bg-white/10 dark:hover:text-white md:inline-flex"
                aria-label={sidebarCollapsed ? '展开侧边栏' : '收起侧边栏'}
                title={sidebarCollapsed ? '展开侧边栏' : '收起侧边栏'}
              >
                {sidebarCollapsed ? <PanelLeftOpen size={18} strokeWidth={1.6} /> : <PanelLeftClose size={18} strokeWidth={1.6} />}
              </button>
              {/* mobile menu */}
              <button
                onClick={() => setMobileOpen(true)}
                className="btn-quiet !p-2 md:hidden"
                aria-label="打开导航菜单"
                title="打开导航菜单"
              >
                <Menu size={16} />
              </button>
              {/* mobile brand */}
              <button
                onClick={goHome}
                className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md bg-[#18181B] text-white md:hidden dark:bg-white dark:text-[#111113]"
                aria-label="返回监控台"
              >
                <Radar size={13} strokeWidth={2.2} />
              </button>
              <div className="min-w-0">
                <div className="hidden items-center gap-2 text-[10px] font-medium uppercase tracking-[0.17em] text-neutral-400 dark:text-white/40 sm:flex">
                  <span>Research Radar</span><span aria-hidden="true">/</span><span>Workspace</span>
                </div>
                <div className="mt-0.5 flex min-w-0 items-center gap-2">
                  <button onClick={goHome} className="truncate text-[14px] font-semibold tracking-tight text-ink transition-colors hover:text-neutral-600 dark:text-white dark:hover:text-white/70">
                    {pageTitle}
                  </button>
                  {hasProject && projectChip && <span className="hidden truncate border-l border-black/15 pl-2 text-[12px] text-neutral-500 dark:border-white/15 dark:text-white/50 sm:inline">{projectChip.name}</span>}
                </div>
              </div>
            </div>

            <div className="flex shrink-0 items-center gap-3">
              {hasProject && project && (
                <>
                  <span className="locator hidden sm:inline">上次扫描 {project.lastScan}</span>
                  {project.urgent > 0 && <span className="badge-red badge-dot">{project.urgent} 紧急</span>}
                </>
              )}
              {role === 'admin' && <button
                onClick={goMetrics}
                aria-label="数据看板"
                title="数据看板"
                className="btn-quiet !p-2 md:hidden"
              >
                <TrendingUp size={15} />
              </button>}
              {role === 'admin' && <button
                onClick={goAdmin}
                aria-label="用户管理"
                title="用户管理"
                className="btn-quiet !p-2 md:hidden"
              >
                <Shield size={15} />
              </button>}
              <button
                onClick={goSettings}
                aria-label="设置"
                title="设置"
                className="btn-quiet !p-2 md:hidden"
              >
                <SettingsIcon size={15} />
              </button>
              <button
                type="button"
                onClick={toggleLanguage}
                aria-label={locale === 'zh' ? 'Switch to English' : '切换到中文'}
                title={locale === 'zh' ? 'English' : '中文'}
                className="flex h-9 min-w-9 items-center justify-center rounded-full border border-black/10 px-2.5 text-[11px] font-semibold tracking-wide text-neutral-600 transition-colors hover:bg-black/5 dark:border-white/10 dark:text-white/70 dark:hover:bg-white/10"
              >
                {locale === 'zh' ? 'EN' : '中文'}
              </button>
              <button
                onClick={() => setTheme(resolvedTheme === 'dark' ? 'light' : 'dark')}
                aria-label={resolvedTheme === 'dark' ? '切换到亮色模式' : '切换到暗色模式'}
                className="flex size-9 items-center justify-center rounded-full border border-black/10 text-neutral-600 transition-colors hover:bg-black/5 dark:border-white/10 dark:text-white/70 dark:hover:bg-white/10"
              >
                {resolvedTheme === 'dark' ? <Sun size={15} /> : <Moon size={15} />}
              </button>
            </div>
          </div>

          {/* project pill tabs */}
          {hasProject && (
            <nav
              className="flex min-w-0 items-center gap-1 border-t border-hairline px-4 py-2 md:px-8 dark:border-white/[0.06]"
              aria-label="项目页面"
            >
              <div className="flex min-w-0 flex-1 items-center gap-1 overflow-x-auto">
                {PROJECT_NAV.filter((n) => PRIMARY_KEYS.includes(n.key)).map((n) => (
                  <button
                    key={n.key}
                    onClick={() => goProject(n.key)}
                    aria-current={activeKey === n.key ? 'page' : undefined}
                    className={`whitespace-nowrap rounded-lg px-3 py-1.5 text-[12.5px] font-medium transition-colors ${
                      activeKey === n.key
                        ? 'bg-[#18181B] text-white dark:bg-white/10 dark:text-white'
                        : 'text-neutral-500 hover:bg-neutral-100 hover:text-ink dark:text-white/65 dark:hover:bg-white/[0.06] dark:hover:text-white'
                    }`}
                  >
                    {n.label}
                  </button>
                ))}
              </div>
              <div className="relative shrink-0">
                <button
                  type="button"
                  onClick={() => setMoreOpen((open) => !open)}
                  aria-expanded={moreOpen}
                  aria-haspopup="menu"
                  className={`flex items-center gap-1 whitespace-nowrap rounded-lg px-3 py-1.5 text-[12.5px] font-medium transition-colors ${
                    MORE_KEYS.includes(activeKey)
                      ? 'bg-[#18181B] text-white dark:bg-white/10 dark:text-white'
                      : 'text-neutral-500 hover:bg-neutral-100 hover:text-ink dark:text-white/65 dark:hover:bg-white/[0.06] dark:hover:text-white'
                  }`}
                >
                  更多
                  <ChevronDown size={13} className={`transition-transform ${moreOpen ? 'rotate-180' : ''}`} />
                </button>
                {moreOpen && (
                  <div
                    role="menu"
                    className="absolute right-0 top-full z-50 mt-1 min-w-[140px] rounded-lg border border-hairline bg-white p-1 shadow-lg dark:border-white/[0.1] dark:bg-[#18181B]"
                  >
                    {PROJECT_NAV.filter((n) => MORE_KEYS.includes(n.key)).map((n) => (
                      <button
                        key={n.key}
                        type="button"
                        role="menuitem"
                        onClick={() => goProject(n.key)}
                        aria-current={activeKey === n.key ? 'page' : undefined}
                        className={`block w-full rounded-md px-3 py-2 text-left text-[12.5px] font-medium transition-colors ${
                          activeKey === n.key
                            ? 'bg-[#18181B] text-white dark:bg-white/10 dark:text-white'
                            : 'text-neutral-500 hover:bg-neutral-100 hover:text-ink dark:text-white/85 dark:hover:bg-white/[0.06] dark:hover:text-white'
                        }`}
                      >
                        {n.label}
                      </button>
                    ))}
                  </div>
                )}
              </div>
            </nav>
          )}
        </header>

        <main ref={mainRef} id="main-content" tabIndex={-1} aria-live="polite" className={`mx-auto w-full flex-1 px-4 py-8 outline-none md:px-8 ${location.pathname === '/cases' ? 'max-w-[1200px]' : 'max-w-[980px]'}`}>
          <ErrorBoundary key={location.pathname} mode="page">
            <Outlet />
          </ErrorBoundary>
        </main>
      </div>
    </div>
  );
}
