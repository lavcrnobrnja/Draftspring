import { Navigate } from 'react-router-dom';
import { useAuth } from '../hooks/useAuth';

export function RequireAuth({ children }) {
  const { user, isLoading: loading } = useAuth();

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-bg-deep">
        <div className="w-8 h-8 border-2 border-accent-blue border-t-transparent rounded-full animate-spin" />
      </div>
    );
  }

  if (!user) {
    return <Navigate to="/login" replace />;
  }

  return children;
}

export function RequireReviewAuth({ children }) {
  const { user, isLoading: loading } = useAuth();

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-bg-deep">
        <div className="w-8 h-8 border-2 border-accent-blue border-t-transparent rounded-full animate-spin" />
      </div>
    );
  }

  // Review pages accessible with any valid session (full or scoped)
  if (!user) {
    return <Navigate to="/login" replace />;
  }

  return children;
}
