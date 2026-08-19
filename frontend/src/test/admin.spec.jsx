import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { Admin } from '../pages/Admin';

vi.mock('react-hot-toast', () => ({
  default: { error: vi.fn(), success: vi.fn() },
}));

const mockFetch = vi.fn();
global.fetch = mockFetch;

function renderAdmin() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <Admin />
      </MemoryRouter>
    </QueryClientProvider>
  );
}

const overviewData = {
  total_users: 42,
  total_articles: 100,
  articles_by_state: { OUTLINING: 10, PUBLISHED: 50, FAILED: 5 },
  active_subscriptions: 20,
  estimated_mrr_cents: 30000,
  total_cost_cents: 5000,
  failed_count: 5,
};

const usersData = {
  users: [
    { id: 'u1', email: 'alice@test.com', subscription_status: 'active', article_count: 5, published_count: 3, created_at: '2026-01-01T00:00:00Z' },
    { id: 'u2', email: 'bob@test.com', subscription_status: 'none', article_count: 1, published_count: 0, created_at: '2026-02-01T00:00:00Z' },
  ],
  total: 2,
  page: 1,
  per_page: 20,
  pages: 1,
};

const articlesData = {
  articles: [
    { id: 'a1', title: 'Test Article 1', state: 'PUBLISHED', user_email: 'alice@test.com', failure_reason: null },
    { id: 'a2', title: 'Test Article 2', state: 'FAILED', user_email: 'bob@test.com', failure_reason: 'LLM timeout' },
  ],
  total: 2,
  page: 1,
  per_page: 20,
  pages: 1,
};

describe('Admin Page', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders three tabs', () => {
    mockFetch.mockResolvedValue({ ok: true, status: 200, json: async () => overviewData });
    renderAdmin();
    expect(screen.getByTestId('admin-tab-overview')).toBeInTheDocument();
    expect(screen.getByTestId('admin-tab-users')).toBeInTheDocument();
    expect(screen.getByTestId('admin-tab-articles')).toBeInTheDocument();
  });

  it('renders overview stats', async () => {
    mockFetch.mockResolvedValue({ ok: true, status: 200, json: async () => overviewData });
    renderAdmin();
    await waitFor(() => {
      expect(screen.getByText('42')).toBeInTheDocument();
      expect(screen.getByText('100')).toBeInTheDocument();
      expect(screen.getByText('Total Users')).toBeInTheDocument();
      expect(screen.getByText('Total Articles')).toBeInTheDocument();
      expect(screen.getByText('Active Subs')).toBeInTheDocument();
    });
  });

  it('switches to users tab and shows table', async () => {
    const user = userEvent.setup();
    mockFetch
      .mockResolvedValueOnce({ ok: true, status: 200, json: async () => overviewData })
      .mockResolvedValueOnce({ ok: true, status: 200, json: async () => usersData });

    renderAdmin();
    await user.click(screen.getByTestId('admin-tab-users'));

    await waitFor(() => {
      expect(screen.getByText('alice@test.com')).toBeInTheDocument();
      expect(screen.getByText('bob@test.com')).toBeInTheDocument();
    });
  });

  it('switches to articles tab and shows state filter', async () => {
    const user = userEvent.setup();
    mockFetch
      .mockResolvedValueOnce({ ok: true, status: 200, json: async () => overviewData })
      .mockResolvedValueOnce({ ok: true, status: 200, json: async () => articlesData });

    renderAdmin();
    await user.click(screen.getByTestId('admin-tab-articles'));

    await waitFor(() => {
      expect(screen.getByTestId('admin-state-filter')).toBeInTheDocument();
      expect(screen.getByText('Test Article 1')).toBeInTheDocument();
      expect(screen.getByText('LLM timeout')).toBeInTheDocument();
    });
  });

  it('shows retry button only for failed articles', async () => {
    const user = userEvent.setup();
    mockFetch
      .mockResolvedValueOnce({ ok: true, status: 200, json: async () => overviewData })
      .mockResolvedValueOnce({ ok: true, status: 200, json: async () => articlesData });

    renderAdmin();
    await user.click(screen.getByTestId('admin-tab-articles'));

    await waitFor(() => {
      const retryButtons = screen.getAllByText('Retry');
      expect(retryButtons).toHaveLength(1); // Only for FAILED article
    });
  });

  it('renders admin dashboard title', () => {
    mockFetch.mockResolvedValue({ ok: true, status: 200, json: async () => overviewData });
    renderAdmin();
    expect(screen.getByText('Admin Dashboard')).toBeInTheDocument();
  });
});
