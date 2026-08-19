import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

vi.mock('react-hot-toast', () => ({
  default: { error: vi.fn(), success: vi.fn() },
}));

vi.mock('../hooks/useAuth', () => ({
  useAuth: vi.fn(),
}));

import { Runway } from '../pages/Runway';
import { useAuth } from '../hooks/useAuth';

const mockFetch = vi.fn();
global.fetch = mockFetch;

const sampleArticles = [
  { id: 'art-1', title: 'AI in Healthcare', keyword: 'ai healthcare', state: 'DRAFTING', state_label: 'Writing draft...', column: 'in_production', updated_at: '2026-03-17T10:00:00Z' },
  { id: 'art-2', title: 'Cloud Computing', keyword: 'cloud', state: 'WAITING_CHECKPOINT_2', state_label: 'Ready for review', column: 'in_review', updated_at: '2026-03-16T10:00:00Z' },
  { id: 'art-3', title: 'Remote Work', keyword: 'remote', state: 'PUBLISHED', state_label: 'Published', column: 'published', ghost_post_url: 'https://blog.example.com/rw', updated_at: '2026-03-15T10:00:00Z' },
];

function renderRunway(userOverrides = {}, articles = []) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: 0 } } });
  useAuth.mockReturnValue({
    user: { email: 'test@example.com', ghost_key_valid: 1, subscription_status: 'active', publish_days: '["monday"]', articles_remaining: 6, ...userOverrides },
    loading: false, logout: vi.fn(), refetchUser: vi.fn(),
  });
  mockFetch.mockResolvedValueOnce({ ok: true, status: 200, json: () => Promise.resolve({ articles }) });
  return render(<QueryClientProvider client={qc}><MemoryRouter><Runway /></MemoryRouter></QueryClientProvider>);
}

describe('Runway Page', () => {
  beforeEach(() => vi.clearAllMocks());

  it('shows onboarding checklist when no articles', async () => {
    renderRunway({ ghost_key_valid: 0 }, []);
    await waitFor(() => expect(screen.getByTestId('onboarding-checklist')).toBeInTheDocument());
    expect(screen.getByText(/connect ghost blog/i)).toBeInTheDocument();
  });

  it('shows articles in kanban columns', async () => {
    renderRunway({}, sampleArticles);
    await waitFor(() => expect(screen.getByTestId('kanban-board')).toBeInTheDocument());
    expect(screen.getByText('AI in Healthcare')).toBeInTheDocument();
    expect(screen.getByText('Cloud Computing')).toBeInTheDocument();
    expect(screen.getByText('Remote Work')).toBeInTheDocument();
  });

  it('shows cancel on non-published articles', async () => {
    renderRunway({}, sampleArticles);
    await waitFor(() => expect(screen.getByTestId('kanban-board')).toBeInTheDocument());
    expect(screen.getAllByTestId('cancel-button').length).toBe(2);
  });

  it('shows confirmation on cancel click', async () => {
    renderRunway({}, [sampleArticles[0]]);
    const user = userEvent.setup();
    await waitFor(() => expect(screen.getByTestId('cancel-button')).toBeInTheDocument());
    await user.click(screen.getByTestId('cancel-button'));
    expect(screen.getByTestId('cancel-confirm')).toBeInTheDocument();
    expect(screen.getByText(/cancel this article/i)).toBeInTheDocument();
  });

  it('calls cancel API', async () => {
    renderRunway({}, [sampleArticles[0]]);
    const user = userEvent.setup();
    await waitFor(() => expect(screen.getByTestId('cancel-button')).toBeInTheDocument());
    await user.click(screen.getByTestId('cancel-button'));
    mockFetch.mockResolvedValueOnce({ ok: true, status: 200, json: () => Promise.resolve({ ok: true }) })
      .mockResolvedValueOnce({ ok: true, status: 200, json: () => Promise.resolve({ articles: [] }) });
    await user.click(screen.getByTestId('confirm-cancel'));
    await waitFor(() => expect(mockFetch).toHaveBeenCalledWith(expect.stringContaining('/api/articles/art-1/cancel'), expect.objectContaining({ method: 'POST' })));
  });
});
