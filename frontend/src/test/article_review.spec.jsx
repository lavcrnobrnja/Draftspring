import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

vi.mock('react-hot-toast', () => ({
  default: { error: vi.fn(), success: vi.fn() },
}));

import { ArticleReview } from '../pages/ArticleReview';
import toast from 'react-hot-toast';

const mockFetch = vi.fn();
global.fetch = mockFetch;

const sampleData = {
  article_id: 'art-1',
  state: 'WAITING_CHECKPOINT_2',
  draft_html: '<h1>AI in Healthcare</h1><p>Article content here.</p>',
  images: [{ storage_url: '/img/1.webp', alt_text: 'AI diagram' }],
  seo: { focus_keyword: 'ai healthcare', meta_title: 'AI in Healthcare Guide', meta_description: 'Complete guide to AI in healthcare', secondary_keywords: ['medical ai', 'health tech'] },
  review_history: [{ status: 'revision_requested', revision_notes: 'Please add more examples' }],
  budget_remaining: 3,
};

function renderArticleReview(data = sampleData) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  mockFetch.mockResolvedValueOnce({ ok: true, status: 200, json: () => Promise.resolve(data) });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/review/article/art-1']}>
        <Routes><Route path="/review/article/:articleId" element={<ArticleReview />} /></Routes>
      </MemoryRouter>
    </QueryClientProvider>
  );
}

describe('ArticleReview Page', () => {
  beforeEach(() => vi.clearAllMocks());

  it('renders split screen with preview and actions', async () => {
    renderArticleReview();
    await waitFor(() => expect(screen.getByTestId('split-screen')).toBeInTheDocument());
    expect(screen.getByTestId('article-preview')).toBeInTheDocument();
    expect(screen.getByTestId('review-actions')).toBeInTheDocument();
  });

  it('shows article HTML content', async () => {
    renderArticleReview();
    await waitFor(() => expect(screen.getByText('Article content here.')).toBeInTheDocument());
  });

  it('shows images in preview', async () => {
    renderArticleReview();
    await waitFor(() => expect(screen.getByTestId('article-images')).toBeInTheDocument());
    expect(screen.getByAltText('AI diagram')).toBeInTheDocument();
  });

  it('toggles SEO info', async () => {
    const user = userEvent.setup();
    renderArticleReview();
    await waitFor(() => expect(screen.getByTestId('seo-toggle')).toBeInTheDocument());
    await user.click(screen.getByTestId('seo-toggle'));
    expect(screen.getByTestId('seo-info')).toBeInTheDocument();
    expect(screen.getByText('ai healthcare')).toBeInTheDocument();
  });

  it('approve button works', async () => {
    const user = userEvent.setup();
    renderArticleReview();
    await waitFor(() => expect(screen.getByTestId('approve-button')).toBeInTheDocument());
    mockFetch.mockResolvedValueOnce({ ok: true, status: 200, json: () => Promise.resolve({ ok: true }) })
      .mockResolvedValueOnce({ ok: true, status: 200, json: () => Promise.resolve(sampleData) });
    await user.click(screen.getByTestId('approve-button'));
    await waitFor(() => expect(mockFetch).toHaveBeenCalledWith(expect.stringContaining('/api/checkpoints/article/approve'), expect.objectContaining({ method: 'POST' })));
  });

  it('rejects short revision notes', async () => {
    const user = userEvent.setup();
    renderArticleReview();
    await waitFor(() => expect(screen.getByTestId('revision-textarea')).toBeInTheDocument());
    await user.type(screen.getByTestId('revision-textarea'), 'too short');
    await user.click(screen.getByTestId('revise-button'));
    expect(screen.getByTestId('revise-button')).toBeDisabled();
  });

  it('submits revision with valid notes', async () => {
    const user = userEvent.setup();
    renderArticleReview();
    await waitFor(() => expect(screen.getByTestId('revision-textarea')).toBeInTheDocument());
    await user.type(screen.getByTestId('revision-textarea'), 'Please add more detail about machine learning applications in diagnostics');
    mockFetch.mockResolvedValueOnce({ ok: true, status: 200, json: () => Promise.resolve({ ok: true }) })
      .mockResolvedValueOnce({ ok: true, status: 200, json: () => Promise.resolve(sampleData) });
    await user.click(screen.getByTestId('revise-button'));
    await waitFor(() => expect(mockFetch).toHaveBeenCalledWith(expect.stringContaining('/api/checkpoints/article/revise'), expect.objectContaining({ method: 'POST' })));
  });

  it('shows review history', async () => {
    renderArticleReview();
    await waitFor(() => expect(screen.getByTestId('review-history')).toBeInTheDocument());
    expect(screen.getByText('Please add more examples')).toBeInTheDocument();
  });

  it('shows budget indicator', async () => {
    renderArticleReview();
    await waitFor(() => expect(screen.getByTestId('budget-indicator')).toBeInTheDocument());
    expect(screen.getByTestId('budget-indicator')).toHaveTextContent('3 remaining');
  });
});
