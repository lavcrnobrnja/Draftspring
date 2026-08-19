import { useState, useRef, useCallback, useEffect, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../hooks/useAuth';
import { api } from '../lib/api';
import { trackEvent } from '../lib/posthog';
import toast from 'react-hot-toast';
import { DEFAULT_IMAGE_STYLE, DEFAULT_IMAGE_SUBSTYLE, IMAGE_STYLE_OPTIONS, getSubstyles, firstSubstyle } from '../lib/imageStyles';

const URL_PATTERN = /^https?:\/\/\S+$/;
const MAX_IMAGE_SIZE = 5 * 1024 * 1024; // 5MB
const ALLOWED_TYPES = ['image/jpeg', 'image/png', 'image/webp', 'image/gif'];

// ── PhotoUpload (unchanged) ──────────────────────────────────────────

function PhotoUpload({ label, hint, file, onSet, onRemove }) {
  const fileRef = useRef(null);
  const [dragOver, setDragOver] = useState(false);

  const previewUrl = useMemo(() => {
    if (!file) return null;
    return URL.createObjectURL(file);
  }, [file]);

  useEffect(() => {
    return () => {
      if (previewUrl) URL.revokeObjectURL(previewUrl);
    };
  }, [previewUrl]);

  const handleFile = useCallback((f) => {
    if (!f) return;
    if (!ALLOWED_TYPES.includes(f.type)) {
      toast.error('Only JPEG, PNG, WebP, and GIF images are allowed');
      return;
    }
    if (f.size > MAX_IMAGE_SIZE) {
      toast.error('Image must be under 5MB');
      return;
    }
    onSet(f);
  }, [onSet]);

  const handleDrop = useCallback((e) => {
    e.preventDefault();
    setDragOver(false);
    const f = Array.from(e.dataTransfer.files).find(f => f.type.startsWith('image/'));
    if (f) handleFile(f);
  }, [handleFile]);

  if (file && previewUrl) {
    return (
      <div className="flex-1 min-w-0">
        <div className="relative group">
          <img
            src={previewUrl}
            alt={label}
            className="w-full h-[72px] object-cover rounded-lg border border-border"
          />
          <button
            type="button"
            onClick={onRemove}
            className="absolute -top-1.5 -right-1.5 w-5 h-5 bg-error text-white rounded-full text-xs flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity"
          >
            ✕
          </button>
          <div className="absolute bottom-1 left-1 bg-bg-deep/80 text-[10px] text-text-secondary px-1.5 py-0.5 rounded">
            {label}
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="flex-1 min-w-0">
      <div
        className={`border border-dashed rounded-lg px-3 py-2.5 text-center cursor-pointer transition-all h-[72px] flex flex-col items-center justify-center ${
          dragOver
            ? 'border-accent-blue bg-accent-blue/5'
            : 'border-border/60 hover:border-border-hover'
        }`}
        onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
        onDragLeave={() => setDragOver(false)}
        onDrop={handleDrop}
        onClick={() => fileRef.current?.click()}
      >
        <input
          ref={fileRef}
          type="file"
          accept="image/jpeg,image/png,image/webp,image/gif"
          className="hidden"
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) handleFile(f);
            if (fileRef.current) fileRef.current.value = '';
          }}
        />
        <p className="text-xs font-medium text-text-secondary">{label}</p>
        <p className="text-[10px] text-text-muted mt-0.5">{hint}</p>
      </div>
    </div>
  );
}

// ── Rotating loading one-liners ──────────────────────────────────────

const LOADING_MESSAGES = [
  "Consulting the muse...",
  "Stealing ideas from the future...",
  "Asking GPT to ask Claude to ask Gemini...",
  "Your competitors wish they had this...",
  "Brainstorming at the speed of silicon...",
  "Filtering out the bad puns... most of them...",
  "Scanning the blogosphere for gaps...",
  "Teaching robots to think creatively...",
  "Almost there... the AI is having a moment...",
  "Crafting headlines that don't suck...",
];

const ARTICLE_TYPE_COLORS = {
  'how-to': 'bg-accent-blue/15 text-accent-blue border-accent-blue/25',
  'listicle': 'bg-accent-violet/15 text-accent-violet border-accent-violet/25',
  'opinion': 'bg-warning/15 text-warning border-warning/25',
  'tutorial': 'bg-accent-cyan/15 text-accent-cyan border-accent-cyan/25',
  'deep-dive': 'bg-success/15 text-success border-success/25',
  'case-study': 'bg-error/15 text-error border-error/25',
};

function GeneratingScreen({ message }) {
  return (
    <div className="glass-card overflow-hidden">
      <div className="p-10 flex flex-col items-center justify-center text-center">
        <div className="w-12 h-12 border-2 border-accent-blue border-t-transparent rounded-full animate-spin mb-6" />
        <p className="text-lg font-semibold text-text-primary mb-2">Generating ideas…</p>
        <p
          className="text-sm text-text-muted transition-opacity duration-500"
          key={message}
          style={{ animation: 'fadeInOut 3.5s ease-in-out' }}
        >
          {message}
        </p>
      </div>
    </div>
  );
}

function InlineIdeaReview({ ideas, regenCount, maxRegen, hasCustomImages, onApprove, onRegenerate, onStartOver, approving }) {
  const [selected, setSelected] = useState(new Set());
  const [editedTitles, setEditedTitles] = useState({});
  const [showRegen, setShowRegen] = useState(false);
  const [feedback, setFeedback] = useState('');
  const [regenerating, setRegenerating] = useState(false);

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

  const updateTitle = (id, title) => {
    setEditedTitles((prev) => ({ ...prev, [id]: title }));
  };

  const handleApprove = () => {
    if (selected.size === 0) { toast.error('Select at least one idea'); return; }
    onApprove([...selected].map((id) => ({ id, title: editedTitles[id] || null })));
  };

  const handleRegenerate = async () => {
    if (!feedback.trim()) { toast.error('Please describe what kind of ideas you want'); return; }
    setRegenerating(true);
    try {
      await onRegenerate(feedback.trim());
      setFeedback('');
      setShowRegen(false);
    } finally {
      setRegenerating(false);
    }
  };

  const pendingIdeas = ideas.filter(i => i.status === 'pending');

  return (
    <div className="glass-card overflow-hidden">
      <div className="p-5">
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-sm font-semibold text-text-primary">Pick your ideas</h3>
          <span className="text-xs text-text-muted">
            {selected.size} of {pendingIdeas.length} selected
          </span>
        </div>

        {hasCustomImages && (
          <div className="glass-card p-3 mb-3 border border-accent-cyan/30 bg-accent-cyan/5">
            <p className="text-sm text-accent-cyan font-medium">📸 Custom images attached</p>
            <p className="text-xs text-text-muted mt-1">You can only select 1 idea when custom images are present, so images are correctly matched to the article.</p>
          </div>
        )}

        <div className="space-y-2" data-testid="inline-idea-list">
          {pendingIdeas.map((idea) => {
            const isSelected = selected.has(idea.id);
            const typeClass = idea.article_type
              ? (ARTICLE_TYPE_COLORS[idea.article_type] || 'bg-bg-hover text-text-muted border-border')
              : null;

            return (
              <label
                key={idea.id}
                className={`flex items-start gap-3 p-3 rounded-xl border cursor-pointer transition-all ${
                  isSelected
                    ? 'border-accent-blue/40 bg-accent-blue/5'
                    : 'border-border/50 hover:border-border-hover'
                }`}
              >
                <input
                  type="checkbox"
                  checked={isSelected}
                  onChange={() => toggleIdea(idea.id)}
                  className="mt-1 shrink-0 accent-accent-blue"
                />
                <div className="min-w-0 flex-1">
                  <div className="flex items-start gap-2 mb-1">
                    <input
                      type="text"
                      value={editedTitles[idea.id] ?? idea.title}
                      onChange={(e) => { e.stopPropagation(); updateTitle(idea.id, e.target.value); }}
                      onClick={(e) => e.stopPropagation()}
                      className="flex-1 bg-transparent text-text-primary font-semibold text-sm border-none focus:outline-none rounded px-1"
                      data-testid="idea-title-input"
                    />
                    {typeClass && (
                      <span className={`px-2 py-0.5 rounded-full text-[10px] font-medium border shrink-0 ${typeClass}`}>
                        {idea.article_type}
                      </span>
                    )}
                  </div>
                  <p className="text-xs text-text-muted">{idea.angle}</p>
                </div>
              </label>
            );
          })}
        </div>

        {/* Regenerate section */}
        {canRegen && !showRegen && (
          <button
            onClick={() => setShowRegen(true)}
            className="mt-4 text-sm text-text-muted hover:text-accent-blue transition-colors underline underline-offset-4"
            data-testid="regen-toggle"
          >
            Not happy? Regenerate ideas ({maxRegen - regenCount} left)
          </button>
        )}
        {!canRegen && (
          <p className="mt-4 text-xs text-text-muted" data-testid="regen-exhausted">
            Maximum regenerations reached
          </p>
        )}

        {showRegen && (
          <div className="mt-4 glass-card p-4" data-testid="regen-panel">
            <h3 className="text-sm font-semibold text-text-primary mb-2">What would you like instead?</h3>
            <p className="text-xs text-text-muted mb-3">Be specific — the more detail, the better the results.</p>
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
                <button
                  onClick={() => { setShowRegen(false); setFeedback(''); }}
                  className="px-4 py-2 text-sm text-text-muted hover:text-text-secondary transition-colors"
                >
                  Cancel
                </button>
                <button
                  onClick={handleRegenerate}
                  disabled={!feedback.trim() || regenerating}
                  className="btn-primary px-6 py-2 text-sm"
                  data-testid="regen-submit"
                >
                  {regenerating ? 'Regenerating...' : `Regenerate (${maxRegen - regenCount} left)`}
                </button>
              </div>
            </div>
          </div>
        )}
      </div>

      <div className="border-t border-border px-5 py-3 flex items-center justify-between bg-bg-deep/20">
        <button
          type="button"
          onClick={onStartOver}
          className="text-xs text-text-muted hover:text-text-secondary transition-colors"
        >
          ← Start over
        </button>
        <button
          type="button"
          onClick={handleApprove}
          disabled={selected.size === 0 || approving}
          className="btn-primary px-6 py-2 text-sm"
          data-testid="approve-ideas-button"
        >
          {approving ? 'Starting...' : 'Start Writing →'}
        </button>
      </div>
    </div>
  );
}

// ── Content Brief Tab (original form) ────────────────────────────────

function ContentBriefTab({ ghostConnected, subscriptionActive, onGenerating }) {
  const [description, setDescription] = useState('');
  const [referenceUrls, setReferenceUrls] = useState(['']);
  const [keywords, setKeywords] = useState('');
  const [coverPhoto, setCoverPhoto] = useState(null);
  const [bodyPhoto, setBodyPhoto] = useState(null);
  const [overrideImageStyle, setOverrideImageStyle] = useState(false);
  const [imageStyle, setImageStyle] = useState(DEFAULT_IMAGE_STYLE);
  const [imageSubstyle, setImageSubstyle] = useState(DEFAULT_IMAGE_SUBSTYLE);
  const [submitting, setSubmitting] = useState(false);
  const substyles = getSubstyles(imageStyle);
  const handleImageStyleChange = (value) => {
    setImageStyle(value);
    setImageSubstyle(firstSubstyle(value));
  };

  const addUrlField = () => {
    if (referenceUrls.length >= 3) return;
    setReferenceUrls((prev) => [...prev, '']);
  };

  const updateUrl = (index, value) => {
    setReferenceUrls((prev) => prev.map((u, i) => (i === index ? value : u)));
  };

  const removeUrl = (index) => {
    if (referenceUrls.length <= 1) {
      setReferenceUrls(['']);
      return;
    }
    setReferenceUrls((prev) => prev.filter((_, i) => i !== index));
  };

  const validate = () => {
    if (!description.trim()) {
      toast.error('Description is required');
      return false;
    }
    for (let i = 0; i < referenceUrls.length; i++) {
      const url = referenceUrls[i].trim();
      if (url && !URL_PATTERN.test(url)) {
        toast.error(`Reference URL ${i + 1}: invalid URL`);
        return false;
      }
    }
    return true;
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!validate()) return;
    setSubmitting(true);
    try {
      const payload = {
        description: description.trim(),
      };

      const validUrls = referenceUrls.map(u => u.trim()).filter(u => u);
      if (validUrls.length > 0) {
        payload.reference_urls = validUrls;
      }

      if (keywords.trim()) {
        payload.keywords = keywords.trim();
      }

      if (overrideImageStyle) {
        payload.image_style = imageStyle;
        payload.image_substyle = imageSubstyle;
      }

      const result = await api.post('/api/seeds', payload);
      const seedIds = result.seed_ids || [];
      const batchId = result.batch_id;

      const primarySeedId = seedIds[0];
      if (primarySeedId) {
        if (coverPhoto) {
          try {
            const formData = new FormData();
            formData.append('file', coverPhoto);
            formData.append('image_role', 'cover');
            await api.upload(`/api/seeds/${primarySeedId}/images`, formData);
          } catch (err) {
            console.warn('Failed to upload cover photo:', err);
            toast.error('Failed to upload cover photo');
          }
        }
        if (bodyPhoto) {
          try {
            const formData = new FormData();
            formData.append('file', bodyPhoto);
            formData.append('image_role', 'body');
            await api.upload(`/api/seeds/${primarySeedId}/images`, formData);
          } catch (err) {
            console.warn('Failed to upload body photo:', err);
            toast.error('Failed to upload body photo');
          }
        }
      }

      trackEvent('batch_created', {
        has_urls: validUrls.length > 0,
        url_count: validUrls.length,
        has_keywords: !!keywords.trim(),
        has_cover_photo: !!coverPhoto,
        has_body_photo: !!bodyPhoto,
        image_style_override: overrideImageStyle,
      });

      // Stay on page — enter generating state
      if (batchId) {
        onGenerating(batchId);
      } else {
        toast.success('Content brief submitted!');
      }
    } catch {
      // error already shown by api client
    } finally {
      setSubmitting(false);
    }
  };

  const blocked = !subscriptionActive || !ghostConnected;

  return (
    <form onSubmit={handleSubmit}>
      <div className="glass-card overflow-hidden">
        <div className="grid grid-cols-1 lg:grid-cols-[1fr_340px]">
          {/* Left: Your article brief */}
          <div className="p-5 flex flex-col">
            <label className="block text-sm font-medium text-text-primary mb-1.5">
              What do you want to write about? <span className="text-error">*</span>
            </label>
            <textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="Describe the article you want written. What's it about? What angle or point should it make? Who's it for? The more detail you provide, the better the result."
              rows={6}
              className="flex-1 min-h-[140px] w-full px-4 py-3 bg-bg-hover border border-border rounded-xl text-text-primary text-sm leading-relaxed placeholder:text-text-muted focus:outline-none focus:border-accent-blue transition-colors resize-none"
              data-testid="description-input"
            />

            <div className="mt-3">
              <label className="block text-xs font-medium text-text-secondary mb-1">
                Target keywords
                <span className="text-text-muted font-normal ml-1">optional, comma-separated</span>
              </label>
              <input
                type="text"
                value={keywords}
                onChange={(e) => setKeywords(e.target.value)}
                placeholder="e.g. saas churn reduction, customer retention strategies"
                className="w-full px-4 py-2 bg-bg-hover border border-border rounded-xl text-text-primary text-sm placeholder:text-text-muted focus:outline-none focus:border-accent-blue transition-colors"
                data-testid="keywords-input"
              />
            </div>
          </div>

          {/* Right: Supporting context */}
          <div className="border-t lg:border-t-0 lg:border-l border-border p-5 flex flex-col bg-bg-deep/30">
            <p className="text-[11px] font-medium text-text-muted uppercase tracking-wider mb-3">
              Supporting context
            </p>

            <div>
              <label className="block text-xs font-medium text-text-secondary mb-1">
                Reference URLs
              </label>
              <p className="text-[11px] text-text-muted mb-2">
                Articles we'll extract and use as inspiration.
              </p>
              <div className="space-y-1.5">
                {referenceUrls.map((url, index) => (
                  <div key={index} className="flex items-center gap-1.5">
                    <input
                      type="text"
                      value={url}
                      onChange={(e) => updateUrl(index, e.target.value)}
                      placeholder="https://..."
                      className="flex-1 min-w-0 px-3 py-1.5 bg-bg-hover border border-border rounded-lg text-text-primary text-sm placeholder:text-text-muted focus:outline-none focus:border-accent-blue transition-colors"
                      data-testid="url-input"
                    />
                    {referenceUrls.length > 1 && (
                      <button
                        type="button"
                        onClick={() => removeUrl(index)}
                        className="text-text-muted hover:text-error transition-colors p-1 rounded-lg hover:bg-error/5 shrink-0"
                        aria-label="Remove URL"
                      >
                        <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                          <path d="M18 6L6 18M6 6l12 12" />
                        </svg>
                      </button>
                    )}
                  </div>
                ))}
              </div>
              {referenceUrls.length < 3 && (
                <button
                  type="button"
                  onClick={addUrlField}
                  className="mt-1.5 text-xs text-accent-blue hover:text-accent-blue/80 font-medium transition-colors"
                >
                  + Add another
                </button>
              )}
            </div>

            <div className="h-px bg-border my-3" />

            <div>
              <label className="flex items-center gap-2 text-xs font-medium text-text-secondary mb-2">
                <input
                  type="checkbox"
                  checked={overrideImageStyle}
                  onChange={(e) => setOverrideImageStyle(e.target.checked)}
                  className="rounded border-border bg-bg-hover text-accent-blue focus:ring-accent-blue/30"
                  data-testid="brief-image-style-override"
                />
                Override image style for this brief
              </label>
              <p className="text-[11px] text-text-muted mb-2">
                Leave off to use your Profile default.
              </p>
              {overrideImageStyle && (
                <div className="grid grid-cols-1 gap-2">
                  <select
                    value={imageStyle}
                    onChange={(e) => handleImageStyleChange(e.target.value)}
                    className="w-full px-3 py-2 bg-bg-hover border border-border rounded-lg text-text-primary text-sm focus:outline-none focus:border-accent-blue transition-colors"
                    data-testid="brief-image-style-select"
                  >
                    {IMAGE_STYLE_OPTIONS.map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}
                  </select>
                  <select
                    value={imageSubstyle}
                    onChange={(e) => setImageSubstyle(e.target.value)}
                    className="w-full px-3 py-2 bg-bg-hover border border-border rounded-lg text-text-primary text-sm focus:outline-none focus:border-accent-blue transition-colors"
                    data-testid="brief-image-substyle-select"
                  >
                    {substyles.map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}
                  </select>
                </div>
              )}
            </div>

            <div className="h-px bg-border my-3" />

            <div>
              <label className="block text-xs font-medium text-text-secondary mb-1">
                Photos
                <span className="text-text-muted font-normal ml-1">max 5MB each</span>
              </label>
              <div className="flex gap-2.5 mt-1.5">
                <PhotoUpload
                  label="Cover"
                  hint="Featured image"
                  role="cover"
                  file={coverPhoto}
                  onSet={setCoverPhoto}
                  onRemove={() => setCoverPhoto(null)}
                />
                <PhotoUpload
                  label="Body"
                  hint="In-article image"
                  role="body"
                  file={bodyPhoto}
                  onSet={setBodyPhoto}
                  onRemove={() => setBodyPhoto(null)}
                />
              </div>
            </div>
          </div>
        </div>

        <div className="border-t border-border px-5 py-3 flex items-center justify-between bg-bg-deep/20">
          <p className="text-xs text-text-muted">
            We'll generate 3 article ideas for you to choose from.
          </p>
          <button
            type="submit"
            disabled={submitting || blocked}
            className="btn-primary px-6 py-2 text-sm"
            data-testid="submit-seeds"
          >
            {submitting ? 'Generating...' : 'Generate Ideas →'}
          </button>
        </div>
      </div>
    </form>
  );
}

// ── Blog Analysis Tab ────────────────────────────────────────────────

const ANALYSIS_STAGES = [
  { key: 'rss', label: 'Finding RSS feed...' },
  { key: 'posts', label: 'Reading posts...' },
  { key: 'style', label: 'Analyzing style & topics...' },
  { key: 'ideas', label: 'Brainstorming ideas...' },
  { key: 'complete', label: 'Ready!' },
];

function AnalysisProgress({ stage }) {
  const stageIndex = ANALYSIS_STAGES.findIndex(s => s.key === stage);

  return (
    <div className="space-y-2.5 py-2">
      {ANALYSIS_STAGES.map((s, i) => {
        const isDone = i < stageIndex;
        const isCurrent = i === stageIndex;

        return (
          <div
            key={s.key}
            className={`flex items-center gap-3 py-2 px-3 rounded-lg transition-all duration-300 ${
              isCurrent ? 'bg-accent-blue/10 border border-accent-blue/20' :
              isDone ? 'opacity-60' : 'opacity-30'
            }`}
          >
            <span className="text-base flex-shrink-0">
              {isDone ? '✅' : isCurrent ? (
                <svg className="w-4 h-4 animate-spin text-accent-blue" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="M12 2v4m0 12v4m-7.07-3.93l2.83-2.83m8.49-8.49l2.83-2.83M2 12h4m12 0h4m-3.93 7.07l-2.83-2.83M7.76 7.76L4.93 4.93" />
                </svg>
              ) : '○'}
            </span>
            <span className={`text-sm ${isCurrent ? 'text-text-primary font-medium' : 'text-text-secondary'}`}>
              {s.label}
            </span>
          </div>
        );
      })}
    </div>
  );
}

function BlogAnalysisTab({ ghostConnected, subscriptionActive }) {
  const navigate = useNavigate();

  // States: input | analyzing | ideas | submitting
  const [step, setStep] = useState('input');
  const [blogUrl, setBlogUrl] = useState('');
  const [analyzing, setAnalyzing] = useState(false);
  const [analysisStage, setAnalysisStage] = useState('rss');
  const [profileId, setProfileId] = useState('');
  const [ideas, setIdeas] = useState([]);
  const [selectedIdeas, setSelectedIdeas] = useState(new Set());
  const [submitting, setSubmitting] = useState(false);

  const blocked = !subscriptionActive || !ghostConnected;

  const handleAnalyze = async () => {
    let checkUrl = blogUrl.trim();
    if (!checkUrl) {
      toast.error('Please enter a blog URL');
      return;
    }
    if (!/^https?:\/\//i.test(checkUrl)) {
      checkUrl = `https://${checkUrl}`;
    }
    try { new URL(checkUrl); } catch {
      toast.error('Please enter a valid URL');
      return;
    }

    trackEvent('blog_analysis_started', { url: checkUrl });

    setStep('analyzing');
    setAnalyzing(true);
    setAnalysisStage('rss');

    // Skip staged UI delays in test env so the spec suite doesn't burn ~6.5s per flow test.
    const isTest = import.meta.env?.MODE === 'test';
    const delay = (ms) => (isTest ? Promise.resolve() : new Promise(resolve => setTimeout(resolve, ms)));

    // Fire analyze API in parallel with staged UI delays
    const apiPromise = api.post('/api/blog-analysis/analyze', { url: checkUrl });

    try {
      // Stage 1: Finding RSS feed
      await delay(1500);
      setAnalysisStage('posts');

      // Stage 2: Reading posts
      await delay(2000);
      setAnalysisStage('style');

      // Stage 3: Analyzing style & topics — await analyze API
      const [result] = await Promise.all([apiPromise, delay(1500)]);
      const profile = result.profile;

      trackEvent('blog_analysis_completed', {
        site_name: profile.site_name,
        topic_count: profile.topics?.length || 0,
      });

      // Stage 4: Brainstorming ideas — fire generate-ideas API
      setAnalysisStage('ideas');
      const ideasPromise = api.post('/api/blog-analysis/generate-ideas', {
        profile_id: profile.id,
        count: 10,
      });
      const [ideasResult] = await Promise.all([ideasPromise, delay(1500)]);

      setIdeas(ideasResult.ideas || []);
      setSelectedIdeas(new Set());
      setProfileId(profile.id);

      trackEvent('blog_ideas_generated', { idea_count: ideasResult.ideas?.length || 0 });

      // Stage 5: Complete — brief pause, then transition
      setAnalysisStage('complete');
      await delay(500);
      setStep('ideas');
    } catch {
      setStep('input');
      // toast already shown by api client
    } finally {
      setAnalyzing(false);
    }
  };

  const toggleIdea = (index) => {
    setSelectedIdeas(prev => {
      const next = new Set(prev);
      if (next.has(index)) {
        next.delete(index);
        return next;
      }
      if (next.size >= 3) {
        toast.error('You can pick up to 3 ideas');
        return prev;
      }
      next.add(index);
      return next;
    });
  };

  const handleSubmitIdeas = async () => {
    if (selectedIdeas.size === 0) return;
    setSubmitting(true);
    try {
      const selectedList = ideas
        .filter((_, i) => selectedIdeas.has(i))
        .map(idea => ({
          title: idea.title,
          angle: idea.angle,
          article_type: idea.article_type,
        }));

      const result = await api.post('/api/seeds/from-analysis', {
        profile_id: profileId,
        ideas: selectedList,
      }, { errorToastOptions: { duration: 12000 } });

      trackEvent('blog_analysis_submitted', {
        selected_count: selectedList.length,
        profile_id: profileId,
      });

      const articlesCreated = result.articles_created ?? 0;

      if (articlesCreated === 0 || result.budget_limited) {
        toast.error('No articles were created. Please try again or contact support.', { duration: 12000 });
        return;
      }

      toast.success(`${articlesCreated} article${articlesCreated === 1 ? '' : 's'} queued!`);
      navigate('/dashboard');
    } catch {
      // toast already shown
    } finally {
      setSubmitting(false);
    }
  };

  const handleRetry = () => {
    setStep('input');
    setProfileId('');
    setIdeas([]);
    setSelectedIdeas(new Set());
  };

  // Input state
  if (step === 'input') {
    return (
      <div className="glass-card overflow-hidden">
        <div className="p-5">
          <label className="block text-sm font-medium text-text-primary mb-1.5">
            Blog URL
          </label>
          <p className="text-xs text-text-muted mb-3">
            Enter any blog URL — we'll analyze its content, style, and find opportunities.
          </p>
          <div className="flex gap-3">
            <input
              type="text"
              value={blogUrl}
              onChange={(e) => setBlogUrl(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && !blocked && handleAnalyze()}
              placeholder="https://yourblog.com"
              className="flex-1 px-4 py-2.5 bg-bg-hover border border-border rounded-xl text-text-primary text-sm placeholder:text-text-muted focus:outline-none focus:border-accent-blue transition-colors"
              data-testid="analysis-url-input"
            />
            <button
              type="button"
              onClick={handleAnalyze}
              disabled={blocked || analyzing}
              className="btn-primary px-6 py-2.5 text-sm whitespace-nowrap"
              data-testid="analyze-button"
            >
              Analyze
            </button>
          </div>
        </div>
      </div>
    );
  }

  // Analyzing state
  if (step === 'analyzing') {
    return (
      <div className="glass-card overflow-hidden">
        <div className="p-5">
          <h3 className="text-sm font-semibold text-text-primary mb-3">Analyzing blog...</h3>
          <AnalysisProgress stage={analysisStage} />
        </div>
      </div>
    );
  }

  // Ideas state
  if (step === 'ideas') {
    return (
      <div className="glass-card overflow-hidden">
        <div className="p-5">
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-sm font-semibold text-text-primary">
              Article Ideas
            </h3>
            <span className="text-xs text-text-muted">
              {selectedIdeas.size} of 3 selected
            </span>
          </div>

          <div className="space-y-2">
            {ideas.map((idea, i) => {
              const isSelected = selectedIdeas.has(i);
              const typeClass = ARTICLE_TYPE_COLORS[idea.article_type] || 'bg-bg-hover text-text-muted border-border';

              return (
                <label
                  key={i}
                  className={`flex items-start gap-3 p-3 rounded-xl border cursor-pointer transition-all ${
                    isSelected
                      ? 'border-accent-blue/40 bg-accent-blue/5'
                      : 'border-border/50 hover:border-border-hover'
                  }`}
                >
                  <input
                    type="checkbox"
                    checked={isSelected}
                    onChange={() => toggleIdea(i)}
                    className="mt-1 shrink-0 accent-accent-blue"
                  />
                  <div className="min-w-0 flex-1">
                    <div className="flex items-start gap-2 mb-1">
                      <span className="text-sm font-medium text-text-primary leading-snug flex-1">
                        {idea.title}
                      </span>
                      <span className={`px-2 py-0.5 rounded-full text-[10px] font-medium border shrink-0 ${typeClass}`}>
                        {idea.article_type}
                      </span>
                    </div>
                    <p className="text-xs text-text-muted">{idea.angle}</p>
                  </div>
                </label>
              );
            })}
          </div>
        </div>

        <div className="border-t border-border px-5 py-3 flex items-center justify-between bg-bg-deep/20">
          <button
            type="button"
            onClick={handleRetry}
            className="text-xs text-text-muted hover:text-text-secondary transition-colors"
          >
            ← Analyze another
          </button>
          <button
            type="button"
            onClick={handleSubmitIdeas}
            disabled={selectedIdeas.size === 0 || submitting}
            className="btn-primary px-6 py-2 text-sm"
            data-testid="submit-analysis-ideas"
          >
            {submitting ? 'Submitting...' : `Generate →`}
          </button>
        </div>
      </div>
    );
  }

  return null;
}

// ── Main Component ───────────────────────────────────────────────────

export function NewBatch() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const [activeTab, setActiveTab] = useState('brief');

  // States: form | generating | review
  const [phase, setPhase] = useState('form');
  const [batchId, setBatchId] = useState(null);
  const [loadingMsg, setLoadingMsg] = useState(LOADING_MESSAGES[0]);
  const [ideasData, setIdeasData] = useState(null);
  const [approving, setApproving] = useState(false);

  const ghostConnected = user?.ghost_key_valid === 1;
  const subscriptionActive = ['active', 'trialing'].includes(user?.subscription_status);

  // Rotate loading messages
  useEffect(() => {
    if (phase !== 'generating') return;
    let idx = 0;
    const interval = setInterval(() => {
      idx = (idx + 1) % LOADING_MESSAGES.length;
      setLoadingMsg(LOADING_MESSAGES[idx]);
    }, 3500);
    return () => clearInterval(interval);
  }, [phase]);

  // Poll for ideas when generating
  useEffect(() => {
    if (phase !== 'generating' || !batchId) return;
    let cancelled = false;

    const poll = async () => {
      try {
        const data = await api.get(`/api/checkpoints/ideas/${batchId}`);
        if (cancelled) return;
        if (data.status === 'waiting_approval') {
          setIdeasData(data);
          setPhase('review');
          return;
        }
        // Keep polling
        if (!cancelled) setTimeout(poll, 3000);
      } catch {
        if (!cancelled) setTimeout(poll, 5000);
      }
    };

    const timer = setTimeout(poll, 3000);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [phase, batchId]);

  const handleGenerating = (newBatchId) => {
    setBatchId(newBatchId);
    setPhase('generating');
    setLoadingMsg(LOADING_MESSAGES[0]);
  };

  const handleStartOver = () => {
    setPhase('form');
    setBatchId(null);
    setIdeasData(null);
  };

  const handleApprove = async (approvedIdeas) => {
    setApproving(true);
    try {
      const result = await api.post('/api/checkpoints/ideas/approve', {
        batch_id: batchId,
        approved_ideas: approvedIdeas,
      }, { errorToastOptions: { duration: 12000 } });

      if (!result.articles_created || result.articles_created === 0 || result.budget_limited) {
        toast.error('No articles were created. Please try again or contact support.', { duration: 12000 });
        return;
      }

      trackEvent('article_reviewed', { batch_id: batchId, articles_created: result.articles_created });
      toast.success(`${result.articles_created} article${result.articles_created === 1 ? '' : 's'} queued!`);
      navigate('/dashboard');
    } catch {
      // 409 = hard cap or any error: api layer already showed the toast.
      // Stay on review screen and preserve selection (just don't navigate).
      return;
    } finally {
      setApproving(false);
    }
  };

  const handleRegenerate = async (feedback) => {
    const result = await api.post('/api/checkpoints/ideas/regenerate', {
      batch_id: batchId,
      feedback,
    });
    toast.success(`Regenerating ideas (${result.regen_count}/${result.max_regen} used)`);
    // Go back to generating state to poll for new ideas
    setPhase('generating');
    setIdeasData(null);
    setLoadingMsg(LOADING_MESSAGES[0]);
  };

  // Generating phase
  if (phase === 'generating') {
    return (
      <div className="px-6 py-5 max-w-[1080px] mx-auto">
        <div className="mb-4">
          <h1 className="text-xl font-bold text-text-primary">New Content</h1>
          <p className="text-sm text-text-secondary mt-0.5">Sit tight — your ideas are being crafted.</p>
        </div>
        <GeneratingScreen message={loadingMsg} />
        <div className="mt-3 text-center">
          <button
            onClick={handleStartOver}
            className="text-xs text-text-muted hover:text-text-secondary transition-colors"
          >
            ← Start over
          </button>
        </div>
      </div>
    );
  }

  // Review phase
  if (phase === 'review' && ideasData) {
    return (
      <div className="px-6 py-5 max-w-[1080px] mx-auto">
        <div className="mb-4">
          <h1 className="text-xl font-bold text-text-primary">New Content</h1>
          <p className="text-sm text-text-secondary mt-0.5">Your ideas are ready — pick the ones you want written.</p>
        </div>
        <InlineIdeaReview
          ideas={ideasData.ideas || []}
          regenCount={ideasData.regen_count || 0}
          maxRegen={ideasData.max_regen || 3}
          hasCustomImages={ideasData.has_custom_images || false}
          onApprove={handleApprove}
          onRegenerate={handleRegenerate}
          onStartOver={handleStartOver}
          approving={approving}
        />
      </div>
    );
  }

  // Form phase (default)
  return (
    <div className="px-6 py-5 max-w-[1080px] mx-auto">
      {/* Header */}
      <div className="mb-4">
        <h1 className="text-xl font-bold text-text-primary">New Content</h1>
        <p className="text-sm text-text-secondary mt-0.5">
          {activeTab === 'brief'
            ? 'Describe your article — we\'ll generate ideas from your brief.'
            : 'Analyze a blog and pick up to 3 ideas — we\'ll write them all.'}
        </p>
      </div>

      {/* Warnings */}
      {!subscriptionActive && (
        <div className="glass-card p-3 mb-3 border-accent-blue/30" data-testid="subscription-warning">
          <p className="text-accent-blue text-sm font-medium">
            🔒 Subscription required —{' '}
            <a
              href="/dashboard/settings"
              onClick={(e) => { e.preventDefault(); navigate('/dashboard/settings'); }}
              className="hover:underline"
            >
              Start your 7-day free trial →
            </a>
          </p>
        </div>
      )}
      {subscriptionActive && !ghostConnected && (
        <div className="glass-card p-3 mb-3 border-warning/30" data-testid="ghost-warning">
          <p className="text-warning text-sm font-medium">
            ⚠️ Connect your Ghost blog in Settings before submitting.
          </p>
        </div>
      )}

      {/* Tabs */}
      <div className="flex gap-1 mb-0">
        {[
          { key: 'brief', label: 'Content Brief' },
          { key: 'analysis', label: 'Blog Analysis' },
        ].map((tab) => (
          <button
            key={tab.key}
            onClick={() => setActiveTab(tab.key)}
            className={`px-5 py-2 text-sm font-medium transition-all rounded-t-lg border border-b-0 ${
              activeTab === tab.key
                ? 'bg-bg-hover text-text-primary border-border/40 relative after:absolute after:bottom-[-1px] after:left-0 after:right-0 after:h-[1px] after:bg-bg-hover'
                : 'bg-transparent text-text-muted border-transparent hover:text-text-secondary hover:bg-bg-hover/50'
            }`}
          >
            {tab.label}
          </button>
        ))}
        <div className="flex-1 border-b border-border/40" />
      </div>
      <div className="border-x border-b border-border/40 rounded-b-lg">

      {/* Tab content */}
      {activeTab === 'brief' ? (
        <ContentBriefTab
          ghostConnected={ghostConnected}
          subscriptionActive={subscriptionActive}
          onGenerating={handleGenerating}
        />
      ) : (
        <BlogAnalysisTab
          ghostConnected={ghostConnected}
          subscriptionActive={subscriptionActive}
        />
      )}
      </div>
    </div>
  );
}
