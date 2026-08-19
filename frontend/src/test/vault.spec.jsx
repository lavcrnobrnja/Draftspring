import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

vi.mock('react-hot-toast', () => ({
  default: { error: vi.fn(), success: vi.fn() },
}));

import { Vault } from '../pages/Vault';

const mockFetch = vi.fn();
global.fetch = mockFetch;

const sampleGallery = {
  published: [
    {
      article_id: 'art-1',
      article_title: 'My Published Article',
      published_at: '2026-03-15T10:00:00Z',
      images: [
        { id: 'img-1', storage_url: '/images/hero.png', alt_text: 'Hero', source_type: 'generated', section_heading: 'Intro', created_at: '2026-03-15T09:00:00Z', width: 800, height: 600 },
      ],
    },
  ],
  in_progress: [
    {
      article_id: 'art-2',
      article_title: 'Draft Article',
      article_state: 'DRAFTING',
      article_state_label: 'Writing',
      images: [
        { id: 'img-2', storage_url: '/images/draft.png', alt_text: 'Draft img', source_type: 'seed', section_heading: null, created_at: '2026-03-16T09:00:00Z' },
      ],
    },
  ],
  available: [
    {
      group_type: 'seed',
      seed_id: 'seed-1',
      seed_content: 'AI in healthcare',
      seed_type: 'topic',
      batch_created_at: '2026-03-14T08:00:00Z',
      images: [
        { id: 'img-3', storage_url: '/images/seed.png', filename: 'seed.png', created_at: '2026-03-14T08:00:00Z' },
      ],
    },
    {
      group_type: 'vault',
      images: [
        { id: 'img-4', storage_url: '/images/pool.png', filename: 'pool.png', description: 'Stock photo', tags: '["stock"]', used_count: 0, created_at: '2026-03-13T08:00:00Z' },
      ],
    },
  ],
};

function renderVault(gallery = sampleGallery) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  mockFetch.mockResolvedValueOnce({
    ok: true, status: 200, json: () => Promise.resolve(gallery),
  });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <Vault />
      </MemoryRouter>
    </QueryClientProvider>
  );
}

describe('Vault Page (Gallery)', () => {
  beforeEach(() => vi.clearAllMocks());

  it('renders three tabs with correct labels', async () => {
    renderVault();
    await waitFor(() => expect(screen.getByText('Published')).toBeInTheDocument());
    expect(screen.getByText('In Progress')).toBeInTheDocument();
    expect(screen.getByText('Custom Images')).toBeInTheDocument();
  });

  it('shows published tab content by default', async () => {
    renderVault();
    await waitFor(() => expect(screen.getByText('My Published Article')).toBeInTheDocument());
  });

  it('shows empty state on published tab when no images', async () => {
    renderVault({ published: [], in_progress: [], available: [] });
    await waitFor(() => expect(screen.getByText(/no published articles with images/i)).toBeInTheDocument());
  });

  it('switches to in-progress tab and shows state badge', async () => {
    renderVault();
    await waitFor(() => expect(screen.getByText('Published')).toBeInTheDocument());
    fireEvent.click(screen.getByText('In Progress'));
    expect(screen.getByText('Draft Article')).toBeInTheDocument();
    expect(screen.getByText('Writing')).toBeInTheDocument();
  });

  it('switches to available tab and shows seed group', async () => {
    renderVault();
    await waitFor(() => expect(screen.getByText('Published')).toBeInTheDocument());
    fireEvent.click(screen.getByText('Custom Images'));
    expect(screen.getByText('AI in healthcare')).toBeInTheDocument();
  });

  it('shows vault pool group on available tab', async () => {
    renderVault();
    await waitFor(() => expect(screen.getByText('Published')).toBeInTheDocument());
    fireEvent.click(screen.getByText('Custom Images'));
    expect(screen.getByText('Vault Pool')).toBeInTheDocument();
  });

  it('hides other tab content when switching', async () => {
    renderVault();
    await waitFor(() => expect(screen.getByText('My Published Article')).toBeInTheDocument());
    fireEvent.click(screen.getByText('In Progress'));
    expect(screen.queryByText('My Published Article')).not.toBeInTheDocument();
    expect(screen.getByText('Draft Article')).toBeInTheDocument();
  });
});
