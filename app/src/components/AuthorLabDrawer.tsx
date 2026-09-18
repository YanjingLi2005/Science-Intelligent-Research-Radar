import {
  Building2,
  ExternalLink,
  GraduationCap,
  TrendingUp,
  Users,
} from 'lucide-react';
import type {
  AuthorProfile,
  InfluenceMetrics,
  LabTimelineItem,
  Paper,
} from '../data/mock';
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from './ui/sheet';

interface AuthorLabDrawerProps {
  paper?: Paper | null;
  authorProfile?: AuthorProfile;
  influenceMetrics?: InfluenceMetrics;
  labTimeline?: LabTimelineItem[];
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function AuthorLabDrawer({
  paper,
  authorProfile,
  influenceMetrics,
  labTimeline,
  open,
  onOpenChange,
}: AuthorLabDrawerProps) {
  const profile = paper?.authorProfile ?? authorProfile;
  const metrics = paper?.influenceMetrics ?? influenceMetrics;
  const timeline = [...(paper?.labTimeline ?? labTimeline ?? [])].sort((a, b) => b.year - a.year);

  const isTopTier =
    metrics?.is_top_tier_lab === true ||
    profile?.is_top_tier === true ||
    paper?.strategicFlags?.includes('TOP_TIER_LAB') === true;

  const topHIndex = metrics?.top_h_index ?? profile?.top_h_index;
  const velocity = metrics?.citation_velocity;
  const primaryInstitution =
    metrics?.top_institution || profile?.primary_institution || 'Unknown Research Group';
  const citations = metrics?.citation_count ?? 0;
  const socialHeat = metrics?.social_heat ?? 0;

  const authors = paper?.authors ?? [];
  const roster = authors.map((name, index) => {
    const isFirst = index === 0;
    const isLast = index === authors.length - 1 && authors.length > 1;
    const role = isFirst ? '第一作者' : isLast ? '通讯作者' : '合作者';
    const hIndex = isFirst
      ? profile?.first_author_h_index
      : isLast
        ? profile?.corresponding_author_h_index
        : undefined;
    return { name, role, hIndex };
  });

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="w-full sm:max-w-lg overflow-y-auto p-0">
        <SheetHeader className="border-b border-hairline px-5 py-4 dark:border-white/10">
          <SheetTitle className="flex items-center gap-2 text-base">
            <Building2 className="size-4 text-[#4E8AFB]" />
            团队动态 / Lab Profile
          </SheetTitle>
          <SheetDescription className="text-xs">
            {primaryInstitution} · 竞争团队学术影响力画像
          </SheetDescription>
        </SheetHeader>

        <div className="space-y-6 px-5 py-5">
          {/* Team profile header */}
          <div className="rounded-xl border border-hairline bg-white p-4 dark:border-white/10 dark:bg-white/[0.03]">
            <div className="flex flex-col sm:flex-row sm:items-start sm:justify-between gap-3">
              <div>
                <div className="kicker mb-1">Team Profile · Primary Institution</div>
                <div className="text-sm font-semibold text-neutral-900 dark:text-white">
                  {primaryInstitution}
                </div>
              </div>
              <div className="flex flex-wrap gap-1.5">
                {isTopTier && <span className="badge-blue">Top-Tier Lab</span>}
                {typeof topHIndex === 'number' && topHIndex > 0 && (
                  <span className="badge-teal">H-Index {topHIndex}</span>
                )}
                {typeof velocity === 'number' && velocity > 0 && (
                  <span className="badge-orange">Velocity {velocity}/mo</span>
                )}
              </div>
            </div>

            <div className="mt-4 grid grid-cols-3 gap-3">
              <div className="rounded-lg bg-neutral-50 p-3 dark:bg-white/[0.04]">
                <div className="font-mono text-[10px] text-neutral-400 dark:text-white/50">Citations</div>
                <div className="num-display mt-1 text-[#4E8AFB]">{citations}</div>
              </div>
              <div className="rounded-lg bg-neutral-50 p-3 dark:bg-white/[0.04]">
                <div className="font-mono text-[10px] text-neutral-400 dark:text-white/50">Velocity</div>
                <div className="num-display mt-1 text-[#4E8AFB]">{velocity ?? '—'}</div>
              </div>
              <div className="rounded-lg bg-neutral-50 p-3 dark:bg-white/[0.04]">
                <div className="font-mono text-[10px] text-neutral-400 dark:text-white/50">Social Heat</div>
                <div className="num-display mt-1 text-[#4E8AFB]">{socialHeat || '—'}</div>
              </div>
            </div>

            {profile?.affiliations && profile.affiliations.length > 0 && (
              <div className="mt-3 flex flex-wrap gap-1.5">
                {profile.affiliations.slice(0, 4).map((aff) => (
                  <span key={aff} className="chip">{aff}</span>
                ))}
              </div>
            )}
          </div>

          {/* Author roster */}
          <div>
            <div className="kicker mb-3 flex items-center gap-1.5">
              <Users className="size-3.5" /> Author Roster
            </div>
            {roster.length === 0 ? (
              <p className="text-xs text-neutral-400 dark:text-white/60">暂无作者信息（离线降级）</p>
            ) : (
              <div className="space-y-2">
                {roster.map((author) => (
                  <div
                    key={`${author.name}-${author.role}`}
                    className="flex items-center justify-between rounded-lg border border-hairline px-3.5 py-2.5 dark:border-white/10"
                  >
                    <div className="min-w-0">
                      <div className="truncate text-[13px] font-medium text-neutral-800 dark:text-white/90">
                        {author.name}
                      </div>
                      <div className="text-[10px] text-neutral-400 dark:text-white/50">{author.role}</div>
                    </div>
                    {typeof author.hIndex === 'number' && (
                      <span className="badge-teal shrink-0">H {author.hIndex}</span>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Research direction timeline */}
          <div>
            <div className="kicker mb-3 flex items-center gap-1.5">
              <GraduationCap className="size-3.5" /> Research Direction Timeline
            </div>
            {timeline.length === 0 ? (
              <p className="text-xs text-neutral-400 dark:text-white/60">暂无可信实验室时间线（等待下次扫描在线丰富）</p>
            ) : (
              <div className="relative ml-1.5 border-l border-hairline dark:border-white/15">
                {timeline.map((item, index) => (
                  <div key={`${item.year}-${index}`} className="relative ml-4 pb-5 last:pb-0">
                    <span className="absolute -left-[5.5px] top-1 h-2.5 w-2.5 rounded-full border-2 border-white bg-[#4E8AFB] dark:border-[#17171A]" />
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-mono text-[11px] font-semibold text-neutral-700 dark:text-white/90">
                        {item.year}
                      </span>
                      {item.badge && <span className="badge-gray">{item.badge}</span>}
                    </div>
                    <div className="mt-1 text-[13px] font-medium leading-snug text-neutral-800 dark:text-white/95">
                      {item.title}
                    </div>
                    <div className="mt-0.5 text-[11px] text-neutral-500 dark:text-white/65">
                      {item.venue || 'Unknown venue'}
                      {typeof item.citations === 'number' && ` · ${item.citations} citations`}
                    </div>
                    {item.url && (
                      <a
                        href={item.url}
                        target="_blank"
                        rel="noreferrer"
                        className="mt-1 inline-flex items-center gap-1 text-[11px] text-[#4E8AFB] hover:underline"
                      >
                        <ExternalLink className="size-3" /> 查看来源
                      </a>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Social heat blurb */}
          <div className="flex items-start gap-2 rounded-lg border border-hairline bg-neutral-50 p-3 text-[11px] leading-relaxed text-neutral-500 dark:border-white/10 dark:bg-white/[0.02] dark:text-white/60">
            <TrendingUp className="mt-0.5 size-3.5 shrink-0 text-[#4E8AFB]" />
            <span>
              该画像综合 Semantic Scholar / OpenAlex 在线指标与本地启发式回退。若外部服务不可用，
              将保留本地元数据并继续扫描，不会阻塞雷达任务。
            </span>
          </div>
        </div>
      </SheetContent>
    </Sheet>
  );
}

export default AuthorLabDrawer;
