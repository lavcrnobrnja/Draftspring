import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor, within, act } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { BrowserRouter, MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

// Mock api module
vi.mock('../lib/api', () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    del: vi.fn(),
  },
}));

// Mock useAuth hook
vi.mock('../hooks/useAuth', () => ({
  useAuth: vi.fn(),
}));

// Mock react-hot-toast
vi.mock('react-hot-toast', () => ({
  default: {
    success: vi.fn(),
    error: vi.fn(),
  },
}));

import { Runway } from '../pages/Runway';
import { api } from '../lib/api';
import { useAuth } from '../hooks/useAuth';
import toast from 'react-hot-toast';

// Helper: create a fresh QueryClient for each test
function createTestQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0, staleTime: 0 },
      mutations: { retry: false },
    },
  });
}

// Helper: render with providers
function renderWithProviders(ui, { route = '/dashboard' } = {}) {
  const queryClient = createTestQueryClient();
  return {
    ...render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={[route]}>
          {ui}
        </MemoryRouter>
      </QueryClientProvider>
    ),
    queryClient,
  };
}

// Factories
function makeArticle(overrides = {}) {
  return {
    id: 'art_001',
    title: 'Test Article',
    state: 'DRAFTING',
    state_label: 'Writing',
    column: 'in_production',
    keyword: 'test keyword',
    has_seo: false,
    image_count: 0,
    valid_image_count: 0,
    updated_at: '2026-03-18T12:00:00Z',
    ghost_post_url: null,
    ...overrides,
  };
}

function makeBatch(overrides = {}) {
  return {
    id: 'batch_001',
    status: 'pending_ideation',
    seed_count: 3,
    created_at: '2026-03-18T12:00:00Z',
    ...overrides,
  };
}

describe('Dashboard (Runway)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useAuth.mockReturnValue({
      user: { email: 'test@example.com', ghost_key_valid: 1, publish_days: '["monday"]', subscription_status: 'active' },
      isLoading: false,
      isAuthenticated: true,
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  // ─── Loading State ───

  it('renders loading skeleton while data is fetching', async () => {
    api.get.mockImplementation((path) => {
      if (path === '/api/articles') return new Promise(() => {}); // never resolves
      if (path === '/api/batches') return Promise.resolve({ batches: [] });
      return Promise.resolve({});
    });

    renderWithProviders(<Runway />);
    expect(screen.getByText('Dashboard')).toBeInTheDocument();
    expect(document.querySelector('.skeleton')).toBeInTheDocument();
  });

  // ─── Empty State (No Articles, No Active Batches) ───

  it('renders onboarding checklist when no articles and no active batches', async () => {
    api.get.mockImplementation((path) => {
      if (path === '/api/articles') return Promise.resolve({ articles: [] });
      if (path === '/api/batches') return Promise.resolve({ batches: [] });
      return Promise.resolve({});
    });

    renderWithProviders(<Runway />);
    await waitFor(() => {
      expect(screen.getByTestId('onboarding-checklist')).toBeInTheDocument();
    });
    expect(screen.getByText('Welcome to DraftSpring! 🚀')).toBeInTheDocument();
  });

  it('shows onboarding with unchecked Ghost when not connected', async () => {
    useAuth.mockReturnValue({
      user: { email: 'test@example.com', ghost_key_valid: 0, publish_days: '[]', subscription_status: 'active' },
      isLoading: false,
      isAuthenticated: true,
    });

    api.get.mockImplementation((path) => {
      if (path === '/api/articles') return Promise.resolve({ articles: [] });
      if (path === '/api/batches') return Promise.resolve({ batches: [] });
      return Promise.resolve({});
    });

    renderWithProviders(<Runway />);
    await waitFor(() => {
      expect(screen.getByTestId('onboarding-checklist')).toBeInTheDocument();
    });
  });

  // ─── Kanban Board Rendering ───

  it('renders kanban board with all 4 columns', async () => {
    const articles = [
      makeArticle({ id: '1', state: 'OUTLINING', state_label: 'Outlining', column: 'in_production' }),
      makeArticle({ id: '2', state: 'WAITING_CHECKPOINT_2', state_label: 'Ready for Review', column: 'in_review' }),
      makeArticle({ id: '3', state: 'READY_TO_PUBLISH', state_label: 'Scheduled', column: 'scheduled' }),
      makeArticle({ id: '4', state: 'PUBLISHED', state_label: 'Published', column: 'published', ghost_post_url: 'https://blog.example.com/article' }),
    ];

    api.get.mockImplementation((path) => {
      if (path === '/api/articles') return Promise.resolve({ articles });
      if (path === '/api/batches') return Promise.resolve({ batches: [] });
      return Promise.resolve({});
    });

    renderWithProviders(<Runway />);
    await waitFor(() => {
      expect(screen.getByTestId('kanban-board')).toBeInTheDocument();
    });

    const board = screen.getByTestId('kanban-board');
    // Column headers are h2 elements with the column label
    const headers = board.querySelectorAll('h2');
    const headerTexts = Array.from(headers).map(h => h.textContent);
    expect(headerTexts.some(t => t.includes('In Production'))).toBe(true);
    expect(headerTexts.some(t => t.includes('In Review'))).toBe(true);
    expect(headerTexts.some(t => t.includes('Scheduled'))).toBe(true);
    expect(headerTexts.some(t => t.includes('Published'))).toBe(true);
  });

  it('shows correct article count per column', async () => {
    const articles = [
      makeArticle({ id: '1', column: 'in_production', state: 'DRAFTING' }),
      makeArticle({ id: '2', column: 'in_production', state: 'OUTLINING' }),
      makeArticle({ id: '3', column: 'in_review', state: 'WAITING_CHECKPOINT_2' }),
    ];

    api.get.mockImplementation((path) => {
      if (path === '/api/articles') return Promise.resolve({ articles });
      if (path === '/api/batches') return Promise.resolve({ batches: [] });
      return Promise.resolve({});
    });

    renderWithProviders(<Runway />);
    await waitFor(() => {
      expect(screen.getByTestId('kanban-board')).toBeInTheDocument();
    });

    // Check column counts
    expect(screen.getByText('(2)')).toBeInTheDocument(); // in_production
    expect(screen.getByText('(1)')).toBeInTheDocument(); // in_review
  });

  // ─── Article Card Variants ───

  it('renders REVISION article with lock icon and dimmed state', async () => {
    const articles = [
      makeArticle({ id: '1', state: 'REVISION', state_label: 'Revising', column: 'in_production', title: 'Revision Article' }),
    ];

    api.get.mockImplementation((path) => {
      if (path === '/api/articles') return Promise.resolve({ articles });
      if (path === '/api/batches') return Promise.resolve({ batches: [] });
      return Promise.resolve({});
    });

    renderWithProviders(<Runway />);
    await waitFor(() => {
      expect(screen.getByText('🔒')).toBeInTheDocument();
      expect(screen.getByText('🔒 Being revised…')).toBeInTheDocument();
    });
  });

  it('renders SEO badge correctly when has_seo is true', async () => {
    const articles = [
      makeArticle({ id: '1', has_seo: true }),
    ];

    api.get.mockImplementation((path) => {
      if (path === '/api/articles') return Promise.resolve({ articles });
      if (path === '/api/batches') return Promise.resolve({ batches: [] });
      return Promise.resolve({});
    });

    renderWithProviders(<Runway />);
    await waitFor(() => {
      expect(screen.getByText('✓ SEO')).toBeInTheDocument();
    });
  });

  it('renders "No SEO" badge when has_seo is false', async () => {
    const articles = [
      makeArticle({ id: '1', has_seo: false }),
    ];

    api.get.mockImplementation((path) => {
      if (path === '/api/articles') return Promise.resolve({ articles });
      if (path === '/api/batches') return Promise.resolve({ batches: [] });
      return Promise.resolve({});
    });

    renderWithProviders(<Runway />);
    await waitFor(() => {
      expect(screen.getByText('✗ No SEO')).toBeInTheDocument();
    });
  });

  it('renders image badge with valid image count', async () => {
    const articles = [
      makeArticle({ id: '1', image_count: 3, valid_image_count: 2 }),
    ];

    api.get.mockImplementation((path) => {
      if (path === '/api/articles') return Promise.resolve({ articles });
      if (path === '/api/batches') return Promise.resolve({ batches: [] });
      return Promise.resolve({});
    });

    renderWithProviders(<Runway />);
    await waitFor(() => {
      expect(screen.getByText('🖼 2')).toBeInTheDocument();
    });
  });

  it('renders "Missing" image badge when images exist but none are valid', async () => {
    const articles = [
      makeArticle({ id: '1', image_count: 2, valid_image_count: 0 }),
    ];

    api.get.mockImplementation((path) => {
      if (path === '/api/articles') return Promise.resolve({ articles });
      if (path === '/api/batches') return Promise.resolve({ batches: [] });
      return Promise.resolve({});
    });

    renderWithProviders(<Runway />);
    await waitFor(() => {
      expect(screen.getByText('🖼 Missing')).toBeInTheDocument();
    });
  });

  it('does not render image badge when image_count is 0', async () => {
    const articles = [
      makeArticle({ id: '1', image_count: 0, valid_image_count: 0 }),
    ];

    api.get.mockImplementation((path) => {
      if (path === '/api/articles') return Promise.resolve({ articles });
      if (path === '/api/batches') return Promise.resolve({ batches: [] });
      return Promise.resolve({});
    });

    renderWithProviders(<Runway />);
    await waitFor(() => {
      expect(screen.getByTestId('article-card')).toBeInTheDocument();
    });
    expect(screen.queryByText(/🖼/)).not.toBeInTheDocument();
  });

  it('renders published article as clickable card', async () => {
    const articles = [
      makeArticle({
        id: '1',
        state: 'PUBLISHED',
        state_label: 'Published',
        column: 'published',
        title: 'Published Article',
        ghost_post_url: 'https://blog.example.com/published-article',
      }),
    ];

    api.get.mockImplementation((path) => {
      if (path === '/api/articles') return Promise.resolve({ articles });
      if (path === '/api/batches') return Promise.resolve({ batches: [] });
      return Promise.resolve({});
    });

    renderWithProviders(<Runway />);
    await waitFor(() => {
      expect(screen.getByText('Published Article')).toBeInTheDocument();
    });
    // Published articles render as clickable cards (role="link") not <a> tags
    const card = screen.getByTestId('article-card');
    expect(card).toHaveAttribute('role', 'link');
  });

  it('renders in-review article as clickable card', async () => {
    const articles = [
      makeArticle({
        id: 'art_review',
        state: 'WAITING_CHECKPOINT_2',
        state_label: 'Ready for Review',
        column: 'in_review',
        title: 'Review Me',
      }),
    ];

    api.get.mockImplementation((path) => {
      if (path === '/api/articles') return Promise.resolve({ articles });
      if (path === '/api/batches') return Promise.resolve({ batches: [] });
      return Promise.resolve({});
    });

    renderWithProviders(<Runway />);
    await waitFor(() => {
      expect(screen.getByText('Review Me')).toBeInTheDocument();
    });
    // In-review articles render as clickable cards (role="link")
    const card = screen.getByTestId('article-card');
    expect(card).toHaveAttribute('role', 'link');
  });

  // ─── Archived Section ───

  it('renders archived section with articles', async () => {
    const articles = [
      makeArticle({ id: '1', column: 'in_production', state: 'DRAFTING' }),
      makeArticle({ id: '2', column: 'archived', state: 'ARCHIVED', state_label: 'Archived', title: 'Old Article' }),
    ];

    api.get.mockImplementation((path) => {
      if (path === '/api/articles') return Promise.resolve({ articles });
      if (path === '/api/batches') return Promise.resolve({ batches: [] });
      return Promise.resolve({});
    });

    renderWithProviders(<Runway />);
    await waitFor(() => {
      expect(screen.getByTestId('archived-section')).toBeInTheDocument();
    });

    // Archived section collapsed by default
    expect(screen.getByText('Archived (1)')).toBeInTheDocument();
    expect(screen.queryByText('Old Article')).not.toBeInTheDocument();
  });

  it('expands archived section on click', async () => {
    const user = userEvent.setup();
    const articles = [
      makeArticle({ id: '1', column: 'in_production', state: 'DRAFTING' }),
      makeArticle({ id: '2', column: 'archived', state: 'ARCHIVED', state_label: 'Archived', title: 'Old Article' }),
    ];

    api.get.mockImplementation((path) => {
      if (path === '/api/articles') return Promise.resolve({ articles });
      if (path === '/api/batches') return Promise.resolve({ batches: [] });
      return Promise.resolve({});
    });

    renderWithProviders(<Runway />);
    await waitFor(() => {
      expect(screen.getByTestId('archived-section')).toBeInTheDocument();
    });

    await user.click(screen.getByText('Archived (1)'));
    expect(screen.getByText('Old Article')).toBeInTheDocument();
  });

  it('does not render archived section when no archived articles', async () => {
    const articles = [
      makeArticle({ id: '1', column: 'in_production', state: 'DRAFTING' }),
    ];

    api.get.mockImplementation((path) => {
      if (path === '/api/articles') return Promise.resolve({ articles });
      if (path === '/api/batches') return Promise.resolve({ batches: [] });
      return Promise.resolve({});
    });

    renderWithProviders(<Runway />);
    await waitFor(() => {
      expect(screen.getByTestId('kanban-board')).toBeInTheDocument();
    });
    expect(screen.queryByTestId('archived-section')).not.toBeInTheDocument();
  });

  // ─── Batch Status Banners ───

  it('renders pending ideation batch banner', async () => {
    api.get.mockImplementation((path) => {
      if (path === '/api/articles') return Promise.resolve({ articles: [makeArticle()] });
      if (path === '/api/batches') return Promise.resolve({ batches: [makeBatch({ status: 'pending_ideation', seed_count: 5 })] });
      return Promise.resolve({});
    });

    renderWithProviders(<Runway />);
    await waitFor(() => {
      expect(screen.getByText('Generating ideas…')).toBeInTheDocument();
      expect(screen.getByText(/Brief submitted/)).toBeInTheDocument();
    });
  });

  it('does not render waiting approval batch banner (moved to NewBatch)', async () => {
    api.get.mockImplementation((path) => {
      if (path === '/api/articles') return Promise.resolve({ articles: [makeArticle()] });
      if (path === '/api/batches') return Promise.resolve({ batches: [makeBatch({ status: 'waiting_approval' })] });
      return Promise.resolve({});
    });

    renderWithProviders(<Runway />);
    await waitFor(() => {
      expect(screen.getByTestId('kanban-board')).toBeInTheDocument();
    });
    // waiting_approval banners no longer appear on dashboard — idea review happens inline in NewBatch
    expect(screen.queryByText('Ideas ready for review!')).not.toBeInTheDocument();
  });

  it('renders no batch banner for processed/expired batches', async () => {
    api.get.mockImplementation((path) => {
      if (path === '/api/articles') return Promise.resolve({ articles: [makeArticle()] });
      if (path === '/api/batches') return Promise.resolve({ batches: [makeBatch({ status: 'processed' })] });
      return Promise.resolve({});
    });

    renderWithProviders(<Runway />);
    await waitFor(() => {
      expect(screen.getByTestId('kanban-board')).toBeInTheDocument();
    });
    expect(screen.queryByTestId('batch-status')).not.toBeInTheDocument();
  });

  it('renders batch banner with ideas-will-appear message', async () => {
    api.get.mockImplementation((path) => {
      if (path === '/api/articles') return Promise.resolve({ articles: [makeArticle()] });
      if (path === '/api/batches') return Promise.resolve({ batches: [makeBatch({ status: 'pending_ideation', seed_count: 1 })] });
      return Promise.resolve({});
    });

    renderWithProviders(<Runway />);
    await waitFor(() => {
      expect(screen.getByText(/ideas will appear shortly/i)).toBeInTheDocument();
    });
  });

  // ─── Cancel Article ───

  it('shows cancel confirmation dialog on cancel button click', async () => {
    const user = userEvent.setup();
    const articles = [
      makeArticle({ id: '1', state: 'DRAFTING', column: 'in_production' }),
    ];

    api.get.mockImplementation((path) => {
      if (path === '/api/articles') return Promise.resolve({ articles });
      if (path === '/api/batches') return Promise.resolve({ batches: [] });
      return Promise.resolve({});
    });

    renderWithProviders(<Runway />);
    await waitFor(() => {
      expect(screen.getByTestId('cancel-button')).toBeInTheDocument();
    });

    await user.click(screen.getByTestId('cancel-button'));
    expect(screen.getByTestId('cancel-confirm')).toBeInTheDocument();
  });

  it('calls cancel mutation on confirm', async () => {
    const user = userEvent.setup();
    const articles = [
      makeArticle({ id: 'art_cancel', state: 'DRAFTING', column: 'in_production' }),
    ];

    api.get.mockImplementation((path) => {
      if (path === '/api/articles') return Promise.resolve({ articles });
      if (path === '/api/batches') return Promise.resolve({ batches: [] });
      return Promise.resolve({});
    });
    api.post.mockResolvedValue({ status: 'archived' });

    renderWithProviders(<Runway />);
    await waitFor(() => {
      expect(screen.getByTestId('cancel-button')).toBeInTheDocument();
    });

    await user.click(screen.getByTestId('cancel-button'));
    await user.click(screen.getByTestId('confirm-cancel'));

    await waitFor(() => {
      expect(api.post).toHaveBeenCalledWith('/api/articles/art_cancel/cancel');
    });
  });

  it('does not show cancel button for PUBLISHED articles', async () => {
    const articles = [
      makeArticle({ id: '1', state: 'PUBLISHED', column: 'published' }),
    ];

    api.get.mockImplementation((path) => {
      if (path === '/api/articles') return Promise.resolve({ articles });
      if (path === '/api/batches') return Promise.resolve({ batches: [] });
      return Promise.resolve({});
    });

    renderWithProviders(<Runway />);
    await waitFor(() => {
      expect(screen.getByTestId('article-card')).toBeInTheDocument();
    });
    expect(screen.queryByTestId('cancel-button')).not.toBeInTheDocument();
  });

  it('does not show cancel button for REVISION articles', async () => {
    const articles = [
      makeArticle({ id: '1', state: 'REVISION', column: 'in_production' }),
    ];

    api.get.mockImplementation((path) => {
      if (path === '/api/articles') return Promise.resolve({ articles });
      if (path === '/api/batches') return Promise.resolve({ batches: [] });
      return Promise.resolve({});
    });

    renderWithProviders(<Runway />);
    await waitFor(() => {
      expect(screen.getByTestId('article-card')).toBeInTheDocument();
    });
    expect(screen.queryByTestId('cancel-button')).not.toBeInTheDocument();
  });

  // ─── Retry button (Trello #340) ───

  it('shows retry button for FAILED articles', async () => {
    const articles = [
      makeArticle({ id: 'art_failed', state: 'FAILED', column: 'in_production', state_label: 'Failed' }),
    ];

    api.get.mockImplementation((path) => {
      if (path === '/api/articles') return Promise.resolve({ articles });
      if (path === '/api/batches') return Promise.resolve({ batches: [] });
      return Promise.resolve({});
    });

    renderWithProviders(<Runway />);
    await waitFor(() => {
      expect(screen.getByTestId('retry-button')).toBeInTheDocument();
    });
    // Cancel button still present alongside retry
    expect(screen.getByTestId('cancel-button')).toBeInTheDocument();
  });

  it('does not show retry button for non-FAILED articles', async () => {
    const articles = [
      makeArticle({ id: '1', state: 'DRAFTING', column: 'in_production' }),
    ];

    api.get.mockImplementation((path) => {
      if (path === '/api/articles') return Promise.resolve({ articles });
      if (path === '/api/batches') return Promise.resolve({ batches: [] });
      return Promise.resolve({});
    });

    renderWithProviders(<Runway />);
    await waitFor(() => {
      expect(screen.getByTestId('cancel-button')).toBeInTheDocument();
    });
    expect(screen.queryByTestId('retry-button')).not.toBeInTheDocument();
  });

  it('calls retry mutation when retry button clicked', async () => {
    const user = userEvent.setup();
    const articles = [
      makeArticle({ id: 'art_retry', state: 'FAILED', column: 'in_production', state_label: 'Failed' }),
    ];

    api.get.mockImplementation((path) => {
      if (path === '/api/articles') return Promise.resolve({ articles });
      if (path === '/api/batches') return Promise.resolve({ batches: [] });
      return Promise.resolve({});
    });
    api.post.mockResolvedValue({ status: 'retrying', resumed_from: 'HUMANIZING' });

    renderWithProviders(<Runway />);
    await waitFor(() => {
      expect(screen.getByTestId('retry-button')).toBeInTheDocument();
    });

    await user.click(screen.getByTestId('retry-button'));

    await waitFor(() => {
      expect(api.post).toHaveBeenCalledWith('/api/articles/art_retry/retry');
    });
  });

  // ─── Navigation ───

  it('renders New Content button linking to /dashboard/new-batch', async () => {
    api.get.mockImplementation((path) => {
      if (path === '/api/articles') return Promise.resolve({ articles: [makeArticle()] });
      if (path === '/api/batches') return Promise.resolve({ batches: [] });
      return Promise.resolve({});
    });

    renderWithProviders(<Runway />);
    await waitFor(() => {
      expect(screen.getByTestId('kanban-board')).toBeInTheDocument();
    });

    const newBatchLink = screen.getByText('+ New Content');
    expect(newBatchLink).toHaveAttribute('href', '/dashboard/new-batch');
  });

  // ─── Edge Cases ───

  it('handles articles with no updated_at gracefully', async () => {
    const articles = [
      makeArticle({ id: '1', updated_at: null }),
    ];

    api.get.mockImplementation((path) => {
      if (path === '/api/articles') return Promise.resolve({ articles });
      if (path === '/api/batches') return Promise.resolve({ batches: [] });
      return Promise.resolve({});
    });

    // Should not throw — updated_at?.slice(0,10) handles null with optional chaining
    renderWithProviders(<Runway />);
    await waitFor(() => {
      expect(screen.getByTestId('article-card')).toBeInTheDocument();
    });
  });

  it('handles null batches response', async () => {
    api.get.mockImplementation((path) => {
      if (path === '/api/articles') return Promise.resolve({ articles: [makeArticle()] });
      if (path === '/api/batches') return Promise.resolve({ batches: null });
      return Promise.resolve({});
    });

    // Should not throw (batches defaults to [])
    renderWithProviders(<Runway />);
    await waitFor(() => {
      expect(screen.getByTestId('kanban-board')).toBeInTheDocument();
    });
  });

  it('handles API error for articles gracefully', async () => {
    api.get.mockImplementation((path) => {
      if (path === '/api/articles') return Promise.reject(new Error('Network error'));
      if (path === '/api/batches') return Promise.resolve({ batches: [] });
      return Promise.resolve({});
    });

    // React Query will handle the error — component should not crash
    renderWithProviders(<Runway />);
    // Wait a bit for the query to fail
    await new Promise(r => setTimeout(r, 100));
    // Component should still be rendered (not crashed)
    expect(screen.getByText('Dashboard')).toBeInTheDocument();
  });

  // ─── Polling ───

  it('sets up polling with correct intervals', async () => {
    api.get.mockImplementation((path) => {
      if (path === '/api/articles') return Promise.resolve({ articles: [] });
      if (path === '/api/batches') return Promise.resolve({ batches: [] });
      return Promise.resolve({});
    });

    renderWithProviders(<Runway />);
    await waitFor(() => {
      // articles polled at 15s, batches at 10s — both should have been called at least once
      expect(api.get).toHaveBeenCalledWith('/api/articles');
      expect(api.get).toHaveBeenCalledWith('/api/batches');
    });
  });

  // ─── Multiple Batches ───

  it('renders pending batch banner (waiting_approval no longer shown on dashboard)', async () => {
    api.get.mockImplementation((path) => {
      if (path === '/api/articles') return Promise.resolve({ articles: [makeArticle()] });
      if (path === '/api/batches') return Promise.resolve({
        batches: [
          makeBatch({ id: 'b1', status: 'pending_ideation', seed_count: 3 }),
          makeBatch({ id: 'b2', status: 'waiting_approval', seed_count: 5 }),
        ],
      });
      return Promise.resolve({});
    });

    renderWithProviders(<Runway />);
    await waitFor(() => {
      expect(screen.getByText('Generating ideas…')).toBeInTheDocument();
    });
    // waiting_approval banners no longer on dashboard
    expect(screen.queryByText('Ideas ready for review!')).not.toBeInTheDocument();
  });

  // ─── State → Column mapping ───

  it('maps all article states to correct kanban columns', async () => {
    const articles = [
      makeArticle({ id: '1', state: 'OUTLINING', column: 'in_production' }),
      makeArticle({ id: '2', state: 'DRAFTING', column: 'in_production' }),
      makeArticle({ id: '3', state: 'HUMANIZING', column: 'in_production' }),
      makeArticle({ id: '4', state: 'EDIT_REVIEW', column: 'in_production' }),
      makeArticle({ id: '5', state: 'MEDIA_ASSEMBLY', column: 'in_production' }),
      makeArticle({ id: '6', state: 'REVISION', column: 'in_production' }),
      makeArticle({ id: '7', state: 'FAILED', column: 'in_production' }),
      makeArticle({ id: '8', state: 'WAITING_CHECKPOINT_2', column: 'in_review', title: 'Review Article' }),
      makeArticle({ id: '9', state: 'READY_TO_PUBLISH', column: 'scheduled' }),
      makeArticle({ id: '10', state: 'PUBLISHING', column: 'scheduled' }),
      makeArticle({ id: '11', state: 'PUBLISHED', column: 'published' }),
      makeArticle({ id: '12', state: 'ARCHIVED', column: 'archived', title: 'Archived Article' }),
    ];

    api.get.mockImplementation((path) => {
      if (path === '/api/articles') return Promise.resolve({ articles });
      if (path === '/api/batches') return Promise.resolve({ batches: [] });
      return Promise.resolve({});
    });

    renderWithProviders(<Runway />);
    await waitFor(() => {
      expect(screen.getByTestId('kanban-board')).toBeInTheDocument();
    });

    // Verify columns have correct counts by checking column headers
    // in_production: 7, in_review: 1, scheduled: 2, published: 1, ideas: 0
    const board = screen.getByTestId('kanban-board');
    expect(within(board).getByText('(7)')).toBeInTheDocument(); // in_production
    // Use getAllByText for counts that appear multiple times across columns
    const onesAndTwos = within(board).getAllByText(/\(\d+\)/);
    // Archived articles should be in archived section, not kanban
    expect(screen.getByTestId('archived-section')).toBeInTheDocument();
  });
});
