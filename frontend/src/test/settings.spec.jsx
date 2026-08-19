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
  },
}));

vi.mock('../lib/posthog', () => ({
  trackEvent: vi.fn(),
}));

import { Settings } from '../pages/Settings';
import { useAuth } from '../hooks/useAuth';
import { api } from '../lib/api';
import toast from 'react-hot-toast';

function renderSettings(userOverrides = {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  useAuth.mockReturnValue({
    user: {
      email: 'test@example.com',
      ghost_key_valid: 0,
      ghost_url: '',
      ghost_site_title: null,
      ghost_version: null,
      publish_days: '["monday","thursday"]',
      publish_time: '09:00',
      publish_timezone: 'America/New_York',
      brand_voice: '',
      default_word_count: 1500,
      subscription_status: 'active',
      ...userOverrides,
    },
    loading: false,
  });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <Settings />
      </MemoryRouter>
    </QueryClientProvider>
  );
}

describe('Settings Page', () => {
  beforeEach(() => vi.clearAllMocks());

  it('renders four tabs (profile, ghost, schedule, billing)', () => {
    renderSettings();
    expect(screen.getByText('profile')).toBeInTheDocument();
    expect(screen.getByText('ghost')).toBeInTheDocument();
    expect(screen.getByText('schedule')).toBeInTheDocument();
    expect(screen.getByText('billing')).toBeInTheDocument();
  });

  it('shows Profile tab by default with brand voice and word count', () => {
    renderSettings();
    // Profile is the default tab — should show email field and brand voice
    expect(screen.getByDisplayValue('test@example.com')).toBeInTheDocument();
    expect(screen.getByText('Brand voice')).toBeInTheDocument();
    expect(screen.getByText('Word count')).toBeInTheDocument();
    expect(screen.getByText('Default image style')).toBeInTheDocument();
  });

  it('shows Ghost connection form when Ghost tab is clicked', async () => {
    const user = userEvent.setup();
    renderSettings();
    await user.click(screen.getByText('ghost'));
    expect(screen.getByPlaceholderText('https://your-blog.ghost.io')).toBeInTheDocument();
    expect(screen.getByPlaceholderText('Ghost Admin API key')).toBeInTheDocument();
  });

  it('shows connected state when ghost is valid', async () => {
    const user = userEvent.setup();
    renderSettings({ ghost_key_valid: 1, ghost_site_title: 'My Blog', ghost_version: '5.0' });
    await user.click(screen.getByText('ghost'));
    expect(screen.getByText('Connected')).toBeInTheDocument();
  });

  it('submits Ghost connection', async () => {
    const user = userEvent.setup();
    renderSettings();
    await user.click(screen.getByText('ghost'));

    await user.type(screen.getByPlaceholderText('https://your-blog.ghost.io'), 'https://myblog.ghost.io');
    await user.type(screen.getByPlaceholderText('Ghost Admin API key'), 'abc123:def456');

    api.put.mockResolvedValueOnce({ site_title: 'My Blog', valid: true });

    await user.click(screen.getByText('Connect Ghost'));

    await waitFor(() => {
      expect(api.put).toHaveBeenCalledWith('/api/settings/ghost', expect.objectContaining({
        ghost_url: 'https://myblog.ghost.io',
        ghost_admin_api_key: 'abc123:def456',
      }));
    });
  });

  it('shows schedule tab with day buttons', async () => {
    const user = userEvent.setup();
    renderSettings();
    await user.click(screen.getByText('schedule'));

    expect(screen.getByText('Mon')).toBeInTheDocument();
    expect(screen.getByText('Tue')).toBeInTheDocument();
    expect(screen.getByText('Sun')).toBeInTheDocument();
  });

  it('enforces max 2 days in schedule', async () => {
    const user = userEvent.setup();
    renderSettings({ publish_days: '["monday","wednesday"]' });
    await user.click(screen.getByText('schedule'));

    await user.click(screen.getByText('Tue'));
    expect(toast.error).toHaveBeenCalledWith('Max 2 days');
  });

  it('saves schedule', async () => {
    const user = userEvent.setup();
    renderSettings();
    await user.click(screen.getByText('schedule'));

    api.put.mockResolvedValueOnce({ ok: true });

    await user.click(screen.getByText('Save schedule'));

    await waitFor(() => {
      expect(api.put).toHaveBeenCalledWith('/api/settings/schedule', expect.objectContaining({
        publish_days: expect.any(String),
        publish_time: expect.any(String),
        publish_timezone: expect.any(String),
      }));
    });
  });

  it('shows profile tab with brand voice', async () => {
    renderSettings({ brand_voice: 'Professional and friendly' });
    // Profile is default tab
    expect(screen.getByDisplayValue('Professional and friendly')).toBeInTheDocument();
  });

  it('saves profile image style and sub-style', async () => {
    const user = userEvent.setup();
    renderSettings();
    await user.selectOptions(screen.getByTestId('image-style-select'), 'illustration');
    await user.selectOptions(screen.getByTestId('image-substyle-select'), 'isometric');

    api.put.mockResolvedValueOnce({ ok: true });
    await user.click(screen.getByText('Save profile'));

    await waitFor(() => {
      expect(api.put).toHaveBeenCalledWith('/api/settings/profile', expect.objectContaining({
        image_style: 'illustration',
        image_substyle: 'isometric',
      }));
    });
  });
});
