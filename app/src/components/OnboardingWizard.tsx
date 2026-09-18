import { useState } from 'react';
import { ArrowRight, CheckCheck, FileUp, Radar, ScanSearch } from 'lucide-react';
import { useNavigate } from 'react-router';
import { useProject } from '../contexts/ProjectContext';
import { Reveal } from './chrome';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from './ui/dialog';

const ONBOARDING_KEY = 'rr-onboarding-done';

const STEPS = [
  {
    title: '创建监控 / 上传文稿',
    locator: '01 · CREATE',
    description: '为论文建立一个持续监控对象。上传 .tex、.md 或 .pdf 文稿后，Research Radar 会提取研究问题与核心 Claim。',
    cta: '创建第一个监控',
    icon: FileUp,
    path: () => '/cases?new=1',
  },
  {
    title: '确认 Claim',
    locator: '02 · CONFIRM',
    description: '复核系统从文稿中提取的论断，确认至少一条准确 Claim。它们将成为后续检索、证据比对与影响判断的基准。',
    cta: '前往确认 Claim',
    icon: CheckCheck,
    path: (caseId: string) => `/cases/${caseId}/paper`,
  },
  {
    title: '启动雷达扫描',
    locator: '03 · SCAN',
    description: '围绕已确认的 Claim 扫描公开文献。雷达会保留检索回执，并标出支持、挑战或改变论文边界的新证据。',
    cta: '前往文献雷达',
    icon: ScanSearch,
    path: (caseId: string) => `/cases/${caseId}/radar`,
  },
  {
    title: '复核影响与行动',
    locator: '04 · ACT',
    description: '确认新证据对论文的实际影响，再把它转成补实验、补引用或调整表述的可审阅行动。',
    cta: '开始复核行动',
    icon: Radar,
    path: (caseId: string) => `/cases/${caseId}/actions`,
  },
] as const;

export default function OnboardingWizard() {
  const navigate = useNavigate();
  const { projectId, caseList } = useProject();
  const [step, setStep] = useState(0);
  const [open, setOpen] = useState(() => localStorage.getItem(ONBOARDING_KEY) === null);

  const current = STEPS[step];
  const Icon = current.icon;
  const caseId = projectId || caseList[0]?.id || '';
  const needsCase = step > 0 && !caseId;

  const finish = () => {
    localStorage.setItem(ONBOARDING_KEY, '1');
    setOpen(false);
  };

  const handleCta = () => {
    if (needsCase) {
      navigate('/cases?new=1');
      setStep(0);
      return;
    }

    navigate(current.path(caseId));
    if (step === STEPS.length - 1) finish();
  };

  return (
    <Dialog open={open} onOpenChange={(nextOpen) => !nextOpen && finish()}>
      <DialogContent
        showCloseButton={false}
        className="card max-w-[560px] gap-0 overflow-hidden border-hairline bg-white p-0 text-ink shadow-2xl dark:border-white/[0.1] dark:bg-[#17171A] dark:text-white"
      >
        <div className="border-b border-hairline px-6 py-4 dark:border-white/[0.08] sm:px-8">
          <div className="flex items-center justify-between gap-4">
            <span className="locator uppercase">First run · Research Radar</span>
            <button type="button" onClick={finish} className="btn-quiet !px-2 !py-1 text-[11px]">
              跳过
            </button>
          </div>
          <div className="mt-4 flex items-center gap-2" aria-label={`第 ${step + 1} 步，共 ${STEPS.length} 步`}>
            {STEPS.map((item, index) => (
              <span
                key={item.title}
                aria-hidden="true"
                className={`h-1.5 rounded-full transition-all ${
                  index === step
                    ? 'w-8 bg-[#18181B] dark:bg-white'
                    : index < step
                      ? 'w-3 bg-neutral-400 dark:bg-white/55'
                      : 'w-3 bg-neutral-200 dark:bg-white/15'
                }`}
              />
            ))}
            <span className="locator ml-auto">{step + 1} / {STEPS.length}</span>
          </div>
        </div>

        <Reveal className="px-6 py-8 sm:px-8 sm:py-10">
          <div className="flex h-11 w-11 items-center justify-center rounded-lg bg-neutral-100 text-ink dark:bg-white/[0.07] dark:text-white">
            <Icon size={20} strokeWidth={1.8} />
          </div>
          <DialogHeader className="mt-6 text-left">
            <span className="locator uppercase">{current.locator}</span>
            <DialogTitle className="text-[20px] tracking-tight">{current.title}</DialogTitle>
            <DialogDescription className="pt-1 text-[13.5px] leading-7 text-neutral-500 dark:text-white/70">
              {current.description}
            </DialogDescription>
          </DialogHeader>

          <button type="button" onClick={handleCta} className="btn-primary mt-7">
            {needsCase ? '先创建监控' : current.cta}
            <ArrowRight size={14} />
          </button>
        </Reveal>

        <div className="flex items-center justify-between border-t border-hairline px-6 py-4 dark:border-white/[0.08] sm:px-8">
          <button
            type="button"
            onClick={() => setStep((value) => Math.max(0, value - 1))}
            disabled={step === 0}
            className="btn-secondary disabled:opacity-35"
          >
            上一步
          </button>
          <div className="flex items-center gap-2">
            <button type="button" onClick={finish} className="btn-quiet">
              跳过
            </button>
            {step < STEPS.length - 1 ? (
              <button type="button" onClick={() => setStep((value) => value + 1)} className="btn-primary">
                下一步
              </button>
            ) : (
              <button type="button" onClick={finish} className="btn-primary">
                完成
              </button>
            )}
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
