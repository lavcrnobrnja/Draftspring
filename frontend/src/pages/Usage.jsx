import { useQuery } from '@tanstack/react-query';
import { api } from '../lib/api';

const IN_PROGRESS_STATES = [
  'OUTLINING', 'DRAFTING', 'HUMANIZING', 'EDIT_REVIEW',
  'MEDIA_ASSEMBLY', 'WAITING_CHECKPOINT_2', 'REVISION',
  'READY_TO_PUBLISH', 'PUBLISHING',
];

function truncate(str, len = 18) {
  if (!str) return '';
  return str.length > len ? str.slice(0, len) + '…' : str;
}

function formatDate(iso) {
  if (!iso) return '';
  try {
    const d = new Date(iso);
    return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
  } catch { return ''; }
}

function getArticleState(state) {
  if (state === 'PUBLISHED') return 'published';
  if (state === 'FAILED') return 'failed';
  if (IN_PROGRESS_STATES.includes(state)) return 'in_progress';
  return 'empty';
}

export function Usage() {
  const { data, isLoading } = useQuery({
    queryKey: ['usage'],
    queryFn: () => api.get('/api/usage'),
  });

  if (isLoading) {
    return (
      <div className="max-w-2xl mx-auto px-4 py-8">
        <div className="skeleton h-16 w-48 mx-auto mb-4 rounded-xl" />
        <div className="skeleton h-4 w-64 mx-auto mb-8 rounded" />
        <div className="skeleton h-48 rounded-xl mb-6" />
        <div className="skeleton h-32 rounded-xl" />
      </div>
    );
  }

  const cycle = data?.cycle || {};
  const current = data?.current_cycle || {};
  const articles = data?.articles || [];
  const previousCycles = data?.previous_cycles || [];
  const limit = cycle.articles_limit || 8;
  const published = current.published || 0;
  const failed = current.failed || 0;
  const inProgress = current.in_progress || 0;
  const available = current.available || 0;
  const daysLeft = cycle.days_left ?? 0;

  // Nudge logic: weekly pacing — expect ~(limit/4) articles per week
  // e.g. 8/cycle = 2/week. Behind if done+inProgress < expected by this week.
  const cycleDays = 30;
  const daysElapsed = Math.max(0, cycleDays - daysLeft);
  const currentWeek = Math.floor(daysElapsed / 7); // 0-indexed: week 0 = days 0-6
  const articlesPerWeek = Math.ceil(limit / 4);
  const expectedByNow = Math.min(limit, (currentWeek + 1) * articlesPerWeek);
  const totalActive = published + inProgress;
  const behindPace = daysElapsed >= 7 && totalActive < expectedByNow && available > 0;

  // Build slots: one per article_limit
  const slots = [];
  const sortedArticles = [...articles].filter(a => a.state !== 'ARCHIVED');

  for (let i = 0; i < limit; i++) {
    if (i < sortedArticles.length) {
      const a = sortedArticles[i];
      const st = getArticleState(a.state);
      slots.push({
        type: st,
        title: a.title,
        date: a.published_at || a.scheduled_publish_at || null,
        article: a,
      });
    } else {
      slots.push({ type: 'empty', title: null, date: null, article: null });
    }
  }

  // Count published for progress line
  const lastPublishedIdx = slots.reduce((acc, s, i) => s.type === 'published' ? i : acc, -1);

  const cycleStart = cycle.start ? formatDate(cycle.start) : '';
  const cycleEnd = cycle.end ? formatDate(cycle.end) : '';

  return (
    <div className="max-w-2xl mx-auto px-4 py-6 flex flex-col gap-5">
      {/* Hero */}
      <div className="text-center">
        <div className="text-6xl font-bold tracking-tight">
          <span className="text-accent-blue">{published}</span>
          <span className="text-text-muted"> / {limit}</span>
        </div>
        <p className="text-text-secondary text-sm mt-2">
          articles published · {daysLeft} day{daysLeft !== 1 ? 's' : ''} left in cycle
        </p>
      </div>

      {/* Nudge banner */}
      {behindPace && (
        <div className="rounded-xl px-4 py-3 bg-warning/10 border border-warning/20 text-sm text-warning">
          <span className="font-medium">⚡ Behind pace —</span>{' '}
          {inProgress > 0 && `${inProgress} article${inProgress > 1 ? 's' : ''} in progress`}
          {inProgress > 0 && available > 0 && ', '}
          {available > 0 && `${available} slot${available > 1 ? 's' : ''} still available`}.
          {' '}Submit a batch to fill {available === 1 ? 'it' : 'them'}.
        </div>
      )}

      {/* Timeline card */}
      <div className="glass-card p-5">
        <div className="flex items-center justify-between mb-5">
          <span className="text-xs text-text-muted font-medium uppercase tracking-wider">
            Current Cycle
          </span>
          <span className="text-xs text-text-muted">
            {cycleStart} — {cycleEnd}
          </span>
        </div>

        {/* Timeline dots */}
        <div className="relative">
          {/* Progress line */}
          {lastPublishedIdx >= 0 && (
            <div
              className="absolute top-[18px] left-[18px] h-[3px] bg-accent-blue rounded-full z-0"
              style={{
                width: lastPublishedIdx > 0
                  ? `calc(${(lastPublishedIdx / (limit - 1)) * 100}% - 18px)`
                  : '0px',
              }}
            />
          )}

          {/* Background line */}
          <div className="absolute top-[18px] left-[18px] h-[1px] bg-border z-0"
            style={{ width: `calc(100% - 36px)` }}
          />

          <div className="relative z-10 flex justify-between">
            {slots.map((slot, i) => (
              <div key={i} className="flex flex-col items-center" style={{ width: `${100 / limit}%` }}>
                {/* Dot */}
                {slot.type === 'published' && (
                  <div className="w-9 h-9 rounded-full bg-accent-blue flex items-center justify-center text-white text-xs font-bold shadow-[0_0_12px_rgba(59,130,246,0.3)]">
                    ✓
                  </div>
                )}
                {slot.type === 'failed' && (
                  <div className="w-9 h-9 rounded-full bg-error flex items-center justify-center text-white text-xs font-bold">
                    ✗
                  </div>
                )}
                {slot.type === 'in_progress' && (
                  <div className="w-9 h-9 rounded-full border-2 border-dashed border-warning flex items-center justify-center text-warning text-xs">
                    ⏳
                  </div>
                )}
                {slot.type === 'empty' && (
                  <div className="w-9 h-9 rounded-full border-2 border-dashed border-text-muted/30 flex items-center justify-center text-text-muted text-xs">
                    {i + 1}
                  </div>
                )}

                {/* Date */}
                <span className="text-[10px] text-text-muted mt-1.5 leading-tight">
                  {slot.date ? formatDate(slot.date) : '\u00A0'}
                </span>

                {/* Title — hide on mobile to prevent overlap */}
                <span className="hidden sm:block text-[10px] text-text-secondary mt-0.5 leading-tight text-center max-w-[72px] truncate">
                  {slot.title ? truncate(slot.title, 14) : '\u00A0'}
                </span>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* Previous cycles */}
      {previousCycles.length > 0 && (
        <div className="glass-card p-5">
          <span className="text-xs text-text-muted font-medium uppercase tracking-wider block mb-4">
            Previous Cycles
          </span>
          <div className="flex flex-col gap-3">
            {previousCycles.slice(0, 5).map((pc, i) => (
              <div key={i} className="flex items-center gap-3">
                <span className="text-xs text-text-secondary w-8 shrink-0 font-medium">
                  {pc.label}
                </span>
                <div className="flex gap-1 flex-1">
                  {Array.from({ length: pc.limit }).map((_, j) => {
                    let color = 'bg-text-muted/20'; // unused
                    if (j < pc.published) color = 'bg-accent-blue';
                    else if (j < pc.published + pc.failed) color = 'bg-error';
                    return (
                      <div key={j} className={`w-2.5 h-2.5 rounded-full ${color}`} />
                    );
                  })}
                </div>
                <span className="text-xs text-text-muted shrink-0">
                  {pc.published} of {pc.limit}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Legend */}
      <div className="flex items-center justify-center gap-5 text-xs text-text-muted">
        <div className="flex items-center gap-1.5">
          <div className="w-2.5 h-2.5 rounded-full bg-accent-blue" />
          Published
        </div>
        <div className="flex items-center gap-1.5">
          <div className="w-2.5 h-2.5 rounded-full bg-error" />
          Failed
        </div>
        <div className="flex items-center gap-1.5">
          <div className="w-2.5 h-2.5 rounded-full bg-text-muted/20" />
          Unused
        </div>
      </div>
    </div>
  );
}
