import { useState } from 'react';
import { useParams, useNavigate, Link } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../lib/api';
import { trackEvent } from '../lib/posthog';
import toast from 'react-hot-toast';

export function IdeaReview() {
  const { batchId } = useParams();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [selected, setSelected] = useState(new Set());
  const [editedTitles, setEditedTitles] = useState({});
  const [showRegen, setShowRegen] = useState(false);
  const [feedback, setFeedback] = useState('');

  const { data, isLoading, error } = useQuery({
    queryKey: ['ideas', batchId],
    queryFn: () => api.get(`/api/checkpoints/ideas/${batchId}`),
    refetchInterval: (query) => {
      // Poll while regenerating (status = pending_ideation)
      const d = query.state.data;
      return (d?.status === 'pending_ideation' || d?.status === 'processing_ideation') ? 3000 : false;
    },
  });

  const approveMutation = useMutation({
    mutationFn: (approvedIdeas) =>
      api.post(
        '/api/checkpoints/ideas/approve',
        { batch_id: batchId, approved_ideas: approvedIdeas },
        { errorToastOptions: { duration: 12000 } },
      ),
    onSuccess: (result) => {
      const articlesCreated = result.articles_created ?? 0;
      if (articlesCreated === 0 || result.budget_limited) {
        toast.error('No articles were created. Please try again or contact support.', { duration: 12000 });
        return;
      }
      trackEvent('article_reviewed', { batch_id: batchId, articles_created: articlesCreated });
      toast.success(`${articlesCreated} article${articlesCreated === 1 ? '' : 's'} created!`);
      navigate('/dashboard');
    },
  });

  const regenMutation = useMutation({
    mutationFn: (fb) =>
      api.post('/api/checkpoints/ideas/regenerate', { batch_id: batchId, feedback: fb }),
    onSuccess: (result) => {
      toast.success(`Regenerating ideas (${result.regen_count}/${result.max_regen} used)`);
      navigate('/dashboard');
    },
    onError: (err) => {
      toast.error(err.message || 'Failed to regenerate');
    },
  });

  const hasCustomImages = data?.has_custom_images || false;
  const regenCount = data?.regen_count || 0;
  const maxRegen = data?.max_regen || 3;
  const canRegen = regenCount < maxRegen;

  const toggleIdea = (id) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        if (hasCustomImages && next.size >= 1) {
          toast.error('Only 1 idea can be selected when custom images are attached');
          return prev;
        }
        next.add(id);
      }
      return next;
    });
  };

  const updateTitle = (id, title) => { setEditedTitles((prev) => ({ ...prev, [id]: title })); };

  const handleApprove = () => {
    if (selected.size === 0) { toast.error('Select at least one idea'); return; }
    approveMutation.mutate([...selected].map((id) => ({ id, title: editedTitles[id] || null })));
  };

  const handleRegenerate = () => {
    if (!feedback.trim()) { toast.error('Please describe what kind of ideas you want'); return; }
    regenMutation.mutate(feedback.trim());
  };

  if (isLoading) return <div className="min-h-screen bg-bg-deep flex items-center justify-center"><div className="w-8 h-8 border-2 border-accent-blue border-t-transparent rounded-full animate-spin" /></div>;
  if (error) return <div className="min-h-screen bg-bg-deep flex items-center justify-center"><div className="glass-card p-8"><p className="text-error">{error.message}</p></div></div>;

  const { ideas, status } = data;

  // If batch is regenerating, redirect to dashboard (banner shows progress there)
  if (status === 'pending_ideation' || status === 'processing_ideation') {
    navigate('/dashboard');
    return null;
  }

  if (status === 'expired') return (
    <div className="min-h-screen bg-bg-deep flex items-center justify-center px-4">
      <div className="glass-card-lg p-8 max-w-lg text-center" data-testid="expired-state">
        <h1 className="text-xl font-bold text-text-primary mb-2">Ideas Expired</h1>
        <p className="text-text-secondary">These ideas have expired.</p>
      </div>
    </div>
  );

  if (status === 'processed') return (
    <div className="min-h-screen bg-bg-deep flex items-center justify-center px-4">
      <div className="glass-card-lg p-8 max-w-lg text-center" data-testid="processed-state">
        <h1 className="text-xl font-bold text-text-primary mb-2">Ideas Processed</h1>
        <p className="text-text-secondary">Your approved ideas are being turned into articles.</p>
        <Link to="/dashboard" className="btn-primary inline-block mt-4 px-6 py-2 text-sm">← Back to Dashboard</Link>
      </div>
    </div>
  );

  return (
    <div className="min-h-screen bg-bg-deep px-4 py-8">
      <div className="max-w-3xl mx-auto">
        <div className="mb-6">
          <Link to="/dashboard" className="text-sm text-text-muted hover:text-text-secondary transition-colors" data-testid="back-button">← Back to Dashboard</Link>
        </div>
        <div className="text-center mb-8">
          <h1 className="text-2xl font-bold gradient-text mb-2">Review Your Ideas</h1>
          <p className="text-sm text-text-muted mt-1" data-testid="budget-info">{selected.size} selected</p>
        </div>
        {hasCustomImages && (
          <div className="glass-card p-4 mb-4 border border-accent-cyan/30 bg-accent-cyan/5" data-testid="custom-images-banner">
            <p className="text-sm text-accent-cyan font-medium">📸 Custom images attached</p>
            <p className="text-xs text-text-muted mt-1">Some topics have reference images. You can only select 1 idea per batch when custom images are present, so images are correctly matched to the article.</p>
          </div>
        )}
        <div className="space-y-3" data-testid="idea-list">
          {ideas.filter(i => i.status === 'pending').map((idea) => (
            <div key={idea.id} className={`glass-card p-4 cursor-pointer ${selected.has(idea.id) ? 'border-accent-blue/40' : ''}`}
              data-testid="idea-card" onClick={() => toggleIdea(idea.id)}>
              <div className="flex items-start gap-3">
                <input type="checkbox" checked={selected.has(idea.id)} readOnly
                  className="mt-1 pointer-events-none" data-testid="idea-checkbox" />
                <div className="flex-1">
                  <input type="text" value={editedTitles[idea.id] ?? idea.title}
                    onChange={(e) => updateTitle(idea.id, e.target.value)} onClick={(e) => e.stopPropagation()}
                    className="w-full bg-transparent text-text-primary font-semibold text-sm border-none focus:outline-none rounded px-1"
                    data-testid="idea-title-input" />
                  <p className="text-text-secondary text-xs mt-1">{idea.angle}</p>

                </div>
              </div>
            </div>
          ))}
        </div>

        {/* Action buttons */}
        <div className="mt-8 flex flex-col items-center gap-4">
          <button onClick={handleApprove} disabled={selected.size === 0 || approveMutation.isPending}
            className="btn-primary px-8 py-3" data-testid="approve-button">
            {approveMutation.isPending ? 'Approving...' : `Approve ${selected.size} Idea${selected.size !== 1 ? 's' : ''}`}
          </button>

          {/* Regenerate section */}
          {canRegen && !showRegen && (
            <button onClick={() => setShowRegen(true)}
              className="text-sm text-text-muted hover:text-accent-blue transition-colors underline underline-offset-4"
              data-testid="regen-toggle">
              Not happy? Regenerate ideas ({maxRegen - regenCount} left)
            </button>
          )}
          {!canRegen && (
            <p className="text-xs text-text-muted" data-testid="regen-exhausted">Maximum regenerations reached</p>
          )}

          {showRegen && (
            <div className="w-full glass-card p-5 mt-2" data-testid="regen-panel">
              <h3 className="text-sm font-semibold text-text-primary mb-2">What would you like instead?</h3>
              <p className="text-xs text-text-muted mb-3">Describe what kind of ideas you're looking for. Be specific — the more detail, the better the results.</p>
              <textarea
                value={feedback}
                onChange={(e) => setFeedback(e.target.value)}
                placeholder="e.g. More technical deep-dives, less beginner content. Focus on advanced strategies..."
                className="w-full px-4 py-3 rounded-xl bg-bg-deep border border-border text-text-primary text-sm focus:outline-none focus:border-accent-blue/50 transition-all resize-none"
                rows={3}
                maxLength={2000}
                data-testid="regen-feedback"
              />
              <div className="flex items-center justify-between mt-3">
                <span className="text-xs text-text-muted">{feedback.length}/2000</span>
                <div className="flex gap-2">
                  <button onClick={() => { setShowRegen(false); setFeedback(''); }}
                    className="px-4 py-2 text-sm text-text-muted hover:text-text-secondary transition-colors"
                    data-testid="regen-cancel">
                    Cancel
                  </button>
                  <button onClick={handleRegenerate}
                    disabled={!feedback.trim() || regenMutation.isPending}
                    className="btn-primary px-6 py-2 text-sm"
                    data-testid="regen-submit">
                    {regenMutation.isPending ? 'Regenerating...' : `Regenerate (${maxRegen - regenCount} left)`}
                  </button>
                </div>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
