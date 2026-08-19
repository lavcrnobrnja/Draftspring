/**
 * Auth hook — fetches settings to check if session is valid.
 */
import { useEffect, useRef } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '../lib/api';

export function useAuth() {
  const { data, isLoading, error } = useQuery({
    queryKey: ['settings'],
    queryFn: () => api.get('/api/settings'),
    retry: false,
    staleTime: 60_000,
  });

  return {
    user: data || null,
    isLoading,
    isAuthenticated: !!data && !error,
  };
}
