import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('react-hot-toast', () => ({
  default: { error: vi.fn() },
}));

vi.mock('../lib/posthog', () => ({
  captureError: vi.fn(),
}));

import toast from 'react-hot-toast';
import { api } from '../lib/api';

describe('api client error handling', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    global.fetch = vi.fn();
  });

  it('shows backend detail through standard handler with per-request toast options', async () => {
    global.fetch.mockResolvedValueOnce({
      ok: false,
      status: 409,
      json: () => Promise.resolve({ detail: 'You have 0 articles remaining this cycle. No articles were created.' }),
    });

    await expect(
      api.post('/api/checkpoints/ideas/approve', { batch_id: 'batch-1', approved_ideas: [] }, { errorToastOptions: { duration: 12000 } }),
    ).rejects.toMatchObject({ status: 409 });

    expect(toast.error).toHaveBeenCalledWith(
      'You have 0 articles remaining this cycle. No articles were created.',
      { duration: 12000 },
    );
  });

  it('does not pass internal api options through to fetch', async () => {
    global.fetch.mockResolvedValueOnce({ ok: true, status: 204 });

    await api.post('/api/test', { ok: true }, { errorToastOptions: { duration: 12000 } });

    expect(global.fetch).toHaveBeenCalledWith(
      '/api/test',
      expect.not.objectContaining({ errorToastOptions: expect.anything() }),
    );
  });
});
