import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '../lib/api';
import { Admin } from './Admin';

function AdminLogin() {
  const [email, setEmail] = useState('');
  const [sent, setSent] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');
  const [devUrl, setDevUrl] = useState('');

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError('');
    setSubmitting(true);
    try {
      const res = await api.post('/api/admin/login', { email });
      if (res.dev_verify_url) {
        const url = new URL(res.dev_verify_url);
        url.host = window.location.host;
        url.protocol = window.location.protocol;
        setDevUrl(url.toString());
      }
      setSent(true);
    } catch (err) {
      setError(err.message || 'Access denied');
    } finally {
      setSubmitting(false);
    }
  };

  if (sent) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-bg-deep px-4">
        <div className="glass-card p-8 max-w-md w-full text-center">
          <div className="text-4xl mb-4">🔐</div>
          <h1 className="text-2xl font-bold text-text-primary mb-2">Check your email</h1>
          <p className="text-text-secondary">
            {devUrl ? (
              <>Click below to sign in (dev mode):</>
            ) : (
              <>Admin magic link sent to <span className="font-medium text-text-primary">{email}</span>.</>
            )}
          </p>
          {devUrl && (
            <a
              href={devUrl}
              className="inline-block mt-4 px-6 py-3 bg-accent-blue text-white rounded-xl font-medium hover:opacity-90 transition-opacity"
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
          <h1 className="text-3xl font-bold gradient-text mb-2">DraftSpring Admin</h1>
          <p className="text-text-secondary">Admin access only</p>
        </div>
        <form onSubmit={handleSubmit} noValidate>
          <label htmlFor="admin-email" className="block text-sm font-medium text-text-secondary mb-2">
            Admin email
          </label>
          <input
            id="admin-email"
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="admin@example.com"
            className="w-full px-4 py-3 bg-bg-hover border border-border rounded-xl text-text-primary placeholder:text-text-muted focus:outline-none focus:border-accent-blue transition-colors"
            autoFocus
            disabled={submitting}
          />
          {error && <p className="mt-2 text-sm text-error" role="alert">{error}</p>}
          <button type="submit" disabled={submitting} className="btn-primary w-full mt-6 py-3 text-base">
            {submitting ? 'Sending...' : 'Request admin access'}
          </button>
        </form>
      </div>
    </div>
  );
}

function AdminLayout() {
  const handleLogout = async () => {
    try {
      await api.post('/auth/logout');
    } catch {
      // ignore
    }
    window.location.href = '/daddyo/';
  };

  return (
    <div className="min-h-screen bg-bg-deep">
      <header className="border-b border-border bg-bg-card px-6 py-4 flex items-center justify-between">
        <h1 className="text-xl font-bold gradient-text">DraftSpring Admin</h1>
        <button
          onClick={handleLogout}
          className="text-sm text-text-secondary hover:text-text-primary transition-colors"
        >
          Logout
        </button>
      </header>
      <main className="p-8 max-w-7xl mx-auto">
        <Admin />
      </main>
    </div>
  );
}

export function AdminArea() {
  // Check if current session is admin
  const { data, isLoading } = useQuery({
    queryKey: ['admin-check'],
    queryFn: () => api.get('/api/admin/check'),
    retry: false,
    staleTime: 30_000,
  });

  if (isLoading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-bg-deep">
        <div className="w-8 h-8 border-2 border-accent-blue border-t-transparent rounded-full animate-spin" />
      </div>
    );
  }

  if (data?.is_admin) {
    return <AdminLayout />;
  }

  return <AdminLogin />;
}
