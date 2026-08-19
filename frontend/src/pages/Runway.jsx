import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Link, useNavigate } from 'react-router-dom';
import { useAuth } from '../hooks/useAuth';
import { api } from '../lib/api';
import toast from 'react-hot-toast';

const COLUMNS = [
  { key: 'in_production', label: 'In Production', color: 'accent-blue', borderColor: '#3B82F6', bgTint: 'rgba(59,130,246,0.05)' },
  { key: 'in_review', label: 'In Review', color: 'warning', borderColor: '#F59E0B', bgTint: 'rgba(245,158,11,0.05)' },
  { key: 'scheduled', label: 'Scheduled', color: 'accent-cyan', borderColor: '#06B6D4', bgTint: 'rgba(6,182,212,0.05)' },
  { key: 'published', label: 'Published', color: 'success', borderColor: '#10B981', bgTint: 'rgba(16,185,129,0.05)' },
];

function ArticleCard({ article, onCancel, onRetry }) {
  const navigate = useNavigate();
  const [showConfirm, setShowConfirm] = useState(false);
  const canCancel = article.state !== 'PUBLISHED' && article.state !== 'ARCHIVED';
  const isRevision = article.state === 'REVISION';
  const isFailed = article.state === 'FAILED';

  const productionStates = ['OUTLINING', 'DRAFTING', 'HUMANIZING', 'EDIT_REVIEW', 'MEDIA_ASSEMBLY'];
  const isInProduction = productionStates.includes(article.state);


  const reviewStates = ['WAITING_CHECKPOINT_2'];
  const getCardHref = () => {
    if (isRevision) return null;
    if (reviewStates.includes(article.state)) return `/review/article/${article.id}`;
    if (article.state === 'PUBLISHED' && article.ghost_post_url) return article.ghost_post_url;
    return null;
  };

  const href = getCardHref();
  const isExternal = href && href.startsWith('http');

  const handleCardClick = (e) => {
    // Don't navigate if clicking cancel buttons
    if (e.target.closest('[data-testid="cancel-button"]') || e.target.closest('[data-testid="cancel-confirm"]')) return;
    if (!href) return;
    if (isExternal) {
      window.open(href, '_blank', 'noopener,noreferrer');
    } else {
      navigate(href);
    }
  };

  return (
    <div
      className={`group glass-card mb-2.5 rounded-xl border border-border/50 overflow-hidden ${href ? 'cursor-pointer hover:border-accent-blue/30 hover:shadow-md transition-all' : ''}`}
      data-testid="article-card"
      onClick={handleCardClick}
      role={href ? 'link' : undefined}
    >
      {/* Zone 1: Title */}
      <div className="px-3 pt-2.5 pb-1.5">
        <h3 className="font-semibold text-text-primary leading-snug" style={{fontSize: '13px', display: '-webkit-box', WebkitLineClamp: 3, WebkitBoxOrient: 'vertical', overflow: 'hidden'}}>
          {isRevision ? (
            <span className="flex items-center gap-1.5 text-text-muted">
              <span>🔒</span>
              <span className="line-through opacity-50">{article.title}</span>
            </span>
          ) : article.title}
        </h3>
      </div>

      {/* Zone 2: Metadata pills */}
      <div className="px-3 pb-1.5 flex flex-wrap items-center gap-x-1.5 gap-y-1" style={{fontSize: '10px'}}>
        <span className={`px-1.5 py-px rounded-full font-medium ${article.has_seo ? 'bg-success/15 text-success border border-success/25' : 'bg-warning/15 text-warning border border-warning/25'}`} style={{fontSize: '10px'}}>
          {article.has_seo ? '✓ SEO' : '✗ No SEO'}
        </span>

        {article.image_count > 0 && (
          <span className={article.valid_image_count > 0 ? 'text-accent-cyan' : 'text-error'}>
            {article.valid_image_count > 0 ? `🖼 ${article.valid_image_count}` : '🖼 Missing'}
          </span>
        )}

        {article.word_count > 0 && (
          <span className="text-text-muted">
            📝 {article.word_count >= 1000 ? `${(article.word_count / 1000).toFixed(1)}k` : article.word_count}
          </span>
        )}
      </div>

      {/* Zone 3: Footer — state + date */}
      <div className="flex items-center justify-between gap-2 px-3 py-1.5" style={{borderTop: '1px solid rgba(148, 163, 184, 0.08)'}}>
        <span className="font-medium text-text-secondary whitespace-nowrap" style={{fontSize: '11px'}}>
          {isRevision ? (
            <span className="text-warning">🔒 Being revised…</span>
          ) : isInProduction ? (
            <span className="inline-flex items-center gap-1.5 text-accent-blue">
              <span className="production-loader">
                <span className="dot" />
                <span className="dot" />
                <span className="dot" />
              </span>
              <span>{article.state_label}</span>
            </span>
          ) : (
            article.state_label
          )}
        </span>
        <span className="text-text-muted whitespace-nowrap" style={{fontSize: '10px'}}>
          {article.scheduled_publish_at
            ? `📅 ${new Date(article.scheduled_publish_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })}`
            : article.state === 'PUBLISHED'
              ? article.updated_at ? new Date(article.updated_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric' }) : ''
              : article.updated_at ? `${new Date(article.updated_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}` : ''}
        </span>
      </div>

      {/* Cancel / Retry actions — always visible bottom strip */}
      {canCancel && !isRevision && (
        showConfirm ? (
          <div className="flex items-center justify-between px-3 py-1.5" style={{borderTop: '1px solid rgba(248, 113, 113, 0.15)', background: 'rgba(248, 113, 113, 0.05)'}} data-testid="cancel-confirm">
            <span className="text-text-secondary" style={{fontSize: '10px'}}>Cancel this article?</span>
            <div className="flex gap-1.5">
              <button onClick={() => onCancel(article.id)} className="px-2.5 py-0.5 rounded-lg bg-error/10 text-error" style={{fontSize: '10px'}} data-testid="confirm-cancel">Yes</button>
              <button onClick={() => setShowConfirm(false)} className="px-2.5 py-0.5 rounded-lg bg-bg-hover text-text-muted" style={{fontSize: '10px'}}>Keep</button>
            </div>
          </div>
        ) : isFailed && onRetry ? (
          <div className="flex items-stretch" style={{borderTop: '1px solid rgba(148, 163, 184, 0.05)'}}>
            <button
              onClick={() => onRetry(article.id)}
              className="flex-1 text-center text-accent-blue hover:bg-accent-blue/5 transition-colors py-1.5"
              style={{fontSize: '10px'}}
              data-testid="retry-button"
            >
              ↻ Retry
            </button>
            <div style={{width: '1px', background: 'rgba(148, 163, 184, 0.08)'}} />
            <button
              onClick={() => setShowConfirm(true)}
              className="flex-1 text-center text-text-muted hover:text-error transition-colors py-1.5"
              style={{fontSize: '10px'}}
              data-testid="cancel-button"
            >
              Cancel article
            </button>
          </div>
        ) : (
          <button onClick={() => setShowConfirm(true)} className="w-full text-center text-text-muted hover:text-error transition-colors py-1.5" style={{borderTop: '1px solid rgba(148, 163, 184, 0.05)', fontSize: '10px'}} data-testid="cancel-button">Cancel article</button>
        )
      )}
    </div>
  );
}

function BatchStatusBanner({ batches }) {
  if (!batches || batches.length === 0) return null;

  const pending = batches.filter(b => b.status === 'pending_ideation' || b.status === 'processing_ideation');

  if (pending.length === 0) return null;

  return (
    <div className="space-y-3 mb-6" data-testid="batch-status">
      {pending.map(b => (
        <div key={b.id} className="glass-card p-4 border border-accent-blue/20 flex items-center gap-3">
          <div className="w-5 h-5 border-2 border-accent-blue border-t-transparent rounded-full animate-spin flex-shrink-0" />
          <div>
            <p className="text-sm text-text-primary font-medium">Generating ideas…</p>
            <p className="text-xs text-text-muted">
              {b.source === 'analysis'
                ? 'Generating articles from your blog analysis...'
                : 'Brief submitted — ideas will appear shortly'}
            </p>
          </div>
        </div>
      ))}
    </div>
  );
}

function ArchivedSection({ articles, onCancel }) {
  const [expanded, setExpanded] = useState(false);
  if (!articles || articles.length === 0) return null;

  return (
    <div className="mt-8" data-testid="archived-section">
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex items-center gap-2 text-sm text-text-muted hover:text-text-secondary transition-colors mb-3"
      >
        <span>{expanded ? '▼' : '▶'}</span>
        <span>Archived ({articles.length})</span>
      </button>
      {expanded && (
        <div className="grid grid-cols-3 gap-4">
          {articles.map(article => (
            <ArticleCard key={article.id} article={article} onCancel={onCancel} />
          ))}
        </div>
      )}
    </div>
  );
}

function OnboardingChecklist({ user }) {
  const ghostConnected = user?.ghost_key_valid === 1;
  let hasSchedule = false;
  try { hasSchedule = JSON.parse(user?.publish_days || '[]').length > 0; } catch {}

  const getStartedHref = !ghostConnected
    ? '/dashboard/settings?tab=ghost'
    : !hasSchedule
      ? '/dashboard/settings?tab=schedule'
      : '/dashboard/new-batch';

  return (
    <div className="glass-card-lg p-8 max-w-lg mx-auto text-center" data-testid="onboarding-checklist">
      <h2 className="text-xl font-bold text-text-primary mb-2">Welcome to DraftSpring! 🚀</h2>
      <p className="text-text-secondary mb-6">Complete these steps to start generating articles.</p>
      <div className="space-y-3 text-left">
        <Link to="/dashboard/settings?tab=ghost" className="flex items-center gap-3 hover:opacity-80 transition-opacity"><span>{ghostConnected ? '✅' : '☐'}</span><span>Connect Ghost blog</span></Link>
        <Link to="/dashboard/settings?tab=schedule" className="flex items-center gap-3 hover:opacity-80 transition-opacity"><span>{hasSchedule ? '✅' : '☐'}</span><span>Set publish schedule</span></Link>
        <Link to="/dashboard/new-batch" className="flex items-center gap-3 hover:opacity-80 transition-opacity"><span>☐</span><span>Submit your first ideas</span></Link>
      </div>
      <Link to={getStartedHref} className="btn-primary inline-block mt-6 px-6 py-3">Get Started →</Link>
    </div>
  );
}

export function Runway() {
  const { user } = useAuth();
  const queryClient = useQueryClient();

  const { data, isLoading } = useQuery({
    queryKey: ['articles'],
    queryFn: () => api.get('/api/articles'),
    refetchInterval: 15000,
  });

  const { data: batchData } = useQuery({
    queryKey: ['batches'],
    queryFn: () => api.get('/api/batches'),
    refetchInterval: 10000,
  });

  const cancelMutation = useMutation({
    mutationFn: (articleId) => api.post(`/api/articles/${articleId}/cancel`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['articles'] });
      toast.success('Article cancelled');
    },
    onError: () => {
      // Error toast already shown by api.js — just ensure UI resets
      queryClient.invalidateQueries({ queryKey: ['articles'] });
    },
  });

  const retryMutation = useMutation({
    mutationFn: (articleId) => api.post(`/api/articles/${articleId}/retry`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['articles'] });
      toast.success('Retrying article');
    },
    onError: () => {
      queryClient.invalidateQueries({ queryKey: ['articles'] });
    },
  });

  const articles = data?.articles || [];
  const batches = batchData?.batches || [];

  // Separate archived from active articles (Bug 7)
  const activeArticles = articles.filter(a => a.column !== 'archived');
  const archivedArticles = articles.filter(a => a.column === 'archived');

  const grouped = {};
  for (const col of COLUMNS) grouped[col.key] = activeArticles.filter((a) => a.column === col.key);

  // Sort scheduled articles: earliest publish date first
  if (grouped['scheduled']) {
    grouped['scheduled'].sort((a, b) => {
      const dateA = a.scheduled_publish_at ? new Date(a.scheduled_publish_at) : new Date(a.created_at);
      const dateB = b.scheduled_publish_at ? new Date(b.scheduled_publish_at) : new Date(b.created_at);
      return dateA - dateB;
    });
  }

  if (isLoading) {
    return <div className="p-8"><h1 className="text-2xl font-bold text-text-primary mb-6">Dashboard</h1><div className="skeleton h-64" /></div>;
  }

  if (articles.length === 0 && batches.filter(b => b.status === 'pending_ideation' || b.status === 'processing_ideation').length === 0) {
    return (
      <div className="p-8">
        <h1 className="text-2xl font-bold text-text-primary mb-6">Dashboard</h1>
        <BatchStatusBanner batches={batches} />
        <OnboardingChecklist user={user} />
      </div>
    );
  }

  return (
    <div className="p-8">
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold text-text-primary">Dashboard</h1>
        <Link to="/dashboard/new-batch" className="btn-primary text-sm px-4 py-2">+ New Content</Link>
      </div>

      {/* Batch status banners (Bug 9) */}
      <BatchStatusBanner batches={batches} />

      {/* Kanban board with distinct column styling (Bug 2) */}
      <div className="grid grid-cols-4 gap-4" data-testid="kanban-board">
        {COLUMNS.map((col) => (
          <div
            key={col.key}
            className="rounded-xl overflow-hidden"
            style={{
              borderLeft: `3px solid ${col.borderColor}`,
              background: col.bgTint,
            }}
          >
            <h2
              className="text-sm font-semibold px-3 py-2.5 border-b border-border/30"
              style={{ color: col.borderColor }}
            >
              {col.label} <span className="text-xs opacity-60">({grouped[col.key].length})</span>
            </h2>
            <div className="min-h-[200px] p-2">
              {grouped[col.key].map((article) => (
                <ArticleCard key={article.id} article={article} onCancel={(id) => cancelMutation.mutate(id)} onRetry={(id) => retryMutation.mutate(id)} />
              ))}
            </div>
          </div>
        ))}
      </div>

      {/* Archived section (Bug 7) */}
      <ArchivedSection articles={archivedArticles} onCancel={(id) => cancelMutation.mutate(id)} />
    </div>
  );
}
