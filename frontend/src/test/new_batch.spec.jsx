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

vi.mock('../lib/api', () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    del: vi.fn(),
    upload: vi.fn(),
  },
}));

vi.mock('../lib/posthog', () => ({
  trackEvent: vi.fn(),
}));

const navigateSpy = vi.fn();
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom');
  return {
    ...actual,
    useNavigate: () => navigateSpy,
  };
});

import { NewBatch } from '../pages/NewBatch';
import { useAuth } from '../hooks/useAuth';
import { api } from '../lib/api';
import toast from 'react-hot-toast';

function renderNewBatch(userOverrides = {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  useAuth.mockReturnValue({
    user: { email: 'test@example.com', ghost_key_valid: 1, subscription_status: 'active', articles_remaining: 6, ...userOverrides },
    loading: false, logout: vi.fn(), refetchUser: vi.fn(),
  });
  return render(<QueryClientProvider client={qc}><MemoryRouter><NewBatch /></MemoryRouter></QueryClientProvider>);
}

describe('NewBatch Page', () => {
  beforeEach(() => vi.clearAllMocks());

  it('renders content brief form with description, keywords, urls, and submit', () => {
    renderNewBatch();
    expect(screen.getByTestId('description-input')).toBeInTheDocument();
    expect(screen.getByTestId('keywords-input')).toBeInTheDocument();
    expect(screen.getByTestId('url-input')).toBeInTheDocument();
    expect(screen.getByTestId('submit-seeds')).toBeInTheDocument();
  });

  it('adds and removes reference URL fields', async () => {
    const user = userEvent.setup();
    renderNewBatch();
    // Starts with 1 URL field
    expect(screen.getAllByTestId('url-input').length).toBe(1);
    // Add another
    await user.click(screen.getByText('+ Add another'));
    expect(screen.getAllByTestId('url-input').length).toBe(2);
    // Remove one
    const removeButtons = screen.getAllByLabelText(/remove url/i);
    await user.click(removeButtons[0]);
    expect(screen.getAllByTestId('url-input').length).toBe(1);
  });

  it('shows ghost warning when not connected', () => {
    renderNewBatch({ ghost_key_valid: 0 });
    expect(screen.getByTestId('ghost-warning')).toBeInTheDocument();
    expect(screen.getByTestId('submit-seeds')).toBeDisabled();
  });

  it('submits content brief successfully', async () => {
    const user = userEvent.setup();
    renderNewBatch();
    await user.type(screen.getByTestId('description-input'), 'AI in healthcare trends for 2026');
    api.post.mockResolvedValueOnce({ seed_ids: ['s1'] });

    await user.click(screen.getByTestId('submit-seeds'));
    await waitFor(() => {
      expect(api.post).toHaveBeenCalledWith('/api/seeds', expect.objectContaining({
        description: 'AI in healthcare trends for 2026',
      }));
    });
  });

  it('submits optional image style override from content brief', async () => {
    const user = userEvent.setup();
    renderNewBatch();
    await user.type(screen.getByTestId('description-input'), 'AI in healthcare trends for 2026');
    await user.click(screen.getByTestId('brief-image-style-override'));
    await user.selectOptions(screen.getByTestId('brief-image-style-select'), 'illustration');
    await user.selectOptions(screen.getByTestId('brief-image-substyle-select'), 'isometric');
    api.post.mockResolvedValueOnce({ seed_ids: ['s1'] });

    await user.click(screen.getByTestId('submit-seeds'));
    await waitFor(() => {
      expect(api.post).toHaveBeenCalledWith('/api/seeds', expect.objectContaining({
        description: 'AI in healthcare trends for 2026',
        image_style: 'illustration',
        image_substyle: 'isometric',
      }));
    });
  });

  it('validates empty description', async () => {
    const user = userEvent.setup();
    renderNewBatch();
    await user.click(screen.getByTestId('submit-seeds'));
    expect(toast.error).toHaveBeenCalledWith('Description is required');
  });
});

describe('Blog Analysis Tab (simplified)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    navigateSpy.mockReset();
  });

  const openAnalysisTab = async (user) => {
    await user.click(screen.getByRole('button', { name: /blog analysis/i }));
  };

  const makeProfile = (overrides = {}) => ({
    id: 'prof-123',
    url: 'https://example.com',
    site_name: 'Example',
    is_ghost: true,
    topics: ['ai'],
    content_gaps: [],
    style_guide: '',
    example_sentences: [],
    audience_description: '',
    tone_keywords: [],
    strengths: [],
    avg_word_count: 0,
    total_posts: 0,
    latest_post_date: '',
    publishing_frequency: '',
    post_summaries: [],
    analyzed_at: '',
    ...overrides,
  });

  const makeIdeas = (n) =>
    Array.from({ length: n }, (_, i) => ({
      title: `Idea ${i + 1}`,
      angle: `Angle ${i + 1}`,
      article_type: 'how-to',
      reasoning: '',
    }));

  it('clicking Blog Analysis tab shows URL input', async () => {
    const user = userEvent.setup();
    renderNewBatch();
    await openAnalysisTab(user);
    expect(screen.getByTestId('analysis-url-input')).toBeInTheDocument();
    expect(screen.getByTestId('analyze-button')).toBeInTheDocument();
  });

  // Stage delays inside BlogAnalysisTab are skipped in test env (MODE==='test'),
  // so these flows complete in microtasks. Keep a small safety timeout.
  const FLOW_TIMEOUT = 2000;

  it('submits URL, runs analyze then generate-ideas back-to-back, renders ideas', async () => {
    const user = userEvent.setup();
    renderNewBatch();
    await openAnalysisTab(user);

    api.post.mockImplementation(async (path) => {
      if (path === '/api/blog-analysis/analyze') return { profile: makeProfile() };
      if (path === '/api/blog-analysis/generate-ideas') return { ideas: makeIdeas(10) };
      throw new Error(`unexpected ${path}`);
    });

    await user.type(screen.getByTestId('analysis-url-input'), 'https://example.com');
    await user.click(screen.getByTestId('analyze-button'));

    await waitFor(
      () => expect(screen.getByTestId('submit-analysis-ideas')).toBeInTheDocument(),
      { timeout: FLOW_TIMEOUT },
    );

    expect(api.post).toHaveBeenCalledWith('/api/blog-analysis/analyze', { url: 'https://example.com' });
    expect(api.post).toHaveBeenCalledWith('/api/blog-analysis/generate-ideas', {
      profile_id: 'prof-123',
      count: 10,
    });
    expect(screen.getByText('Idea 1')).toBeInTheDocument();
    expect(screen.getByText(/0 of 3 selected/)).toBeInTheDocument();
  }, FLOW_TIMEOUT);

  it('blocks selecting a 4th idea and toasts error', async () => {
    const user = userEvent.setup();
    renderNewBatch();
    await openAnalysisTab(user);

    api.post.mockImplementation(async (path) => {
      if (path === '/api/blog-analysis/analyze') return { profile: makeProfile() };
      if (path === '/api/blog-analysis/generate-ideas') return { ideas: makeIdeas(5) };
      throw new Error(`unexpected ${path}`);
    });

    await user.type(screen.getByTestId('analysis-url-input'), 'https://example.com');
    await user.click(screen.getByTestId('analyze-button'));

    await waitFor(
      () => expect(screen.getByTestId('submit-analysis-ideas')).toBeInTheDocument(),
      { timeout: FLOW_TIMEOUT },
    );

    const checkboxes = screen.getAllByRole('checkbox');
    expect(checkboxes.length).toBe(5);

    await user.click(checkboxes[0]);
    await user.click(checkboxes[1]);
    await user.click(checkboxes[2]);
    expect(screen.getByText(/3 of 3 selected/)).toBeInTheDocument();

    await user.click(checkboxes[3]);
    expect(toast.error).toHaveBeenCalledWith(expect.stringMatching(/up to 3/));
    expect(screen.getByText(/3 of 3 selected/)).toBeInTheDocument();
  }, FLOW_TIMEOUT);


  it('uses standard api error handling with longer duration and stays on blog analysis screen on cap 409', async () => {
    const user = userEvent.setup();
    renderNewBatch();
    await openAnalysisTab(user);

    api.post.mockImplementation(async (path) => {
      if (path === '/api/blog-analysis/analyze') return { profile: makeProfile() };
      if (path === '/api/blog-analysis/generate-ideas') return { ideas: makeIdeas(2) };
      if (path === '/api/seeds/from-analysis') throw new Error('You have 0 articles remaining this cycle. No articles were created.');
      throw new Error(`unexpected ${path}`);
    });

    await user.type(screen.getByTestId('analysis-url-input'), 'https://example.com');
    await user.click(screen.getByTestId('analyze-button'));

    await waitFor(() => expect(screen.getByTestId('submit-analysis-ideas')).toBeInTheDocument(), { timeout: FLOW_TIMEOUT });
    await user.click(screen.getAllByRole('checkbox')[0]);
    await user.click(screen.getByTestId('submit-analysis-ideas'));

    await waitFor(() => expect(api.post).toHaveBeenCalledWith(
      '/api/seeds/from-analysis',
      expect.any(Object),
      { errorToastOptions: { duration: 12000 } },
    ));
    expect(toast.error).not.toHaveBeenCalledWith(expect.stringMatching(/article limit/i));
    expect(navigateSpy).not.toHaveBeenCalledWith('/dashboard');
    expect(screen.getByTestId('submit-analysis-ideas')).toBeInTheDocument();
  }, FLOW_TIMEOUT);


  it('shows a long fallback error if from-analysis returns 200 with zero articles', async () => {
    const user = userEvent.setup();
    renderNewBatch();
    await openAnalysisTab(user);

    api.post.mockImplementation(async (path) => {
      if (path === '/api/blog-analysis/analyze') return { profile: makeProfile() };
      if (path === '/api/blog-analysis/generate-ideas') return { ideas: makeIdeas(2) };
      if (path === '/api/seeds/from-analysis') return { batch_id: 'batch-xyz', articles_created: 0, budget_limited: true, seed_count: 2 };
      throw new Error(`unexpected ${path}`);
    });

    await user.type(screen.getByTestId('analysis-url-input'), 'https://example.com');
    await user.click(screen.getByTestId('analyze-button'));
    await waitFor(() => expect(screen.getByTestId('submit-analysis-ideas')).toBeInTheDocument(), { timeout: FLOW_TIMEOUT });

    await user.click(screen.getAllByRole('checkbox')[0]);
    await user.click(screen.getByTestId('submit-analysis-ideas'));

    await waitFor(() => expect(toast.error).toHaveBeenCalledWith(
      'No articles were created. Please try again or contact support.',
      { duration: 12000 },
    ));
    expect(navigateSpy).not.toHaveBeenCalledWith('/dashboard');
  }, FLOW_TIMEOUT);

  it('clicking Generate calls from-analysis with selected ideas and navigates to /dashboard', async () => {
    const user = userEvent.setup();
    renderNewBatch();
    await openAnalysisTab(user);

    api.post.mockImplementation(async (path) => {
      if (path === '/api/blog-analysis/analyze') return { profile: makeProfile() };
      if (path === '/api/blog-analysis/generate-ideas') return { ideas: makeIdeas(4) };
      if (path === '/api/seeds/from-analysis') {
        return { batch_id: 'batch-xyz', articles_created: 2, budget_limited: false, seed_count: 2 };
      }
      throw new Error(`unexpected ${path}`);
    });

    await user.type(screen.getByTestId('analysis-url-input'), 'https://example.com');
    await user.click(screen.getByTestId('analyze-button'));

    await waitFor(
      () => expect(screen.getByTestId('submit-analysis-ideas')).toBeInTheDocument(),
      { timeout: FLOW_TIMEOUT },
    );

    const checkboxes = screen.getAllByRole('checkbox');
    await user.click(checkboxes[0]);
    await user.click(checkboxes[1]);

    await user.click(screen.getByTestId('submit-analysis-ideas'));

    await waitFor(() => {
      expect(api.post).toHaveBeenCalledWith('/api/seeds/from-analysis', expect.objectContaining({
        profile_id: 'prof-123',
        ideas: expect.arrayContaining([
          expect.objectContaining({ title: 'Idea 1' }),
          expect.objectContaining({ title: 'Idea 2' }),
        ]),
      }), { errorToastOptions: { duration: 12000 } });
    });

    await waitFor(() => expect(navigateSpy).toHaveBeenCalledWith('/dashboard'));
  }, FLOW_TIMEOUT);
});
