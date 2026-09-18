import { useEffect, useRef, useState, type ReactNode } from 'react';

/* ---------- scroll reveal ---------- */
export function Reveal({ children, delay = 0, className = '' }: { children: ReactNode; delay?: number; className?: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const [inView, setInView] = useState(false);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ob = new IntersectionObserver(
      ([e]) => {
        if (e.isIntersecting) {
          setInView(true);
          ob.disconnect();
        }
      },
      { threshold: 0.08 },
    );
    ob.observe(el);
    return () => ob.disconnect();
  }, []);
  return (
    <div ref={ref} className={`reveal ${inView ? 'in' : ''} ${className}`} style={{ ['--reveal-delay' as string]: `${delay}ms` }}>
      {children}
    </div>
  );
}

/* ---------- section heading ---------- */
export function SectionHead({ kicker, title, right }: { kicker?: string; title: string; right?: ReactNode }) {
  return (
    <div className="mb-6 flex items-end justify-between gap-4 border-b border-black/10 pb-4 dark:border-white/10">
      <div>
        {kicker && <div className="kicker mb-2">{kicker}</div>}
        <h2 className="text-[21px] font-semibold tracking-tight">{title}</h2>
      </div>
      {right && <div className="shrink-0">{right}</div>}
    </div>
  );
}

/* ---------- stat ---------- */
export function Stat({ value, label, tone = 'ink' }: { value: ReactNode; label: string; tone?: 'ink' | 'red' | 'teal' | 'orange' }) {
  const color =
    tone === 'red'
      ? 'text-[#B42318] dark:text-red-300'
      : tone === 'orange'
        ? 'text-[#B54708] dark:text-amber-300'
        : tone === 'teal'
          ? 'text-teal'
          : 'text-ink dark:text-white';
  return (
    <div>
      <div className={`num-display ${color}`}>{value}</div>
      <div className="mt-1.5 text-xs text-neutral-500 tracking-wide dark:text-white/85">{label}</div>
    </div>
  );
}

/* ---------- quote block with locator ---------- */
export function Quote({ children, loc, against = false, label }: { children: ReactNode; loc?: string; against?: boolean; label?: string }) {
  return (
    <figure>
      {label && <div className="locator mb-1.5 uppercase">{label}</div>}
      <blockquote className={`quote ${against ? 'quote-against' : ''}`}>{children}</blockquote>
      {loc && <figcaption className="locator mt-1.5 pl-4">{loc}</figcaption>}
    </figure>
  );
}

/* ---------- underline tabs (keyboard-navigable) ---------- */
export function Tabs({ tabs, active, onChange }: { tabs: string[]; active: number; onChange: (i: number) => void }) {
  const listRef = useRef<HTMLDivElement>(null);
  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key !== 'ArrowRight' && e.key !== 'ArrowLeft' && e.key !== 'Home' && e.key !== 'End') return;
    e.preventDefault();
    const buttons = Array.from(listRef.current?.querySelectorAll('[role="tab"]') ?? []) as HTMLButtonElement[];
    if (buttons.length === 0) return;
    const current = buttons.findIndex((b) => b === document.activeElement);
    let next = current;
    if (e.key === 'ArrowRight') next = (current + 1) % buttons.length;
    else if (e.key === 'ArrowLeft') next = (current - 1 + buttons.length) % buttons.length;
    else if (e.key === 'Home') next = 0;
    else if (e.key === 'End') next = buttons.length - 1;
    buttons[next]?.focus();
    onChange(next);
  };
  return (
    <div ref={listRef} role="tablist" aria-label="页面标签" className="flex flex-wrap hairline-b mb-6" onKeyDown={onKeyDown}>
      {tabs.map((t, i) => (
        <button
          key={t}
          role="tab"
          id={`tab-${i}`}
          aria-selected={i === active}
          aria-controls={`tabpanel-${i}`}
          tabIndex={i === active ? 0 : -1}
          className={`tab-btn ${i === active ? 'active' : ''}`}
          onClick={() => onChange(i)}
        >
          {t}
        </button>
      ))}
    </div>
  );
}

/* ---------- tiny progress ring / dot ---------- */
export function Dot({ cls }: { cls: string }) {
  return <span className={`inline-block w-[7px] h-[7px] rounded-full ${cls}`} />;
}

/* ---------- empty state ---------- */
export function Empty({ text }: { text: string }) {
  return (
    <div className="rounded-2xl border border-dashed border-black/15 bg-white/40 py-14 text-center dark:border-white/10 dark:bg-white/[0.02]">
      <div className="select-none text-2xl font-light text-neutral-400 dark:text-white/45">⌁</div>
      <div className="mt-2 text-sm text-neutral-500 dark:text-white/65">{text}</div>
    </div>
  );
}

/* ---------- priority badge ---------- */
export function Priority({ p }: { p: 'P0' | 'P1' | 'P2' }) {
  const cls = p === 'P0' ? 'badge-red' : p === 'P1' ? 'badge-orange' : 'badge-gray';
  return <span className={`${cls} font-mono`}>{p}</span>;
}
