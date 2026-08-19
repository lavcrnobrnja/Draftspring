import { useEffect } from 'react';
import { useSearchParams, useNavigate } from 'react-router-dom';
import { trackEvent } from '../lib/posthog';

export function AuthVerify() {
  const [sp] = useSearchParams();
  const nav = useNavigate();
  useEffect(() => {
    if (!sp.get("token")) {
      nav("/login?error=invalid_token", { replace: true });
    } else {
      trackEvent('login_verified');
    }
  }, [sp, nav]);
  return (
    <div className="min-h-screen bg-bg-deep flex items-center justify-center">
      <div className="text-center">
        <div className="w-12 h-12 rounded-full gradient-bg animate-spin mx-auto mb-4 opacity-60" />
        <p className="text-text-secondary text-sm">Verifying...</p>
      </div>
    </div>
  );
}
