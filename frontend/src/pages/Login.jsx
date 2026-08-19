import { useState } from 'react';
import { api } from '../lib/api';
import { trackEvent } from '../lib/posthog';
import toast from 'react-hot-toast';

function isValidEmail(email) {
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email);
}

export function Login() {
  const [email, setEmail] = useState('');
  const [sent, setSent] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');
  const [devUrl, setDevUrl] = useState('');

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError('');

    if (!isValidEmail(email)) {
      setError('Please enter a valid email address');
      return;
    }

    setSubmitting(true);
    try {
      const res = await api.post('/auth/request', { email });
      if (res.dev_verify_url) {
        // Rewrite the URL to use the current browser origin (dev URL may say localhost)
        const url = new URL(res.dev_verify_url);
        url.host = window.location.host;
        url.protocol = window.location.protocol;
        setDevUrl(url.toString());
      }
      setSent(true);
      trackEvent('magic_link_requested', { email });
    } catch (err) {
      setError(err.message || 'Something went wrong');
    } finally {
      setSubmitting(false);
    }
  };

  if (sent) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-bg-deep px-4">
        <div className="glass-card p-8 max-w-md w-full text-center">
          <div className="text-4xl mb-4">✉️</div>
          <h1 className="text-2xl font-bold text-text-primary mb-2">Check your email</h1>
          <p className="text-text-secondary" data-testid="confirmation-message">
            {devUrl ? (
              <>Click the link below to sign in (dev mode — no email sent):</>
            ) : (
              <>We sent a magic link to <span className="font-medium text-text-primary">{email}</span>. Click it to sign in.</>
            )}
          </p>
          {devUrl && (
            <a
              href={devUrl}
              className="inline-block mt-4 px-6 py-3 bg-accent-blue text-white rounded-xl font-medium hover:opacity-90 transition-opacity"
              data-testid="dev-login-link"
            >
              Sign in now →
            </a>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-bg-deep px-4">
      <div className="glass-card p-8 max-w-md w-full">
        <div className="text-center mb-8">
          <div className="flex items-center justify-center gap-2">
            <h1 className="text-3xl font-bold gradient-text">DraftSpring</h1>
            <span className="px-2 py-0.5 text-[10px] font-bold uppercase tracking-wider bg-accent-blue/15 text-accent-blue border border-accent-blue/30 rounded-full">beta</span>
          </div>
          <p className="text-text-secondary mt-2">Sign in with your email</p>
        </div>

        <form onSubmit={handleSubmit} noValidate>
          <label htmlFor="email" className="block text-sm font-medium text-text-secondary mb-2">
            Email address
          </label>
          <input
            id="email"
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="you@example.com"
            className="w-full px-4 py-3 bg-bg-hover border border-border rounded-xl text-text-primary placeholder:text-text-muted focus:outline-none focus:border-accent-blue transition-colors"
            autoFocus
            disabled={submitting}
          />

          {error && (
            <p className="mt-2 text-sm text-error" role="alert">{error}</p>
          )}

          <button
            type="submit"
            disabled={submitting}
            className="btn-primary w-full mt-6 py-3 text-base"
          >
            {submitting ? 'Sending...' : 'Send magic link'}
          </button>
        </form>
      </div>
    </div>
  );
}
