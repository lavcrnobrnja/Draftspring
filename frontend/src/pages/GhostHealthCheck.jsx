import { useState, useCallback } from 'react';
import { Link } from 'react-router-dom';
import { trackEvent } from '../lib/posthog';

function isValidUrl(str) {
  try {
    const u = new URL(str);
    return u.protocol === 'http:' || u.protocol === 'https:';
  } catch {
    return false;
  }
}

function getDomain(url) {
  try {
    return new URL(url).hostname;
  } catch {
    return url;
  }
}

function ScoreRing({ score }) {
  const size = 160;
  const stroke = 10;
  const radius = (size - stroke) / 2;
  const circumference = 2 * Math.PI * radius;
  const offset = circumference - (score / 100) * circumference;
  const color = score >= 80 ? '#10B981' : score >= 50 ? '#F59E0B' : '#EF4444';

  return (
    <div className="relative inline-flex items-center justify-center" style={{ width: size, height: size }}>
      <svg width={size} height={size} className="-rotate-90">
        <circle cx={size / 2} cy={size / 2} r={radius} fill="none" stroke="rgba(255,255,255,0.08)" strokeWidth={stroke} />
        <circle
          cx={size / 2} cy={size / 2} r={radius} fill="none"
          stroke={color} strokeWidth={stroke} strokeLinecap="round"
          strokeDasharray={circumference} strokeDashoffset={offset}
          className="transition-all duration-1000 ease-out"
        />
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center">
        <span className="text-5xl font-extrabold" style={{ color }}>{score}</span>
        <span className="text-xs text-text-muted mt-0.5">/ 100</span>
      </div>
    </div>
  );
}

function CheckItem({ label, status, detail }) {
  const icons = { pass: '✅', fail: '❌', warning: '⚠️' };
  return (
    <div className="flex items-start gap-3 py-2">
      <span className="text-lg flex-shrink-0 mt-0.5">{icons[status] || '—'}</span>
      <div>
        <div className="text-sm font-medium text-text-primary">{label}</div>
        {detail && <div className="text-xs text-text-muted mt-0.5">{detail}</div>}
      </div>
    </div>
  );
}

export function GhostHealthCheck() {
  const [url, setUrl] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [result, setResult] = useState(null);
  const [email, setEmail] = useState('');
  const [emailStatus, setEmailStatus] = useState(''); // '', 'sending', 'sent', 'error'
  const [emailCaptured, setEmailCaptured] = useState(false);
  // Step: 'input' → 'email-gate' → 'loading' → 'results'
  const [step, setStep] = useState('input');
  // Store the validated URL for use after email capture
  const [pendingUrl, setPendingUrl] = useState('');

  const runHealthCheck = useCallback(async (checkUrl, reportEmail = '') => {
    setLoading(true);
    setStep('loading');
    setError('');

    try {
      const params = new URLSearchParams({ url: checkUrl });
      if (reportEmail) params.set('email', reportEmail);
      const resp = await fetch(`/api/v1/tools/health-check?${params.toString()}`);
      const data = await resp.json();

      if (!resp.ok) {
        setError(data.detail || 'Something went wrong. Please try again.');
        setStep('input');
        return;
      }

      setResult(data);
      setStep('results');
      trackEvent('health_check_completed', {
        url_domain: data.url_domain,
        score: data.score,
        is_ghost: data.is_ghost,
      });
    } catch {
      setError('Could not reach the server. Please try again.');
      setStep('input');
    } finally {
      setLoading(false);
    }
  }, []);

  const handleCheck = useCallback(() => {
    setError('');
    setResult(null);

    const trimmed = url.trim();
    if (!trimmed) {
      setError('Please enter a URL');
      return;
    }

    // Auto-prepend https:// if no protocol
    const checkUrl = /^https?:\/\//i.test(trimmed) ? trimmed : `https://${trimmed}`;
    if (!isValidUrl(checkUrl)) {
      setError('Please enter a valid URL (e.g., https://yourblog.com)');
      return;
    }

    trackEvent('health_check_started', { url_domain: getDomain(checkUrl) });
    setPendingUrl(checkUrl);

    if (emailCaptured) {
      // Already captured email, go straight to check
      runHealthCheck(checkUrl, email);
    } else {
      // Show email gate
      setStep('email-gate');
    }
  }, [url, email, emailCaptured, runHealthCheck]);

  const handleSubscribe = useCallback(async () => {
    if (!email || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) return;
    setEmailStatus('sending');
    trackEvent('health_check_email_captured', { url_domain: getDomain(pendingUrl) });

    try {
      const resp = await fetch('/api/v1/tools/health-check/subscribe', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          email,
          url_domain: getDomain(pendingUrl),
          score: 0, // Score not known yet
        }),
      });
      if (resp.ok) {
        setEmailStatus('sent');
        setEmailCaptured(true);
        // Now run the actual health check
        runHealthCheck(pendingUrl, email);
      } else {
        setEmailStatus('error');
      }
    } catch {
      setEmailStatus('error');
    }
  }, [email, pendingUrl, runHealthCheck]);

  const checks = result?.checks || {};
  const postPages = checks.post_pages || {};

  const checkItems = result ? [
    {
      label: 'Ghost Platform',
      status: checks.is_ghost ? 'pass' : 'fail',
      detail: checks.is_ghost ? 'Running on Ghost CMS' : 'Not a Ghost blog',
    },
    {
      label: 'Recent Activity (30 days)',
      status: checks.posts_last_30d > 0 ? 'pass' : checks.posts_last_90d > 0 ? 'warning' : 'fail',
      detail: checks.posts_last_30d > 0
        ? `${checks.posts_last_30d} post${checks.posts_last_30d !== 1 ? 's' : ''} in the last 30 days`
        : checks.posts_last_90d > 0
          ? `No posts in 30 days, but ${checks.posts_last_90d} in the last 90 days`
          : 'No recent posts detected',
    },
    {
      label: 'Publishing Frequency',
      status: checks.posts_per_month >= 4 ? 'pass' : checks.posts_per_month >= 1 ? 'warning' : 'fail',
      detail: checks.posts_per_month > 0
        ? `${checks.posts_per_month} posts/month average`
        : 'No publishing frequency data',
    },
    {
      label: 'Total Content',
      status: checks.total_posts >= 20 ? 'pass' : checks.total_posts >= 5 ? 'warning' : 'fail',
      detail: checks.total_posts > 0
        ? `${checks.total_posts} posts found${checks.has_sitemap ? ' (via sitemap)' : ''}`
        : 'No posts found',
    },
    {
      label: 'Sitemap',
      status: checks.has_sitemap ? 'pass' : 'fail',
      detail: checks.has_sitemap
        ? `Sitemap found${checks.sitemap_post_count ? ` with ${checks.sitemap_post_count} posts` : ''}`
        : 'No sitemap detected',
    },
    {
      label: 'Homepage Title',
      status: checks.title_length >= 50 && checks.title_length <= 60 ? 'pass'
        : checks.title_length > 0 ? 'warning' : 'fail',
      detail: checks.title_length > 0
        ? `${checks.title_length} characters (optimal: 50-60)`
        : 'No title found',
    },
    {
      label: 'Homepage Meta Description',
      status: checks.has_meta_description ? 'pass' : 'fail',
      detail: checks.has_meta_description ? 'Present' : 'Missing — hurts search appearance',
    },
    {
      label: 'Open Graph Image',
      status: checks.has_og_image ? 'pass' : 'fail',
      detail: checks.has_og_image ? 'Present — social shares will look great' : 'Missing — social shares won\'t have a preview image',
    },
    {
      label: 'Structured Data',
      status: checks.has_structured_data ? 'pass' : 'fail',
      detail: checks.has_structured_data ? 'JSON-LD or schema.org markup found' : 'No structured data — limits rich search results',
    },
  ] : [];

  // Post-level check items
  const postCheckItems = result && postPages.pages_checked > 0 ? [
    {
      label: 'Post Meta Descriptions',
      status: postPages.meta_description_pct >= 80 ? 'pass' : postPages.meta_description_pct >= 50 ? 'warning' : 'fail',
      detail: `${postPages.meta_description_pct}% of recent posts have meta descriptions`,
    },
    {
      label: 'Post OG Images',
      status: postPages.og_image_pct >= 80 ? 'pass' : postPages.og_image_pct >= 50 ? 'warning' : 'fail',
      detail: `${postPages.og_image_pct}% of recent posts have Open Graph images`,
    },
    {
      label: 'Post Length',
      status: postPages.avg_word_count >= 1000 ? 'pass' : postPages.avg_word_count >= 500 ? 'warning' : 'fail',
      detail: `Average ${postPages.avg_word_count.toLocaleString()} words per post`,
    },
    {
      label: 'Internal Linking',
      status: postPages.avg_internal_links >= 3 ? 'pass' : postPages.avg_internal_links >= 1 ? 'warning' : 'fail',
      detail: `Average ${postPages.avg_internal_links} internal links per post`,
    },
    {
      label: 'Image Alt Text',
      status: postPages.alt_text_pct >= 80 ? 'pass' : postPages.alt_text_pct >= 50 ? 'warning' : 'fail',
      detail: postPages.total_images_checked > 0
        ? `${postPages.alt_text_pct}% of images have alt text (${postPages.images_with_alt}/${postPages.total_images_checked})`
        : 'No content images found',
    },
    {
      label: 'Post Structured Data',
      status: postPages.structured_data_pct >= 80 ? 'pass' : postPages.structured_data_pct >= 50 ? 'warning' : 'fail',
      detail: `${postPages.structured_data_pct}% of recent posts have structured data`,
    },
  ] : [];

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
            How healthy is your Ghost blog?
          </h1>
          <p className="text-text-secondary text-base sm:text-lg max-w-lg mx-auto">
            Free instant analysis of your Ghost blog's content health, publishing activity, and SEO fundamentals.
          </p>
        </div>

        {/* Input */}
        <div className="glass-card p-6 sm:p-8 mb-8">
          <div className="flex flex-col sm:flex-row gap-3">
            <input
              type="url"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && !loading && step === 'input' && handleCheck()}
              placeholder="https://yourblog.com"
              className="flex-1 px-4 py-3.5 bg-bg-hover border border-border rounded-xl text-text-primary placeholder:text-text-muted focus:outline-none focus:border-accent-blue transition-colors text-base"
              disabled={loading || step === 'email-gate'}
              autoFocus
              data-testid="health-check-url-input"
            />
            <button
              onClick={handleCheck}
              disabled={loading || step === 'email-gate'}
              className="px-8 py-3.5 rounded-xl font-semibold text-white gradient-bg shadow-lg shadow-accent-blue/20 hover:shadow-accent-blue/30 transition-all disabled:opacity-50 disabled:cursor-not-allowed whitespace-nowrap"
              data-testid="health-check-submit"
            >
              {loading ? (
                <span className="flex items-center gap-2">
                  <svg className="w-4 h-4 animate-spin" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                    <path d="M12 2v4m0 12v4m-7.07-3.93l2.83-2.83m8.49-8.49l2.83-2.83M2 12h4m12 0h4m-3.93 7.07l-2.83-2.83M7.76 7.76L4.93 4.93" />
                  </svg>
                  Checking...
                </span>
              ) : 'Check Health'}
            </button>
          </div>
          {error && (
            <p className="mt-3 text-sm text-error" data-testid="health-check-error">{error}</p>
          )}
        </div>

        {/* Email Gate — shown after clicking Check, before results */}
        {step === 'email-gate' && (
          <div className="glass-card p-6 sm:p-8 mb-8 text-center" data-testid="health-check-email-gate">
            <div className="mb-4">
              <span className="text-4xl">📊</span>
            </div>
            <h3 className="text-xl font-bold text-text-primary mb-2">
              Enter your email to unlock your results
            </h3>
            <p className="text-text-secondary text-sm mb-6 max-w-md mx-auto">
              Get instant access to your health score plus occasional Ghost content tips. Unsubscribe anytime.
            </p>
            <div className="flex flex-col sm:flex-row gap-3 max-w-md mx-auto">
              <input
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && handleSubscribe()}
                placeholder="you@example.com"
                className="flex-1 px-4 py-3.5 bg-bg-hover border border-border rounded-xl text-text-primary placeholder:text-text-muted focus:outline-none focus:border-accent-blue transition-colors text-base"
                disabled={emailStatus === 'sending'}
                autoFocus
                data-testid="health-check-email-input"
              />
              <button
                onClick={handleSubscribe}
                disabled={emailStatus === 'sending' || !email}
                className="px-8 py-3.5 rounded-xl font-semibold text-white gradient-bg shadow-lg shadow-accent-blue/20 hover:shadow-accent-blue/30 transition-all disabled:opacity-50 disabled:cursor-not-allowed whitespace-nowrap"
                data-testid="health-check-email-submit"
              >
                {emailStatus === 'sending' ? 'Submitting...' : 'Show My Results'}
              </button>
            </div>
            {emailStatus === 'error' && (
              <p className="text-error text-xs mt-3">Something went wrong. Please try again.</p>
            )}
            <p className="text-text-muted text-xs mt-4">
              No spam, ever. Unsubscribe anytime.
            </p>
          </div>
        )}

        {/* Loading skeleton */}
        {loading && (
          <div className="glass-card p-8 mb-8" data-testid="health-check-loading">
            <div className="flex flex-col items-center gap-4">
              <div className="w-40 h-40 rounded-full bg-bg-hover animate-pulse" />
              <div className="w-48 h-4 bg-bg-hover rounded animate-pulse" />
              <div className="w-full space-y-3 mt-4">
                {[1, 2, 3, 4, 5].map(i => (
                  <div key={i} className="h-10 bg-bg-hover rounded-lg animate-pulse" />
                ))}
              </div>
            </div>
          </div>
        )}

        {/* Results */}
        {step === 'results' && result && !loading && (
          <>
            <div className="glass-card p-6 sm:p-8 mb-8" data-testid="health-check-results">
              {/* Score */}
              <div className="flex flex-col items-center mb-8">
                <ScoreRing score={result.score} />
                <p className="text-text-secondary text-sm mt-3">
                  {result.score >= 80 ? 'Great shape! A few tweaks could make it perfect.' :
                   result.score >= 50 ? 'Decent foundation. There\'s room for improvement.' :
                   'Needs work. Follow the recommendations below.'}
                </p>
                <p className="text-text-muted text-xs mt-1">{result.url_domain}</p>
              </div>

              {/* Site-level checks */}
              <div className="border-t border-border pt-4">
                <h3 className="text-sm font-semibold text-text-muted uppercase tracking-wider mb-3">Site-Level Checks</h3>
                <div className="divide-y divide-border/50">
                  {checkItems.map((item, i) => (
                    <CheckItem key={i} {...item} />
                  ))}
                </div>
              </div>

              {/* Post-level checks */}
              {postCheckItems.length > 0 && (
                <div className="mt-6 border-t border-border pt-4">
                  <h3 className="text-sm font-semibold text-text-muted uppercase tracking-wider mb-3">
                    Post-Level Analysis
                    <span className="text-xs font-normal ml-2">({postPages.pages_checked} recent posts checked)</span>
                  </h3>
                  <div className="divide-y divide-border/50">
                    {postCheckItems.map((item, i) => (
                      <CheckItem key={i} {...item} />
                    ))}
                  </div>
                </div>
              )}

              {/* Recommendations */}
              {result.recommendations?.length > 0 && (
                <div className="mt-6 border-t border-border pt-4">
                  <h3 className="text-sm font-semibold text-text-muted uppercase tracking-wider mb-3">Recommendations</h3>
                  <ul className="space-y-2">
                    {result.recommendations.map((rec, i) => (
                      <li key={i} className="flex items-start gap-2 text-sm text-text-secondary">
                        <span className="text-accent-blue mt-0.5">→</span>
                        <span>{rec}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </div>

            {/* DraftSpring CTA */}
            <div className="glass-card p-6 sm:p-8 mb-8 text-center" data-testid="health-check-cta">
              <h3 className="text-xl font-bold text-text-primary mb-2">
                Want a blog that publishes itself?
              </h3>
              <p className="text-text-secondary text-sm mb-6 max-w-md mx-auto">
                DraftSpring publishes 8 SEO-optimized articles to your Ghost blog automatically. Research, writing, images, and publishing — all on autopilot.
              </p>
              <Link
                to="/signup?utm_source=health-check&utm_medium=tool&utm_campaign=health-check-tool"
                onClick={() => trackEvent('health_check_cta_clicked')}
                className="inline-block px-8 py-3.5 rounded-xl font-semibold text-white gradient-bg shadow-lg shadow-accent-blue/20 hover:shadow-accent-blue/30 transition-all"
              >
                Start Free 7-Day Trial →
              </Link>
            </div>
          </>
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
