import { Navigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { api } from '../lib/api';

/**
 * For CP1/CP2 magic link pages — checks session exists (any scope).
 * Doesn't require full auth — scoped sessions are fine.
 */
export function RequireReviewAuth({ children }) {
  const { data, isLoading, error } = useQuery({
    queryKey: ['review-auth'],
    queryFn: () => api.get('/api/settings'),
    retry: false,
    staleTime: 60_000,
  });

  if (isLoading) {
    return (
      <div className="min-h-screen bg-bg-deep flex items-center justify-center">
        <div className="w-8 h-8 rounded-full gradient-bg animate-spin opacity-60" />
      </div>
    );
  }

  if (error) {
    return <Navigate to="/login?error=session_expired" replace />;
  }

  return children;
}
