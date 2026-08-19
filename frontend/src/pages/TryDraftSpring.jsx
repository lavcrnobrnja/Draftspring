import { useState, useCallback, useEffect, useRef } from 'react';
import { Link } from 'react-router-dom';

function isValidUrl(str) {
  try {
    const u = new URL(str);
    return u.protocol === 'http:' || u.protocol === 'https:';
  } catch {
    return false;
  }
}

const STAGE_MESSAGES = {
  pending: 'Starting...',
  scanning: 'Scanning your blog...',
  analyzing: 'Analyzing your writing style...',
  ideating: 'Choosing a topic...',
  drafting: 'Writing your article...',
  imaging: 'Generating cover image...',
  sending: 'Sending to your inbox...',
};

const STAGE_EMOJIS = {
  pending: '⏳',
  scanning: '🔍',
  analyzing: '📊',
  ideating: '💡',
  drafting: '✍️',
  imaging: '🎨',
  sending: '📧',
};

const STAGE_ORDER = ['pending', 'scanning', 'analyzing', 'ideating', 'drafting', 'imaging', 'sending', 'complete'];

function ProgressStages({ currentStatus, stageMessage }) {
  const currentIndex = STAGE_ORDER.indexOf(currentStatus);

  return (
    <div className="space-y-3">
      {STAGE_ORDER.filter(s => s !== 'complete' && s !== 'pending').map((stage, i) => {
        const stageIndex = STAGE_ORDER.indexOf(stage);
        const isDone = currentIndex > stageIndex;
        const isCurrent = currentIndex === stageIndex;

        return (
          <div
            key={stage}
            className={`flex items-center gap-3 py-2 px-3 rounded-lg transition-all duration-300 ${
              isCurrent ? 'bg-accent-blue/10 border border-accent-blue/20' :
              isDone ? 'opacity-60' : 'opacity-30'
            }`}
          >
            <span className="text-lg flex-shrink-0">
              {isDone ? '✅' : isCurrent ? STAGE_EMOJIS[stage] : '○'}
            </span>
            <span className={`text-sm ${isCurrent ? 'text-text-primary font-medium' : 'text-text-secondary'}`}>
              {isCurrent ? (stageMessage || STAGE_MESSAGES[stage]) : STAGE_MESSAGES[stage]}
            </span>
            {isCurrent && (
              <svg className="w-4 h-4 animate-spin ml-auto text-accent-blue" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M12 2v4m0 12v4m-7.07-3.93l2.83-2.83m8.49-8.49l2.83-2.83M2 12h4m12 0h4m-3.93 7.07l-2.83-2.83M7.76 7.76L4.93 4.93" />
              </svg>
            )}
          </div>
        );
      })}
    </div>
  );
}

export function TryDraftSpring() {
  const [url, setUrl] = useState('');
  const [email, setEmail] = useState('');
  const [step, setStep] = useState('input'); // input | processing | preview | error | already-used
  const [error, setError] = useState('');
  const [taskId, setTaskId] = useState(null);
  const [taskStatus, setTaskStatus] = useState('pending');
  const [stageMessage, setStageMessage] = useState('');
  const [result, setResult] = useState(null);
  const [submitting, setSubmitting] = useState(false);
  const taskStatusRef = useRef('pending');
  const pollRef = useRef(null);
  const turnstileRef = useRef(null);
  const [turnstileToken, setTurnstileToken] = useState('');
  const turnstileWidgetId = useRef(null);

  // Clean up polling on unmount
  useEffect(() => {
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, []);

  // Load Turnstile script
  useEffect(() => {
    const siteKey = import.meta.env.VITE_TURNSTILE_SITE_KEY;
    if (!siteKey) return;
    if (document.querySelector('script[src*="turnstile"]')) return;
    const script = document.createElement('script');
    script.src = 'https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit';
    script.async = true;
    document.head.appendChild(script);
  }, []);

  // Render Turnstile widget — reset when returning to input step
  useEffect(() => {
    const siteKey = import.meta.env.VITE_TURNSTILE_SITE_KEY;
    if (!siteKey || step !== 'input') return;

    // Clean up previous widget (e.g. after error → retry)
    if (turnstileWidgetId.current !== null && window.turnstile) {
      try { window.turnstile.remove(turnstileWidgetId.current); } catch (_) {}
      turnstileWidgetId.current = null;
      setTurnstileToken('');
    }

    const renderWidget = () => {
      if (window.turnstile && turnstileRef.current && turnstileWidgetId.current === null) {
        turnstileWidgetId.current = window.turnstile.render(turnstileRef.current, {
          sitekey: siteKey,
          theme: 'dark',
          callback: (token) => setTurnstileToken(token),
          'expired-callback': () => setTurnstileToken(''),
        });
      }
    };

    if (window.turnstile) {
      renderWidget();
    } else {
      const interval = setInterval(() => {
        if (window.turnstile) {
          clearInterval(interval);
          renderWidget();
        }
      }, 200);
      const timeout = setTimeout(() => clearInterval(interval), 10000);
      return () => { clearInterval(interval); clearTimeout(timeout); };
    }
  }, [step]); // Re-render when step changes back to input

  const urlRef = useRef('');

  const startPolling = useCallback((id) => {
    if (pollRef.current) clearInterval(pollRef.current);

    pollRef.current = setInterval(async () => {
      try {
        const resp = await fetch(`/api/v1/tools/try-draftspring/${id}/status`);
        const data = await resp.json();

        if (!resp.ok) {
          clearInterval(pollRef.current);
          setError('Something went wrong. Please try again.');
          setStep('error');
          return;
        }

        const prevStatus = taskStatusRef.current;
        setTaskStatus(data.status);
        setStageMessage(data.stage_message || '');
        taskStatusRef.current = data.status;

        // Fire PostHog events on stage transitions
        if (prevStatus === 'analyzing' && data.status !== 'analyzing') {
          window.posthog?.capture('blog_analyzed', { url: urlRef.current });
        }
        if (prevStatus === 'drafting' && data.status !== 'drafting') {
          window.posthog?.capture('article_generated');
        }

        if (data.status === 'complete') {
          clearInterval(pollRef.current);
          setResult(data.result);
          setStep('preview');
          window.posthog?.capture('try_draftspring_completed', {
            title: data.result?.title,
          });
        } else if (data.status === 'failed') {
          clearInterval(pollRef.current);
          setError(data.error || 'Something went wrong generating your article. Please try again.');
          setStep('error');
          window.posthog?.capture('try_draftspring_failed', {
            error: data.error,
          });
        }
      } catch {
        // Network error — keep polling
      }
    }, 3000);
  }, []);

  const handleSubmit = useCallback(async () => {
    setError('');
    const trimmedUrl = url.trim();
    const trimmedEmail = email.trim();

    if (!trimmedUrl) {
      setError('Please enter your blog URL');
      return;
    }
    if (!trimmedEmail) {
      setError('Please enter your email address');
      return;
    }

    const checkUrl = /^https?:\/\//i.test(trimmedUrl) ? trimmedUrl : `https://${trimmedUrl}`;
    if (!isValidUrl(checkUrl)) {
      setError('Please enter a valid URL (e.g., https://yourblog.com)');
      return;
    }

    setSubmitting(true);
    urlRef.current = checkUrl;
    window.posthog?.capture('try_draftspring_started', { url: checkUrl, email: trimmedEmail });

    try {
      const resp = await fetch('/api/v1/tools/try-draftspring', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url: checkUrl, email: trimmedEmail, cf_turnstile_token: turnstileToken }),
      });
      const data = await resp.json();

      if (resp.status === 409) {
        setStep('already-used');
        setSubmitting(false);
        return;
      }

      if (resp.status === 429) {
        setError(data.detail || 'Too many requests. Please try again later.');
        setSubmitting(false);
        return;
      }

      if (!resp.ok) {
        setError(data.detail || 'Something went wrong. Please try again.');
        setSubmitting(false);
        return;
      }

      // Reset Turnstile widget
      if (window.turnstile && turnstileWidgetId.current !== null) {
        window.turnstile.reset(turnstileWidgetId.current);
        setTurnstileToken('');
      }

      setTaskId(data.task_id);
      setTaskStatus('pending');
      setStep('processing');
      startPolling(data.task_id);
    } catch {
      setError('Could not reach the server. Please try again.');
    } finally {
      setSubmitting(false);
    }
  }, [url, email, startPolling]);

  return (
    <div className="min-h-screen bg-bg-deep">
      {/* Background effects */}
      <div className="fixed top-[-200px] left-[-200px] w-[600px] h-[600px] bg-[radial-gradient(circle,rgba(59,130,246,0.12),transparent_70%)] blur-[80px] pointer-events-none" />
      <div className="fixed top-[100px] right-[-200px] w-[500px] h-[500px] bg-[radial-gradient(circle,rgba(139,92,246,0.08),transparent_70%)] blur-[80px] pointer-events-none" />

      <div className="relative z-10 max-w-2xl mx-auto px-4 py-12 sm:py-20">
        {/* Hero */}
        <div className="text-center mb-10">
          <Link to="/login" className="inline-block mb-6">
            <span className="text-2xl font-extrabold gradient-text">DraftSpring</span>
          </Link>
          <h1 className="text-3xl sm:text-4xl font-extrabold text-text-primary mb-3">
            See what DraftSpring writes for your blog
          </h1>
          <p className="text-text-secondary text-base sm:text-lg max-w-lg mx-auto">
            Enter your Ghost blog URL and email. We'll analyze your writing style, generate a full article in your voice, and <strong className="text-text-primary">send it straight to your inbox</strong>. Free, no signup required.
          </p>
        </div>

        {/* Input Form */}
        {step === 'input' && (
          <div className="glass-card p-6 sm:p-8 mb-8" data-testid="try-ds-form">
            <div className="space-y-3">
              <input
                type="url"
                value={url}
                onChange={(e) => setUrl(e.target.value)}
                placeholder="https://yourblog.com"
                className="w-full px-4 py-3.5 bg-bg-hover border border-border rounded-xl text-text-primary placeholder:text-text-muted focus:outline-none focus:border-accent-blue transition-colors text-base"
                disabled={submitting}
                autoFocus
                data-testid="try-ds-url-input"
              />
              <input
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && !submitting && handleSubmit()}
                placeholder="you@example.com"
                className="w-full px-4 py-3.5 bg-bg-hover border border-border rounded-xl text-text-primary placeholder:text-text-muted focus:outline-none focus:border-accent-blue transition-colors text-base"
                disabled={submitting}
                data-testid="try-ds-email-input"
              />
              {import.meta.env.VITE_TURNSTILE_SITE_KEY && (
                <div ref={turnstileRef} className="mb-4 flex justify-center" />
              )}
              <button
                onClick={handleSubmit}
                disabled={submitting || (!!import.meta.env.VITE_TURNSTILE_SITE_KEY && !turnstileToken)}
                className="w-full px-8 py-3.5 rounded-xl font-semibold text-white gradient-bg shadow-lg shadow-accent-blue/20 hover:shadow-accent-blue/30 transition-all disabled:opacity-50 disabled:cursor-not-allowed"
                data-testid="try-ds-submit"
              >
                {submitting ? (
                  <span className="flex items-center justify-center gap-2">
                    <svg className="w-4 h-4 animate-spin" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                      <path d="M12 2v4m0 12v4m-7.07-3.93l2.83-2.83m8.49-8.49l2.83-2.83M2 12h4m12 0h4m-3.93 7.07l-2.83-2.83M7.76 7.76L4.93 4.93" />
                    </svg>
                    Submitting...
                  </span>
                ) : 'Generate My Article ✨'}
              </button>
            </div>
            {error && (
              <p className="mt-3 text-sm text-error" data-testid="try-ds-error">{error}</p>
            )}
            <p className="text-text-muted text-xs mt-4 text-center">
              📧 The full article will be emailed to you. You'll see a preview here when it's ready. Ghost blogs only.
            </p>
          </div>
        )}

        {/* Processing */}
        {step === 'processing' && (
          <div className="glass-card p-6 sm:p-8 mb-8" data-testid="try-ds-processing">
            <div className="text-center mb-6">
              <h2 className="text-xl font-bold text-text-primary mb-2">
                Crafting your article...
              </h2>
              <p className="text-text-secondary text-sm">
                This usually takes 2–3 minutes. You'll see a preview here — the <strong className="text-text-primary">full article will be sent to your email</strong>.
              </p>
            </div>
            <ProgressStages currentStatus={taskStatus} stageMessage={stageMessage} />
          </div>
        )}

        {/* Preview */}
        {step === 'preview' && result && (
          <div className="glass-card p-0 mb-8 overflow-hidden" data-testid="try-ds-preview">
            {result.cover_image_url && (
              <img
                src={result.cover_image_url}
                alt="Article cover"
                className="w-full h-48 sm:h-64 object-cover"
              />
            )}
            <div className="p-6 sm:p-8">
              <h2 className="text-2xl font-bold text-text-primary mb-4">
                {result.title}
              </h2>
              {/* Preview text with fade */}
              <div className="relative">
                <p className="text-text-secondary text-base leading-relaxed">
                  {result.preview}
                </p>
                <div className="absolute bottom-0 left-0 right-0 h-16 bg-gradient-to-t from-bg-card to-transparent" />
              </div>

              {/* CTA */}
              <div className="mt-8 pt-6 border-t border-border text-center">
                <div className="mb-2">
                  <span className="text-3xl">📬</span>
                </div>
                <h3 className="text-lg font-bold text-text-primary mb-2">
                  The full article is in your inbox
                </h3>
                <p className="text-text-secondary text-sm mb-6 max-w-md mx-auto">
                  DraftSpring does this 8 times a month for $9. Research, writing, images, and publishing — all on autopilot.
                </p>
                <Link
                  to="/login?utm_source=try-draftspring&utm_medium=tool&utm_campaign=lead-magnet"
                  className="inline-block px-8 py-3.5 rounded-xl font-semibold text-white gradient-bg shadow-lg shadow-accent-blue/20 hover:shadow-accent-blue/30 transition-all"
                >
                  Start Free 7-Day Trial →
                </Link>
              </div>
            </div>
          </div>
        )}

        {/* Error */}
        {step === 'error' && (
          <div className="glass-card p-6 sm:p-8 mb-8 text-center" data-testid="try-ds-error-state">
            <div className="mb-4">
              <span className="text-4xl">😔</span>
            </div>
            <h3 className="text-xl font-bold text-text-primary mb-2">
              Something went wrong
            </h3>
            <p className="text-text-secondary text-sm mb-6">
              {error}
            </p>
            <button
              onClick={() => { setStep('input'); setError(''); }}
              className="px-8 py-3.5 rounded-xl font-semibold text-white gradient-bg shadow-lg shadow-accent-blue/20 hover:shadow-accent-blue/30 transition-all"
            >
              Try Again
            </button>
          </div>
        )}

        {/* Already Used */}
        {step === 'already-used' && (
          <div className="glass-card p-6 sm:p-8 mb-8 text-center" data-testid="try-ds-already-used">
            <div className="mb-4">
              <span className="text-4xl">📬</span>
            </div>
            <h3 className="text-xl font-bold text-text-primary mb-2">
              You've already generated a sample article
            </h3>
            <p className="text-text-secondary text-sm mb-6">
              Check your inbox! The article we wrote for your blog should already be there.
            </p>
            <Link
              to="/login?utm_source=try-draftspring&utm_medium=tool&utm_campaign=lead-magnet"
              className="inline-block px-8 py-3.5 rounded-xl font-semibold text-white gradient-bg shadow-lg shadow-accent-blue/20 hover:shadow-accent-blue/30 transition-all"
            >
              Ready to automate? Start free →
            </Link>
          </div>
        )}

        {/* Footer */}
        <div className="text-center mt-12">
          <p className="text-text-muted text-xs">
            Free tool by <Link to="/login" className="text-accent-blue hover:underline">DraftSpring</Link> — Content automation for Ghost blogs
          </p>
        </div>
      </div>
    </div>
  );
}
