import { useState, useCallback, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../hooks/useAuth';
import { api } from '../lib/api';
import { trackEvent } from '../lib/posthog';
import toast from 'react-hot-toast';

const ACTIVE_STATUSES = ['active', 'trialing'];

export function Subscribe() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const [loading, setLoading] = useState(false);
  const [tosAccepted, setTosAccepted] = useState(false);

  // If already subscribed, redirect to dashboard
  useEffect(() => {
    if (user && ACTIVE_STATUSES.includes(user.subscription_status)) {
      navigate('/dashboard', { replace: true });
    }
  }, [user, navigate]);

  // Handle checkout=success redirect: poll until subscription is active
  // (webhook may take a few seconds after Stripe redirects user back)
  const [checkoutPending, setCheckoutPending] = useState(() => {
    const params = new URLSearchParams(window.location.search);
    return params.get('checkout') === 'success';
  });

  useEffect(() => {
    if (!checkoutPending) return;
    let cancelled = false;
    let attempts = 0;
    const maxAttempts = 15; // 15 x 2s = 30s max

    const poll = async () => {
      while (!cancelled && attempts < maxAttempts) {
        attempts++;
        try {
          const data = await api.get('/api/settings');
          if (ACTIVE_STATUSES.includes(data?.subscription_status)) {
            trackEvent('subscription_started', { status: data.subscription_status });
            toast.success('Subscription activated! Welcome to DraftSpring.');
            navigate('/dashboard', { replace: true });
            return;
          }
        } catch {}
        await new Promise((r) => setTimeout(r, 2000));
      }
      if (!cancelled) {
        setCheckoutPending(false);
        toast.error('Subscription activation is taking longer than expected. Please refresh the page.');
      }
    };
    poll();
    return () => { cancelled = true; };
  }, [checkoutPending, navigate]);

  const handleSubscribe = useCallback(async () => {
    setLoading(true);
    try {
      const res = await api.get('/api/checkout/session');
      if (res.url) {
        window.location.href = res.url;
      } else {
        toast.error('Could not start checkout. Please try again.');
      }
    } catch (e) {
      toast.error('Could not start checkout. Please try again.');
    } finally {
      setLoading(false);
    }
  }, []);

  // Show activation spinner if checkout just completed
  if (checkoutPending) {
    return (
      <div className="min-h-screen bg-bg-deep flex items-center justify-center px-4">
        <div className="text-center" data-testid="checkout-pending">
          <div className="w-12 h-12 border-3 border-accent-blue border-t-transparent rounded-full animate-spin mx-auto mb-4" />
          <h2 className="text-xl font-bold text-text-primary mb-2">Activating your subscription...</h2>
          <p className="text-text-secondary text-sm">This usually takes just a few seconds.</p>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-bg-deep flex items-center justify-center px-4">
      {/* Background effects */}
      <div className="fixed top-[-200px] left-[-200px] w-[600px] h-[600px] bg-[radial-gradient(circle,rgba(59,130,246,0.12),transparent_70%)] blur-[80px] pointer-events-none" />
      <div className="fixed top-[100px] right-[-200px] w-[500px] h-[500px] bg-[radial-gradient(circle,rgba(139,92,246,0.08),transparent_70%)] blur-[80px] pointer-events-none" />

      <div className="relative z-10 max-w-md w-full">
        {/* Logo */}
        <div className="text-center mb-8">
          <h1 className="text-3xl font-extrabold gradient-text mb-2">DraftSpring</h1>
          <p className="text-text-secondary text-sm">
            Welcome{user?.email ? `, ${user.email}` : ''}! One step to go.
          </p>
        </div>

        {/* Plan card */}
        <div className="glass-card p-8" data-testid="subscribe-card">
          <div className="text-center mb-6">
            <div className="text-4xl font-extrabold text-text-primary mb-1">
              $9<span className="text-lg font-normal text-text-muted">/month</span>
            </div>
            <div className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full bg-accent-cyan/10 border border-accent-cyan/20 mt-2">
              <span className="text-accent-cyan text-sm font-semibold">7-day free trial</span>
            </div>
            <p className="text-text-muted text-xs mt-2">No charge until your trial ends. Cancel anytime.</p>
          </div>

          <ul className="space-y-3 mb-8">
            {[
              '8 articles per billing cycle',
              'AI research, writing & editing',
              'Auto-generated images',
              'SEO optimization built in',
              'Two human approval checkpoints',
              'Direct Ghost publishing',
            ].map((f) => (
              <li key={f} className="flex items-start gap-2.5 text-sm text-text-secondary">
                <svg className="w-4 h-4 text-success flex-shrink-0 mt-0.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                  <polyline points="20 6 9 17 4 12" />
                </svg>
                {f}
              </li>
            ))}
          </ul>

          <label className="flex items-start gap-3 mb-6 cursor-pointer select-none">
            <input
              type="checkbox"
              checked={tosAccepted}
              onChange={(e) => setTosAccepted(e.target.checked)}
              className="mt-0.5 w-4 h-4 rounded border-border-subtle bg-bg-deep text-accent-blue focus:ring-accent-blue/30 focus:ring-2 accent-[#3b82f6] cursor-pointer"
              data-testid="tos-checkbox"
            />
            <span className="text-text-muted text-xs leading-relaxed">
              By creating an account, you agree to our{' '}
              <a href="https://draftspring.io/terms-of-service/" target="_blank" rel="noopener noreferrer" className="text-accent-blue hover:underline">Terms of Service</a>
              {' '}and{' '}
              <a href="https://draftspring.io/privacy-policy/" target="_blank" rel="noopener noreferrer" className="text-accent-blue hover:underline">Privacy Policy</a>.
            </span>
          </label>

          <button
            onClick={handleSubscribe}
            disabled={loading || !tosAccepted}
            className="w-full py-3.5 rounded-xl font-semibold text-white gradient-bg shadow-lg shadow-accent-blue/20 hover:shadow-accent-blue/30 transition-all disabled:opacity-50 disabled:cursor-not-allowed"
            data-testid="start-trial-btn"
          >
            {loading ? (
              <span className="flex items-center justify-center gap-2">
                <svg className="w-4 h-4 animate-spin" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="M12 2v4m0 12v4m-7.07-3.93l2.83-2.83m8.49-8.49l2.83-2.83M2 12h4m12 0h4m-3.93 7.07l-2.83-2.83M7.76 7.76L4.93 4.93" />
                </svg>
                Redirecting to checkout...
              </span>
            ) : (
              'Start Free Trial →'
            )}
          </button>

          <p className="text-center text-text-muted text-xs mt-4">
            Powered by Stripe · Secure payment processing
          </p>
        </div>

      </div>
    </div>
  );
}
