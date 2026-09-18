import type { ComponentProps, ReactNode } from 'react';
import { motion, useReducedMotion } from 'motion/react';
import { Radar } from 'lucide-react';

interface FooterItem {
  title: string;
  href?: string;
}

interface FooterSection {
  label: string;
  items: FooterItem[];
}

export type LoginLocale = 'zh' | 'en';

const footerSections: Record<LoginLocale, FooterSection[]> = {
  zh: [
  {
    label: '产品能力',
    items: [
      { title: '持续文献雷达' },
      { title: '论文主张追踪' },
      { title: '引文与证据核验' },
      { title: '文献问答' },
    ],
  },
  {
    label: '研究流程',
    items: [
      { title: '上传研究文稿' },
      { title: '发现新证据' },
      { title: '评估论文影响' },
      { title: '确认改进建议' },
    ],
  },
  {
    label: '文献来源',
    items: [
      { title: 'arXiv' },
      { title: 'OpenAlex' },
      { title: 'PubMed' },
      { title: 'Crossref' },
    ],
  },
  {
    label: '平台',
    items: [
      { title: '独立研究空间' },
      { title: '人工确认决策' },
      { title: 'API 文档', href: '/api/docs' },
    ],
  },
  ],
  en: [
    { label: 'Product', items: [
      { title: 'Literature radar' }, { title: 'Claim tracking' },
      { title: 'Citation & evidence checks' }, { title: 'Literature Q&A' },
    ] },
    { label: 'Workflow', items: [
      { title: 'Upload your manuscript' }, { title: 'Discover new evidence' },
      { title: 'Assess its impact' }, { title: 'Review suggested actions' },
    ] },
    { label: 'Sources', items: [
      { title: 'arXiv' }, { title: 'OpenAlex' }, { title: 'PubMed' }, { title: 'Crossref' },
    ] },
    { label: 'Platform', items: [
      { title: 'Private workspace' }, { title: 'Human review' },
      { title: 'API docs', href: '/api/docs' },
    ] },
  ],
};

export function Footer({ locale }: { locale: LoginLocale }) {
  return (
    <footer className="relative mx-auto flex w-full max-w-6xl flex-col items-center justify-center overflow-hidden rounded-t-[2rem] border-t border-black/10 bg-[radial-gradient(35%_128px_at_50%_0%,rgba(0,0,0,0.045),transparent)] px-6 py-12 dark:border-white/10 dark:bg-[radial-gradient(35%_128px_at_50%_0%,rgba(255,255,255,0.08),transparent)] md:rounded-t-[3rem] lg:py-16">
      <div className="absolute left-1/2 top-0 h-px w-1/3 -translate-x-1/2 rounded-full bg-black/20 blur-sm dark:bg-white/30" aria-hidden="true" />

      <div className="grid w-full gap-8 xl:grid-cols-3">
        <AnimatedContainer className="space-y-4">
          <div className="flex items-center gap-3">
            <span className="flex size-8 items-center justify-center rounded-lg border border-black/10 bg-black/5 dark:border-white/20 dark:bg-white/10">
              <Radar className="size-4" aria-hidden="true" />
            </span>
            <span className="text-sm font-semibold tracking-tight">Research Radar</span>
          </div>
          <p className="max-w-48 text-sm leading-relaxed text-neutral-600 dark:text-white/55">
            {locale === 'zh' ? '让新证据持续服务于你的研究。' : 'Make new evidence work for your research.'}
          </p>
          <p className="pt-2 text-xs text-neutral-400 dark:text-white/40">© {new Date().getFullYear()} Research Radar</p>
        </AnimatedContainer>

        <div className="grid grid-cols-2 gap-8 md:grid-cols-4 xl:col-span-2">
          {footerSections[locale].map((section, index) => (
            <AnimatedContainer key={section.label} delay={0.1 + index * 0.1}>
              <h2 className="text-xs font-medium text-ink dark:text-white/90">{section.label}</h2>
              <ul className="mt-4 space-y-2 text-sm text-neutral-500 dark:text-white/50">
                {section.items.map((item) => (
                  <li key={item.title}>
                    {item.href ? (
                      <a className="inline-flex transition-colors duration-300 hover:text-ink dark:hover:text-white" href={item.href}>
                        {item.title}
                      </a>
                    ) : (
                      <span>{item.title}</span>
                    )}
                  </li>
                ))}
              </ul>
            </AnimatedContainer>
          ))}
        </div>
      </div>
    </footer>
  );
}

type ViewAnimationProps = {
  delay?: number;
  className?: ComponentProps<typeof motion.div>['className'];
  children: ReactNode;
};

function AnimatedContainer({ className, delay = 0.1, children }: ViewAnimationProps) {
  const shouldReduceMotion = useReducedMotion();

  return (
    <motion.div
      initial={shouldReduceMotion ? false : { filter: 'blur(4px)', translateY: -8, opacity: 0 }}
      whileInView={shouldReduceMotion ? undefined : { filter: 'blur(0px)', translateY: 0, opacity: 1 }}
      viewport={{ once: true }}
      transition={{ delay, duration: 0.8 }}
      className={className}
    >
      {children}
    </motion.div>
  );
}
