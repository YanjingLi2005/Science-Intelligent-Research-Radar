import { useState, type FormEvent } from 'react';
import { ArrowRight, Moon, Radar, Sun } from 'lucide-react';
import { useTheme } from 'next-themes';
import { useAuth } from '../contexts/AuthContext';
import { Footer } from '../components/ui/footer-section';
import { useLanguage } from '../contexts/languageState';

const copy = {
  zh: {
    brandTagline: '科学情报', workspaceBadge: '研究情报工作台', workspaceKicker: '研究空间',
    lightMode: '白天模式', darkMode: '黑夜模式',
    eyebrow: '为正在发生的研究而建', headline: '让新证据，', headlineMuted: '推动下一步研究。',
    description: '持续追踪与你的论文相关的新文献，理解它对核心主张的影响，把发现转化为值得采取的行动。',
    steps: ['追踪文献', '核验证据', '确认行动'],
    welcome: '欢迎回来', registerTitle: '开启你的研究雷达',
    welcomeDescription: '登录后继续查看你的研究动态。', registerDescription: '创建账号，建立独立的研究空间。',
    accountActions: '账号操作', login: '登录', register: '注册', username: '用户名',
    usernamePlaceholder: '输入用户名', usernameHint: '2–32 位字母、数字或 _ . -',
    password: '密码', passwordPlaceholder: '输入密码', newPasswordPlaceholder: '设置至少 8 位密码',
    busy: '请稍候…', loginSubmit: '进入工作台', registerSubmit: '创建账号并进入',
    privacy: '每个账号拥有独立的数据空间与 LLM 配置。',
  },
  en: {
    brandTagline: 'Science intelligence', workspaceBadge: 'Research workspace', workspaceKicker: 'Research workspace',
    lightMode: 'Light mode', darkMode: 'Dark mode',
    eyebrow: 'Built for research in progress', headline: 'Let new evidence', headlineMuted: 'move your research forward.',
    description: 'Track new literature relevant to your paper, understand its impact on your core claims, and turn discoveries into actions worth taking.',
    steps: ['Track literature', 'Check evidence', 'Review actions'],
    welcome: 'Welcome back', registerTitle: 'Start your research radar',
    welcomeDescription: 'Sign in to keep up with your research.', registerDescription: 'Create an account and a private research workspace.',
    accountActions: 'Account actions', login: 'Sign in', register: 'Sign up', username: 'Username',
    usernamePlaceholder: 'Enter your username', usernameHint: '2–32 letters, numbers, or _ . -',
    password: 'Password', passwordPlaceholder: 'Enter your password', newPasswordPlaceholder: 'At least 8 characters',
    busy: 'Please wait…', loginSubmit: 'Enter workspace', registerSubmit: 'Create account',
    privacy: 'Each account has its own data space and LLM settings.',
  },
} as const;

const englishAuthErrors: Record<string, string> = {
  '请输入用户名和密码': 'Enter your username and password.',
  '用户名只能包含字母、数字、点、下划线或连字符（2-32 位）': 'Username must be 2–32 letters, numbers, or _ . -',
  '密码至少 8 位': 'Password must be at least 8 characters.',
  '用户名已被注册': 'This username is already taken.',
  '用户名或密码错误': 'Incorrect username or password.',
  '注册请求过于频繁，请稍后再试': 'Too many sign-up attempts. Please try again later.',
  '登录尝试过于频繁，请稍后再试': 'Too many sign-in attempts. Please try again later.',
  '当前已关闭公开注册，请联系管理员创建账号': 'Public sign-up is closed. Contact an administrator for an account.',
};

export default function Login() {
  const { login, register } = useAuth();
  const { resolvedTheme, setTheme } = useTheme();
  const { locale, toggleLanguage } = useLanguage();
  const t = copy[locale];
  const [mode, setMode] = useState<'login' | 'register'>('login');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    if (!username.trim() || !password) {
      setError('请输入用户名和密码');
      return;
    }
    setBusy(true);
    try {
      if (mode === 'login') {
        await login(username.trim(), password);
      } else {
        await register(username.trim(), password);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div data-i18n-manual lang={locale === 'zh' ? 'zh-CN' : 'en'} className="relative flex min-h-screen flex-col overflow-hidden bg-paper text-ink dark:bg-night dark:text-white">
      <div className="pointer-events-none absolute inset-x-0 top-0 h-[680px] bg-[radial-gradient(ellipse_55%_55%_at_52%_0%,rgba(0,0,0,0.045),transparent)] dark:bg-[radial-gradient(ellipse_55%_55%_at_52%_0%,rgba(255,255,255,0.065),transparent)]" aria-hidden="true" />

      <header className="relative mx-auto flex w-full max-w-6xl items-center justify-between px-6 py-7">
        <div className="flex items-center gap-3">
          <span className="flex size-9 items-center justify-center rounded-xl bg-[#18181B] text-white dark:border dark:border-white/15 dark:bg-white/10">
            <Radar className="size-[18px]" strokeWidth={2} aria-hidden="true" />
          </span>
          <div className="flex flex-col leading-tight">
            <span className="text-sm font-semibold tracking-tight">Research Radar</span>
            <span className="mt-0.5 text-[10px] uppercase tracking-[0.18em] text-neutral-500 dark:text-white/40">{t.brandTagline}</span>
          </div>
        </div>
        <div className="flex items-center gap-3">
          <span className="hidden rounded-full border border-black/10 px-3 py-1.5 text-[11px] tracking-wide text-neutral-500 dark:border-white/10 dark:text-white/50 sm:inline-flex">
            {t.workspaceBadge}
          </span>
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
            type="button"
            onClick={() => setTheme(resolvedTheme === 'dark' ? 'light' : 'dark')}
            aria-label={resolvedTheme === 'dark' ? t.lightMode : t.darkMode}
            title={resolvedTheme === 'dark' ? t.lightMode : t.darkMode}
            className="flex size-9 items-center justify-center rounded-full border border-black/10 text-neutral-600 transition-colors hover:bg-black/5 dark:border-white/10 dark:text-white/70 dark:hover:bg-white/10"
          >
            {resolvedTheme === 'dark' ? <Sun className="size-4" aria-hidden="true" /> : <Moon className="size-4" aria-hidden="true" />}
          </button>
        </div>
      </header>

      <main className="relative mx-auto grid w-full max-w-6xl flex-1 items-center gap-14 px-6 pb-24 pt-16 lg:min-h-[660px] lg:grid-cols-[minmax(0,1fr)_420px] lg:gap-24 lg:pb-28 lg:pt-10">
        <div className="max-w-xl">
          <p className="mb-7 flex items-center gap-3 text-[11px] font-medium uppercase tracking-[0.22em] text-neutral-500 dark:text-white/45">
            <span className="h-px w-7 bg-black/30 dark:bg-white/40" aria-hidden="true" /> {t.eyebrow}
          </p>
          <h1 className="text-[clamp(2.6rem,5vw,4.5rem)] font-semibold leading-[1.12] tracking-[-0.055em]">
            {t.headline}<br />
            <span className="text-neutral-400 dark:text-white/45">{t.headlineMuted}</span>
          </h1>
          <p className="mt-7 max-w-md text-[15px] leading-7 text-neutral-600 dark:text-white/55">
            {t.description}
          </p>

          <div className="mt-12 grid max-w-lg grid-cols-3 gap-4 border-t border-black/10 pt-6 dark:border-white/10">
            {t.steps.map((label, index) => (
              <div key={label} className="flex flex-col gap-2">
                <span className="font-mono text-[11px] text-neutral-400 dark:text-white/35">{String(index + 1).padStart(2, '0')}</span>
                <span className="text-[12px] font-medium text-neutral-700 dark:text-white/75">{label}</span>
              </div>
            ))}
          </div>
        </div>

        <div className="relative w-full overflow-hidden rounded-[1.75rem] border border-black/10 bg-white/85 p-6 shadow-[0_24px_90px_rgba(0,0,0,0.08)] backdrop-blur-xl dark:border-white/10 dark:bg-white/[0.035] dark:shadow-[0_24px_90px_rgba(0,0,0,0.25)] sm:p-9">
          <div className="pointer-events-none absolute inset-x-0 top-0 h-32 bg-[radial-gradient(ellipse_65%_100%_at_50%_0%,rgba(0,0,0,0.025),transparent)] dark:bg-[radial-gradient(ellipse_65%_100%_at_50%_0%,rgba(255,255,255,0.07),transparent)]" aria-hidden="true" />
          <div className="relative">
            <span className="text-[10px] font-medium uppercase tracking-[0.2em] text-neutral-400 dark:text-white/40">{t.workspaceKicker}</span>
            <h2 className="mt-3 text-[25px] font-semibold tracking-tight">
              {mode === 'login' ? t.welcome : t.registerTitle}
            </h2>
            <p className="mt-2 text-[13px] leading-relaxed text-neutral-500 dark:text-white/50">
              {mode === 'login' ? t.welcomeDescription : t.registerDescription}
            </p>

            <div className="mt-8 grid grid-cols-2 rounded-xl border border-black/10 bg-black/[0.035] p-1 dark:border-white/10 dark:bg-black/20" aria-label={t.accountActions}>
              {(['login', 'register'] as const).map((item) => (
                <button
                  key={item}
                  type="button"
                  aria-pressed={mode === item}
                  onClick={() => {
                    setMode(item);
                    setError(null);
                  }}
                  className={`rounded-lg px-4 py-2.5 text-[13px] font-medium transition-colors ${mode === item ? 'bg-white text-ink shadow-sm dark:bg-white/10 dark:text-white' : 'text-neutral-500 hover:text-ink dark:text-white/45 dark:hover:text-white/75'}`}
                >
                  {item === 'login' ? t.login : t.register}
                </button>
              ))}
            </div>

            <form onSubmit={submit} className="mt-7 space-y-5">
              <div>
                <label htmlFor="login-username" className="mb-2 block text-[12px] font-medium text-neutral-700 dark:text-white/75">{t.username}</label>
                <input
                  id="login-username"
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  autoComplete="username"
                  aria-describedby={mode === 'register' ? 'register-username-hint' : undefined}
                  className="w-full rounded-xl border border-black/10 bg-black/[0.025] px-4 py-3 text-sm text-ink outline-none transition-colors placeholder:text-neutral-400 focus:border-black/35 focus:bg-white dark:border-white/10 dark:bg-white/[0.045] dark:text-white dark:placeholder:text-white/30 dark:focus:border-white/40 dark:focus:bg-white/[0.07]"
                  placeholder={t.usernamePlaceholder}
                />
                {mode === 'register' && (
                  <p id="register-username-hint" className="mt-2 text-[11px] leading-relaxed text-neutral-500 dark:text-white/45">
                    {t.usernameHint}
                  </p>
                )}
              </div>
              <div>
                <label htmlFor="login-password" className="mb-2 block text-[12px] font-medium text-neutral-700 dark:text-white/75">{t.password}</label>
                <input
                  id="login-password"
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
                  className="w-full rounded-xl border border-black/10 bg-black/[0.025] px-4 py-3 text-sm text-ink outline-none transition-colors placeholder:text-neutral-400 focus:border-black/35 focus:bg-white dark:border-white/10 dark:bg-white/[0.045] dark:text-white dark:placeholder:text-white/30 dark:focus:border-white/40 dark:focus:bg-white/[0.07]"
                  placeholder={mode === 'login' ? t.passwordPlaceholder : t.newPasswordPlaceholder}
                />
              </div>

              {error && (
                <div role="alert" className="rounded-xl border border-red-500/25 bg-red-500/10 px-3.5 py-3 text-[12.5px] text-red-700 dark:text-red-300">
                  {locale === 'en' ? englishAuthErrors[error] ?? error : error}
                </div>
              )}

              <button
                type="submit"
                disabled={busy}
                className="group flex w-full items-center justify-center gap-2 rounded-xl bg-[#18181B] px-4 py-3 text-[13px] font-semibold text-white transition-all hover:bg-[#303034] disabled:opacity-50 dark:bg-white dark:text-[#111113] dark:hover:bg-white/90"
              >
                {busy ? t.busy : mode === 'login' ? t.loginSubmit : t.registerSubmit}
                {!busy && <ArrowRight className="size-4 transition-transform group-hover:translate-x-0.5" aria-hidden="true" />}
              </button>
            </form>

            <p className="mt-6 border-t border-black/10 pt-5 text-center text-[11px] leading-relaxed text-neutral-500 dark:border-white/10 dark:text-white/40">
              {t.privacy}
            </p>
          </div>
        </div>
      </main>
      <Footer locale={locale} />
    </div>
  );
}
