import { createContext, useContext, useState, useEffect, useCallback } from 'react';
import { api } from '../lib/api';
import { identifyUser, resetUser } from '../lib/posthog';

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);

  const fetchUser = useCallback(async () => {
    try {
      const data = await api.get('/api/settings');
      setUser(data);
      // Identify user in PostHog for analytics
      if (data?.id) {
        identifyUser(data.id, {
          email: data.email,
          ghost_connected: !!data.ghost_key_valid,
          subscription_status: data.subscription_status || 'unknown',
          ghost_url: data.ghost_url || null,
          ghost_site_title: data.ghost_site_title || null,
          brand_voice: data.brand_voice || null,
        });

      }
    } catch {
      setUser(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchUser();
  }, [fetchUser]);

  const logout = useCallback(async () => {
    try {
      await api.post('/auth/logout');
    } catch {
      // ignore
    }
    resetUser();
    setUser(null);
    window.location.href = '/login';
  }, []);

  return (
    <AuthContext.Provider value={{ user, loading, logout, refetchUser: fetchUser }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be inside AuthProvider');
  return ctx;
}
