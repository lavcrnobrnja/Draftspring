import { useState, useMemo, useRef, useEffect } from 'react';
import { useParams, Link, useSearchParams } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import DOMPurify from 'dompurify';
import { api } from '../lib/api';
import { trackEvent } from '../lib/posthog';
import toast from 'react-hot-toast';

export function ArticleReview() {
  const { articleId } = useParams();
  const queryClient = useQueryClient();
  const [revisionNotes, setRevisionNotes] = useState('');
  const [seoExpanded, setSeoExpanded] = useState(false);
  const [searchParams, setSearchParams] = useSearchParams();
  const autoApproveTriggered = useRef(false);

  const { data, isLoading, error } = useQuery({
    queryKey: ['article-review', articleId],
    queryFn: () => api.get(`/api/checkpoints/article/${articleId}`),
  });

  const approveMutation = useMutation({
    mutationFn: () => api.post('/api/checkpoints/article/approve', { article_id: articleId }),
    onSuccess: () => {
      trackEvent('article_approved', { article_id: articleId });
      toast.success('Article approved! It will be published on schedule.');
      queryClient.invalidateQueries({ queryKey: ['article-review', articleId] });
    },
    onError: (err) => {
      const msg = err?.response?.data?.detail || err?.message || 'Failed to approve article';
      // If already approved or wrong state, just refresh to show current state
      if (msg.includes('not in review state')) {
        queryClient.invalidateQueries({ queryKey: ['article-review', articleId] });
      } else {
        toast.error(msg);
      }
    },
  });

  const reviseMutation = useMutation({
    mutationFn: (notes) => api.post('/api/checkpoints/article/revise', { article_id: articleId, revision_notes: notes }),
    onSuccess: () => {
      trackEvent('article_rejected', { article_id: articleId });
      toast.success('Revision requested. Your notes have been sent.');
      queryClient.invalidateQueries({ queryKey: ['article-review', articleId] });
    },
  });

  // Auto-approve from email link (?action=approve)
  useEffect(() => {
    if (
      searchParams.get('action') === 'approve' &&
      data?.state === 'WAITING_CHECKPOINT_2' &&
      !autoApproveTriggered.current
    ) {
      autoApproveTriggered.current = true;
      approveMutation.mutate();
      setSearchParams({}, { replace: true }); // Clean URL
    }
  }, [data?.state, searchParams]);

  const handleRevise = () => {
    if (revisionNotes.length < 20) {
      toast.error('Revision notes must be at least 20 characters');
      return;
    }
    reviseMutation.mutate(revisionNotes);
  };

  const sanitizedHtml = useMemo(() => {
    if (!data?.draft_html) return '<p>No content available</p>';
    const raw = data.draft_html.replace(
      /<img[^>]*src="local:\/\/[^"]*"[^>]*>/g,
      (match) => {
        const alt = match.match(/alt="([^"]*)"/)?.[1] || 'Image';
        return `<div style="background:#1a2236;border:1px solid rgba(148,163,184,0.08);border-radius:12px;padding:12px;display:flex;align-items:center;gap:8px"><span style="font-size:1.5em">🖼️</span><span style="color:#64748b;font-size:0.875em">${alt}</span></div>`;
      }
    );
    return DOMPurify.sanitize(raw, { ADD_TAGS: ['style'], ADD_ATTR: ['style'] });
  }, [data?.draft_html]);

  if (isLoading) return <div className="min-h-screen bg-bg-deep flex items-center justify-center"><div className="w-8 h-8 border-2 border-accent-blue border-t-transparent rounded-full animate-spin" /></div>;
  if (error) return <div className="min-h-screen bg-bg-deep flex items-center justify-center"><div className="glass-card p-8"><p className="text-error">{error.message}</p></div></div>;

  const { draft_html, images, seo, review_history: reviews, budget_remaining, state, article_id: aid } = data;

  return (
    <div className="min-h-screen bg-bg-deep">
      {/* Back button (Bug 6) */}
      <div className="px-6 py-3 border-b border-border bg-bg-card/50">
        <Link to="/dashboard" className="text-sm text-text-muted hover:text-text-primary transition-colors" data-testid="back-to-dashboard">
          ← Back to Dashboard
        </Link>
      </div>
      <div className="flex" style={{ height: 'calc(100vh - 49px)' }} data-testid="split-screen">
        {/* Left: Article Preview */}
        <div className="w-3/5 overflow-y-auto border-r border-border p-8" data-testid="article-preview">
          <div className="max-w-2xl mx-auto prose prose-invert">
            <div dangerouslySetInnerHTML={{ __html: sanitizedHtml }} />
          </div>

          {images && images.length > 0 && (
            <div className="mt-4 space-y-2" data-testid="article-images">
              {images.map((img, i) => (
                <div key={i} className="rounded-xl overflow-hidden">
                  {img.storage_url && !img.storage_url.startsWith('local://') ? (
                    <img src={img.storage_url} alt={img.alt_text || ''} className="max-w-full" />
                  ) : (
                    <div className="bg-bg-hover border border-border rounded-xl p-4 flex items-center gap-3">
                      <span className="text-2xl">🖼️</span>
                      <span className="text-sm text-text-muted">{img.alt_text || `Image ${i + 1}`}</span>
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}

          {seo && (
            <div className="mt-6 max-w-2xl mx-auto">
              <button onClick={() => setSeoExpanded(!seoExpanded)} className="text-sm text-text-muted hover:text-text-secondary" data-testid="seo-toggle">
                {seoExpanded ? '▼' : '▶'} SEO Information
              </button>
              {seoExpanded && (
                <div className="glass-card p-4 mt-2 space-y-2 text-sm" data-testid="seo-info">
                  <p><span className="text-text-muted">Focus keyword:</span> <span className="text-text-primary">{seo.focus_keyword}</span></p>
                  <p><span className="text-text-muted">Meta title:</span> <span className="text-text-primary">{seo.meta_title}</span></p>
                  <p><span className="text-text-muted">Meta description:</span> <span className="text-text-primary">{seo.meta_description}</span></p>
                  {seo.secondary_keywords && <p><span className="text-text-muted">Secondary:</span> <span className="text-text-primary">{seo.secondary_keywords.join(', ')}</span></p>}
                </div>
              )}
            </div>
          )}
        </div>

        {/* Right: Actions */}
        <div className="w-2/5 overflow-y-auto p-8" data-testid="review-actions">
          {state === 'READY_TO_PUBLISH' && (
            <div className="mb-4 px-4 py-2 bg-success/10 border border-success/20 rounded-xl">
              <p className="text-sm text-success font-medium">✓ Approved for publishing</p>
              {data.scheduled_publish_at && (
                <p className="text-xs text-success/70 mt-1">
                  Scheduled for {new Date(data.scheduled_publish_at).toLocaleString('en-US', { weekday: 'long', month: 'long', day: 'numeric', hour: 'numeric', minute: '2-digit', timeZoneName: 'short' })}
                </p>
              )}
            </div>
          )}

          {state === 'REVISION' && (
            <div className="mb-4 px-4 py-2 bg-warning/10 border border-warning/20 rounded-xl">
              <p className="text-sm text-warning font-medium">🔒 Revision in progress — the article is being rewritten based on your feedback.</p>
            </div>
          )}

          {state === 'WAITING_CHECKPOINT_2' && (
            <>
              <div className="mb-6">
                {data.word_count > 0 && (
                  <p className="text-sm text-text-muted mb-3" data-testid="word-count">
                    📝 {data.word_count.toLocaleString()} words
                  </p>
                )}
                <p className="text-sm text-text-muted mb-1" data-testid="budget-indicator">
                  Revision budget: {budget_remaining ?? '?'} remaining
                </p>
              </div>

              {/* Approve */}
              <button onClick={() => approveMutation.mutate()} disabled={approveMutation.isPending}
                className="btn-primary w-full py-3 mb-6" data-testid="approve-button">
                {approveMutation.isPending ? 'Approving...' : '✓ Approve & Schedule'}
              </button>

              {/* Revision */}
              <div className="space-y-3">
                <label className="block text-sm font-medium text-text-secondary">Request Revision</label>
                <textarea value={revisionNotes} onChange={(e) => setRevisionNotes(e.target.value)}
                  placeholder="Describe what changes you'd like (min 20 characters)..."
                  className="w-full px-4 py-3 bg-bg-hover border border-border rounded-xl text-text-primary placeholder:text-text-muted focus:outline-none focus:border-accent-blue resize-none h-32"
                  data-testid="revision-textarea" />
                <div className="flex items-center justify-between">
                  <span className="text-xs text-text-muted">{revisionNotes.length}/20 min characters</span>
                  <button onClick={handleRevise}
                    disabled={revisionNotes.length < 20 || reviseMutation.isPending || (budget_remaining !== undefined && budget_remaining <= 0)}
                    className="btn-secondary px-6 py-2 text-sm" data-testid="revise-button">
                    {reviseMutation.isPending ? 'Sending...' : 'Request Revision'}
                  </button>
                </div>
              </div>
            </>
          )}

          {state !== 'WAITING_CHECKPOINT_2' && state !== 'READY_TO_PUBLISH' && state !== 'REVISION' && (
            <div className="mb-4 px-4 py-2 bg-bg-hover border border-border rounded-xl">
              <p className="text-sm text-text-muted">This article is currently in <strong className="text-text-primary">{state?.replace(/_/g, ' ')?.toLowerCase()}</strong> state.</p>
            </div>
          )}

          {/* Review History */}
          {reviews && reviews.length > 0 && (
            <div className="mt-8" data-testid="review-history">
              <h3 className="text-sm font-semibold text-text-secondary mb-3">Review History</h3>
              <div className="space-y-2">
                {reviews.map((review, i) => (
                  <div key={i} className="glass-card p-3 text-sm">
                    <div className="flex items-center justify-between">
                      <span className={`font-medium ${review.status === 'approved' ? 'text-success' : review.status === 'pending' ? 'text-accent-blue' : 'text-text-muted'}`}>
                        {review.status === 'approved' ? '✓ Approved' : review.status === 'pending' ? '● Awaiting your review' : '↩ Revision was requested (completed)'}
                      </span>
                      <span className="text-xs text-text-muted">Round {review.review_number || i + 1}</span>
                    </div>
                    {review.revision_notes && <p className="text-text-muted mt-1 text-xs">{review.revision_notes}</p>}
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
