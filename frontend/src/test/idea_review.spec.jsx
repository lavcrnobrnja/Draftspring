import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

vi.mock('react-hot-toast', () => ({
  default: { error: vi.fn(), success: vi.fn() },
}));

import { IdeaReview } from '../pages/IdeaReview';
import toast from 'react-hot-toast';

const mockFetch = vi.fn();
global.fetch = mockFetch;

const sampleIdeas = [
  { id: 'idea-1', title: 'AI Healthcare Revolution', angle: 'How AI is transforming diagnostics', target_keyword: 'ai healthcare', status: 'pending' },
  { id: 'idea-2', title: 'Cloud Computing 2026', angle: 'Key trends in cloud infrastructure', target_keyword: 'cloud computing', status: 'pending' },
  { id: 'idea-3', title: 'Remote Work Productivity', angle: 'Best practices for remote teams', target_keyword: 'remote work', status: 'pending' },
];

function renderIdeaReview(ideasResponse = { batch_id: 'batch-1', status: 'waiting_approval', read_only: false, ideas: sampleIdeas }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  mockFetch.mockResolvedValueOnce({ ok: true, status: 200, json: () => Promise.resolve(ideasResponse) });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/review/ideas/batch-1']}>
        <Routes>
          <Route path="/review/ideas/:batchId" element={<IdeaReview />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  );
}

describe('IdeaReview Page', () => {
  beforeEach(() => vi.clearAllMocks());

  it('renders idea cards', async () => {
    renderIdeaReview();
    await waitFor(() => expect(screen.getByTestId('idea-list')).toBeInTheDocument());
    expect(screen.getAllByTestId('idea-card').length).toBe(3);
    expect(screen.getByDisplayValue('AI Healthcare Revolution')).toBeInTheDocument();
  });

  it('allows selecting ideas', async () => {
    const user = userEvent.setup();
    renderIdeaReview();
    await waitFor(() => expect(screen.getByTestId('idea-list')).toBeInTheDocument());

    const cards = screen.getAllByTestId('idea-card');
    await user.click(cards[0]);
    await user.click(cards[1]);

    await waitFor(() => expect(screen.getByTestId('budget-info')).toHaveTextContent('2 selected'));
  });

  it('allows editing titles', async () => {
    const user = userEvent.setup();
    renderIdeaReview();
    await waitFor(() => expect(screen.getByTestId('idea-list')).toBeInTheDocument());

    const titleInputs = screen.getAllByTestId('idea-title-input');
    await user.clear(titleInputs[0]);
    await user.type(titleInputs[0], 'New Title Here');

    expect(titleInputs[0]).toHaveValue('New Title Here');
  });

  it('approves selected ideas', async () => {
    const user = userEvent.setup();
    renderIdeaReview();
    await waitFor(() => expect(screen.getByTestId('idea-list')).toBeInTheDocument());

    await user.click(screen.getAllByTestId('idea-card')[0]);

    mockFetch.mockResolvedValueOnce({ ok: true, status: 200, json: () => Promise.resolve({ articles_created: 1 }) });

    await user.click(screen.getByTestId('approve-button'));

    await waitFor(() => {
      expect(mockFetch).toHaveBeenCalledWith(
        expect.stringContaining('/api/checkpoints/ideas/approve'),
        expect.objectContaining({ method: 'POST' })
      );
    });
  });

  it('uses standard api error handling with longer duration on cap 409', async () => {
    const user = userEvent.setup();
    renderIdeaReview();
    await waitFor(() => expect(screen.getByTestId('idea-list')).toBeInTheDocument());

    await user.click(screen.getAllByTestId('idea-card')[0]);

    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 409,
      json: () => Promise.resolve({ detail: 'You have 0 articles remaining this cycle. No articles were created.' }),
    });

    await user.click(screen.getByTestId('approve-button'));

    await waitFor(() => expect(toast.error).toHaveBeenCalledWith(
      'You have 0 articles remaining this cycle. No articles were created.',
      { duration: 12000 },
    ));
    expect(toast.success).not.toHaveBeenCalled();
  });


  it('does not navigate or show success when approval creates zero articles', async () => {
    const user = userEvent.setup();
    renderIdeaReview();
    await waitFor(() => expect(screen.getByTestId('idea-list')).toBeInTheDocument());

    await user.click(screen.getAllByTestId('idea-card')[0]);

    mockFetch.mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: () => Promise.resolve({ articles_created: 0, budget_limited: true }),
    });

    await user.click(screen.getByTestId('approve-button'));

    await waitFor(() => expect(mockFetch).toHaveBeenCalledWith(
      expect.stringContaining('/api/checkpoints/ideas/approve'),
      expect.objectContaining({ method: 'POST' }),
    ));
    expect(toast.error).toHaveBeenCalledWith(
      'No articles were created. Please try again or contact support.',
      { duration: 12000 },
    );
    expect(toast.success).not.toHaveBeenCalled();
  });

  it('shows expired state', async () => {
    renderIdeaReview({ batch_id: 'batch-1', status: 'expired', read_only: true, ideas: sampleIdeas });
    await waitFor(() => expect(screen.getByTestId('expired-state')).toBeInTheDocument());
    expect(screen.getByText(/ideas have expired/i)).toBeInTheDocument();
  });

  it('shows processed state', async () => {
    renderIdeaReview({ batch_id: 'batch-1', status: 'processed', read_only: true, ideas: sampleIdeas });
    await waitFor(() => expect(screen.getByTestId('processed-state')).toBeInTheDocument());
  });

  it('prevents approving with no selection', async () => {
    const user = userEvent.setup();
    renderIdeaReview();
    await waitFor(() => expect(screen.getByTestId('approve-button')).toBeInTheDocument());
    expect(screen.getByTestId('approve-button')).toBeDisabled();
  });
});
