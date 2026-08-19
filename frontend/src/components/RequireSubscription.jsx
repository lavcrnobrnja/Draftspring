import { Navigate } from 'react-router-dom';
import { useAuth } from '../hooks/useAuth';

const ACTIVE_STATUSES = ['active', 'trialing'];

/**
 * Wraps dashboard routes. Redirects to /subscribe if user has no active subscription.
 * Users with past_due still get in (grace period) — they'll see warnings elsewhere.
 * Users with 'none' or 'canceled' must subscribe first.
 */
export function RequireSubscription({ children }) {
  const { user, isLoading } = useAuth();

  if (isLoading) return null; // RequireAuth already shows spinner

  const status = user?.subscription_status || 'none';

  if (!ACTIVE_STATUSES.includes(status)) {
    return <Navigate to="/subscribe" replace />;
  }

  return children;
}
