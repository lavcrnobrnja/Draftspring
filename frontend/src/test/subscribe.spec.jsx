import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

// These must be declared before vi.mock (hoisted)
const mockUser = { email: 'test@example.com', subscription_status: 'none' };
const mockNavigate = vi.fn();

vi.mock('../hooks/useAuth', () => ({
  useAuth: () => ({ user: mockUser, isLoading: false, isAuthenticated: true }),
}));

vi.mock('../lib/api', () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
  },
}));

vi.mock('../lib/posthog', () => ({
  trackEvent: vi.fn(),
}));

vi.mock('react-hot-toast', () => ({
  default: { error: vi.fn(), success: vi.fn() },
}));

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom');
  return { ...actual, useNavigate: () => mockNavigate };
});

// Import after mocks
import { Subscribe } from '../pages/Subscribe';
import { RequireSubscription } from '../components/RequireSubscription';
import { api } from '../lib/api';

function renderWithProviders(ui, { route = '/' } = {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[route]}>{ui}</MemoryRouter>
    </QueryClientProvider>
  );
}

describe('Subscribe Page', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockUser.subscription_status = 'none';
    delete window.location;
    window.location = { href: '', search: '' };
  });

  it('renders subscribe page with pricing info', () => {
    renderWithProviders(<Subscribe />);
    expect(screen.getByTestId('subscribe-card')).toBeInTheDocument();
    expect(screen.getByText('$9')).toBeInTheDocument();
    expect(screen.getByText('/month')).toBeInTheDocument();
    expect(screen.getByText('7-day free trial')).toBeInTheDocument();
  });

  it('shows user email in welcome message', () => {
    renderWithProviders(<Subscribe />);
    expect(screen.getByText(/test@example\.com/)).toBeInTheDocument();
  });

  it('displays all feature items', () => {
    renderWithProviders(<Subscribe />);
    expect(screen.getByText('8 articles per billing cycle')).toBeInTheDocument();
    expect(screen.getByText('AI research, writing & editing')).toBeInTheDocument();
    expect(screen.getByText('Auto-generated images')).toBeInTheDocument();
    expect(screen.getByText('SEO optimization built in')).toBeInTheDocument();
    expect(screen.getByText('Two human approval checkpoints')).toBeInTheDocument();
    expect(screen.getByText('Direct Ghost publishing')).toBeInTheDocument();
  });

  it('has a Start Free Trial button (disabled until TOS accepted)', () => {
    renderWithProviders(<Subscribe />);
    const btn = screen.getByTestId('start-trial-btn');
    expect(btn).toBeInTheDocument();
    expect(btn).toHaveTextContent('Start Free Trial →');
    // Button is disabled until TOS checkbox is checked
    expect(btn).toBeDisabled();
  });

  it('calls checkout API when Start Free Trial clicked after accepting TOS', async () => {
    api.get.mockResolvedValueOnce({ url: 'https://checkout.stripe.com/test' });
    renderWithProviders(<Subscribe />);

    // Must accept TOS first
    const tosCheckbox = screen.getByTestId('tos-checkbox');
    await userEvent.click(tosCheckbox);

    const btn = screen.getByTestId('start-trial-btn');
    expect(btn).not.toBeDisabled();
    await userEvent.click(btn);

    await waitFor(() => {
      expect(api.get).toHaveBeenCalledWith('/api/checkout/session');
    });
    expect(window.location.href).toBe('https://checkout.stripe.com/test');
  });

  it('shows error on checkout failure and re-enables button', async () => {
    api.get.mockRejectedValueOnce(new Error('fail'));
    renderWithProviders(<Subscribe />);

    // Accept TOS
    await userEvent.click(screen.getByTestId('tos-checkbox'));

    const btn = screen.getByTestId('start-trial-btn');
    await userEvent.click(btn);

    await waitFor(() => {
      expect(api.get).toHaveBeenCalledWith('/api/checkout/session');
    });
    // Button should re-enable after failure
    await waitFor(() => {
      expect(btn).not.toBeDisabled();
    });
  });

  it('redirects to dashboard if already subscribed (active)', () => {
    mockUser.subscription_status = 'active';
    renderWithProviders(<Subscribe />);
    expect(mockNavigate).toHaveBeenCalledWith('/dashboard', { replace: true });
  });

  it('redirects to dashboard if already subscribed (trialing)', () => {
    mockUser.subscription_status = 'trialing';
    renderWithProviders(<Subscribe />);
    expect(mockNavigate).toHaveBeenCalledWith('/dashboard', { replace: true });
  });

  it('does NOT redirect for canceled status', () => {
    mockUser.subscription_status = 'canceled';
    renderWithProviders(<Subscribe />);
    expect(mockNavigate).not.toHaveBeenCalledWith('/dashboard', expect.anything());
  });

  it('shows no-charge disclaimer', () => {
    renderWithProviders(<Subscribe />);
    expect(screen.getByText(/No charge until your trial ends/)).toBeInTheDocument();
  });

  it('shows Stripe branding', () => {
    renderWithProviders(<Subscribe />);
    expect(screen.getByText(/Powered by Stripe/)).toBeInTheDocument();
  });
});

describe('RequireSubscription', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders children when subscription is active', () => {
    mockUser.subscription_status = 'active';
    renderWithProviders(
      <RequireSubscription><div data-testid="protected">Content</div></RequireSubscription>
    );
    expect(screen.getByTestId('protected')).toBeInTheDocument();
  });

  it('renders children when subscription is trialing', () => {
    mockUser.subscription_status = 'trialing';
    renderWithProviders(
      <RequireSubscription><div data-testid="protected">Content</div></RequireSubscription>
    );
    expect(screen.getByTestId('protected')).toBeInTheDocument();
  });

  it('redirects to /subscribe when status is none', () => {
    mockUser.subscription_status = 'none';
    renderWithProviders(
      <RequireSubscription><div data-testid="protected">Content</div></RequireSubscription>
    );
    expect(screen.queryByTestId('protected')).not.toBeInTheDocument();
  });

  it('redirects to /subscribe when status is canceled', () => {
    mockUser.subscription_status = 'canceled';
    renderWithProviders(
      <RequireSubscription><div data-testid="protected">Content</div></RequireSubscription>
    );
    expect(screen.queryByTestId('protected')).not.toBeInTheDocument();
  });

  it('redirects to /subscribe when status is past_due', () => {
    mockUser.subscription_status = 'past_due';
    renderWithProviders(
      <RequireSubscription><div data-testid="protected">Content</div></RequireSubscription>
    );
    expect(screen.queryByTestId('protected')).not.toBeInTheDocument();
  });
});
